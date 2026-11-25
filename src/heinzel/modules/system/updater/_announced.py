"""What an automatic update needs around `uv tool install` (ADR 0116): a copy to go back to, and a trial start.

The copy is of the whole uv tool environment, taken before anything is installed, on the same file system. Putting it
back moves each entry of the copy into the tool folder. If uv recreated that directory, the old startup record no
longer matches its identity. For a previously verified installation, the content fingerprint captured before the
update proves the put-back restored its files, and continuity can be recorded for the new directory (ADR 0108).

The trial start runs the newly installed `hz ui serve` on throwaway folders before anything restarts into it.
"""

import contextlib
import importlib.metadata
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import cast

from packaging.requirements import InvalidRequirement, Requirement

from heinzel._adapter_system import SYSTEM_RUNNER
from heinzel.kernel import HeinzelError, Installation, private_read, private_write, user_config_dir
from heinzel.ports import ProcessRunner

MOVED_ASIDE = ".everything-the-update-left-is-here"
"""Written once every entry the update left is aside: from then on, only the copy moves."""
KEPT = frozenset({".heinzel-startup-state", ".heinzel-startup-pending"})
"""Continuity evidence in the tool folder (ADR 0108): never copied, moved or put back."""


def installed_commit(prefix: Path) -> str | None:
    """The commit hz is installed from in the tool folder, as its `direct_url.json` says; None for an index or a wheel.

    Read from the metadata on disk; nothing installed runs.
    """
    paths = [str(path) for path in prefix.glob("lib/python*/site-packages")]
    for distribution in importlib.metadata.distributions(path=paths, name="heinzel"):
        try:
            text = distribution.read_text("direct_url.json")
            data: object = json.loads(text) if text else None
        except (OSError, ValueError):
            return None
        vcs: object = cast("dict[str, object]", data).get("vcs_info") if isinstance(data, dict) else None
        commit: object = cast("dict[str, object]", vcs).get("commit_id") if isinstance(vcs, dict) else None
        return commit if isinstance(commit, str) else None
    return None


def environment_versions(prefix: Path) -> dict[str, str]:
    """Every distribution installed in the tool folder with its version, by canonical name, from metadata on disk.

    What automatic updates hold where it is: everything but the announced versions (ADR 0116).
    """
    paths = [str(path) for path in prefix.glob("lib/python*/site-packages")]
    found: dict[str, str] = {}
    for distribution in importlib.metadata.distributions(path=paths):
        name = distribution.metadata["Name"]
        if name:
            found[re.sub(r"[-_.]+", "-", name).lower()] = distribution.version
    return dict(sorted(found.items()))


def _distribution(prefix: Path, name: str) -> importlib.metadata.Distribution | None:
    paths = [str(path) for path in prefix.glob("lib/python*/site-packages")]
    for distribution in importlib.metadata.distributions(path=paths):
        if re.sub(r"[-_.]+", "-", distribution.metadata["Name"] or "").lower() == name:
            return distribution
    return None


def installed_identity(prefix: Path, name: str) -> tuple[str, str, str] | None:
    """What a plugin is, as installed: its version, its files' hashes (`RECORD`) and its source (`direct_url.json`).

    Equal before and after an update means not one of its files changed.
    """
    distribution = _distribution(prefix, name)
    if distribution is None:
        return None
    return (
        distribution.version,
        distribution.read_text("RECORD") or "",
        distribution.read_text("direct_url.json") or "",
    )


def plugin_commit(prefix: Path, name: str) -> str | None:
    """The commit a plugin from git is installed from, as its `direct_url.json` says; None if it doesn't say."""
    distribution = _distribution(prefix, name)
    text = distribution.read_text("direct_url.json") if distribution is not None else None
    try:
        data: object = json.loads(text) if text else None
    except ValueError:
        return None
    vcs: object = cast("dict[str, object]", data).get("vcs_info") if isinstance(data, dict) else None
    commit: object = cast("dict[str, object]", vcs).get("commit_id") if isinstance(vcs, dict) else None
    return commit if isinstance(commit, str) else None


def plugin_requires(prefix: Path, name: str) -> list[str]:
    """What the installed plugin `name` requires (`Requires-Dist`), read from its metadata; nothing installed runs."""
    distribution = _distribution(prefix, name)
    return list(distribution.requires or []) if distribution is not None else []


def allows_core(prefix: Path, name: str, version: str) -> bool:
    """Whether the installed plugin `name` declares it works with hz `version` (its `Requires-Dist: heinzel...`)."""
    distribution = _distribution(prefix, name)
    for line in (distribution.requires or []) if distribution is not None else []:
        try:
            requirement = Requirement(line)
        except InvalidRequirement:
            continue
        if requirement.name.lower() == "heinzel" and requirement.marker is None:
            return requirement.specifier.contains(version, prereleases=True)
    return True


@dataclass(frozen=True)
class Announcement:
    """What automatic updates announced and may install (ADR 0116): exact versions, and how old what resolves may be."""

    core: str | None
    """The announced hz version; None to keep the running one."""
    plugins: Mapping[str, str]
    """Announced plugins, by canonical name, mapped to their version: from PyPI, or tagged on GitHub (ADR 0137)."""
    commit: str | None = None
    """The commit hz's release tag named when it was announced."""
    plugin_commits: Mapping[str, str] = field(default_factory=dict[str, str])
    """For announced plugins pinned to a GitHub commit: the commit their release tag named when it was announced."""
    exclude_newer: datetime | None = None
    """Nothing uploaded after this resolves, unless pinned: the plugins' minimum age, for every package."""
    exclude_newer_core: datetime | None = None
    """The same for hz itself: hz's minimum age."""


@dataclass(frozen=True)
class ToolSnapshot:
    """The tool folder `prefix` as it was, copied to `copy`; `aside` takes what an update left while it is put back."""

    prefix: Path
    copy: Path
    aside: Path

    @property
    def _proof(self) -> Path:
        return self.copy.parent / ".rollback" / "continuity"

    @property
    def _partial(self) -> Path:
        return self.copy.with_suffix(".partial")

    @property
    def _retired(self) -> Path:
        return self.copy.with_suffix(".retired")

    @classmethod
    def take(cls, prefix: Path, folder: Path, *, content: str | None = None) -> "ToolSnapshot":
        """Copy the tool folder into `folder`, which must be on its file system so putting it back is a rename.

        `content` is supplied only after the existing continuity record has been verified.

        Raises:
            HeinzelError: If `folder` is on another file system; nothing may be installed then.
            OSError: If it can't be copied, for example for lack of space; nothing may be installed then.
        """
        folder = folder.parent.resolve() / folder.name
        snapshot = cls(prefix, folder / "before-update", folder / "after-update")
        snapshot.recover()  # a copy left by a process that died during its update is the truth: put it back first
        snapshot._clean_discarded()
        partial = snapshot._partial
        folder.mkdir(parents=True, exist_ok=True)
        if folder.stat().st_dev != prefix.stat().st_dev:
            msg = "The state folder is on another disk than hz, so an update couldn't be put back; nothing installed."
            raise HeinzelError(msg)
        partial.mkdir()
        for source in sorted(prefix.iterdir()):
            if source.name in KEPT:
                continue
            target = partial / source.name
            if source.is_dir() and not source.is_symlink():
                shutil.copytree(source, target, symlinks=True)
            else:
                shutil.copy2(source, target, follow_symlinks=False)
        if content is not None:
            proof = snapshot._proof
            private_write(proof, f"{content}\n{prefix}")
            _sync_directory(proof.parent)
        partial.rename(snapshot.copy)  # only a complete copy is ever put back
        if content is not None:
            _sync_directory(folder)
        return snapshot

    def restore(self, *, discard: bool = True) -> None:
        """Put the copy back into the tool folder, entry by entry, each an atomic rename; the folder itself stays.

        First everything the update left goes aside, then a marker says so, then the copy moves in.

        Raises:
            HeinzelError: If the copy must be verified before its gate can be removed.
            OSError: If an entry can't be moved; `recover` finishes the put-back from what is left.
        """
        self.recover(discard=discard)

    def recover(self, *, discard: bool = True) -> None:
        """Put the copy back if one is left; the copy is the truth, and nothing happens without one.

        A copy is left after a put-back that stopped half way, or by a process that died between taking the copy and
        the end of its update, untrialed new version and all.

        Only the process that holds the update (the apply lock, or none running) may call it.

        Raises:
            HeinzelError: If the copy must be verified before its gate can be removed.
            OSError: If an entry can't be moved.
        """
        if not self.copy.is_dir():
            return
        if discard and os.path.lexists(self._proof):
            msg = "An unfinished automatic update needs verified recovery."
            raise HeinzelError(msg)
        self.aside.mkdir(exist_ok=True)
        moved_in = self.aside / MOVED_ASIDE
        if not moved_in.exists():
            for entry in sorted(self.prefix.iterdir()):
                if entry.name not in KEPT:
                    entry.rename(self.aside / entry.name)
            moved_in.touch()
        for entry in sorted(self.copy.iterdir()):
            entry.rename(self.prefix / entry.name)
        if discard:
            self.discard()

    @classmethod
    def recover_interrupted(cls, installation: Installation, folder: Path) -> bool:
        """Restore a dead updater's copy and verify continuity before removing its durable gate.

        Raises:
            HeinzelError: If restored files cannot be independently verified against pre-update evidence.
            OSError: If recovery cannot finish safely; the copy's gate remains in place.
        """
        folder = folder.parent.resolve() / folder.name
        copy = folder / "before-update"
        if not folder.is_dir():
            return False
        installation = installation.for_interrupted_update()
        snapshot = cls(installation.prefix, copy, folder / "after-update")
        if not os.path.lexists(copy):
            if snapshot._retired.is_dir() or (snapshot._partial.is_dir() and installation.startup_state_matches()):
                snapshot._clean_discarded()
            return False
        if (
            snapshot.prefix.resolve(strict=True) != snapshot.prefix
            or copy.is_symlink()
            or not copy.is_dir()
            or snapshot.aside.is_symlink()
        ):
            msg = "The automatic update snapshot is unsafe; explicit recovery is required."
            raise HeinzelError(msg)
        content = snapshot._recovery_content()
        if content is None and not installation.is_tool:
            return False  # an old snapshot cannot identify a tool with an absent uv receipt
        snapshot.restore(discard=False)
        if content is not None:
            generation = installation.startup_fingerprint()
            if installation.content_fingerprint() != content:
                msg = "The restored installation does not match its pre-update copy; explicit recovery is required."
                raise HeinzelError(msg)
            if not installation.repaired_startup_state_matches():
                installation.record_startup_state(expected=generation)
        if not installation.repaired_startup_state_matches():
            msg = "The restored installation has no matching continuity record; explicit recovery is required."
            raise HeinzelError(msg)
        snapshot.discard()
        return True

    def _recovery_content(self) -> str | None:
        try:
            proof = private_read(self._proof)
        except FileNotFoundError:
            return None
        content, _, recorded_prefix = proof.partition("\n")
        if not re.fullmatch(r"[0-9a-f]{64}", content) or recorded_prefix != str(self.prefix):
            msg = "Automatic update recovery evidence is invalid; explicit recovery is required."
            raise HeinzelError(msg)
        return content

    def discard(self) -> None:
        """Delete the copy and what an update left."""
        # Disarm rollback atomically. A process killed while deleting an obsolete copy must not leave a partial
        # before-update that the next process mistakes for a complete rollback.
        self.copy.rename(self._retired)
        _sync_directory(self.copy.parent)
        self._clean_discarded()

    def _clean_discarded(self) -> None:
        for folder in (self.aside, self._proof.parent, self._partial, self._retired):
            with contextlib.suppress(FileNotFoundError):
                shutil.rmtree(folder)


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


TRIAL_SECONDS = 90.0
"""How long the new version may take to answer before its start counts as failed."""


TRIAL_DIRECTORIES = {
    "HZ_STATE_DIR": "state",
    "HZ_DATA_DIR": "data",
    "HZ_CACHE_DIR": "cache",
    "HZ_LOG_DIR": "log",
    "HZ_SYSTEM_DAEMON__UNIT_DIR": "units",
}
"""Where a trial start keeps what it writes: empty folders of its own, never the live ones."""


def trial_environment(scratch: Path, environ: Mapping[str, str], config: Path) -> dict[str, str]:
    """The environment of a trial start: a throwaway copy of the configuration, empty folders for everything else.

    The trial reads the same settings and plugins as the restart would, but anything it writes (state, data, cache,
    logs, the configuration) lands in `scratch`, which is deleted after it; it neither checks nor installs updates.
    """
    env = {key: value for key, value in environ.items() if key != "HEINZEL_UI_RESUME"}
    copy = scratch / "config"
    if config.is_dir():
        shutil.copytree(config, copy, symlinks=True, ignore_dangling_symlinks=True, dirs_exist_ok=True)
    else:
        copy.mkdir(parents=True)
    env["HZ_CONFIG_HOME"] = str(copy)
    for variable, name in TRIAL_DIRECTORIES.items():
        (scratch / name).mkdir(parents=True, exist_ok=True)
        env[variable] = str(scratch / name)
    env["HZ_AUTO_UPDATE"] = "false"
    env["HZ_UPDATE_CHECK"] = "false"
    return env


def trial_start(
    version: str,
    timeout: float = TRIAL_SECONDS,
    command: Sequence[str] | None = None,
    runner: ProcessRunner = SYSTEM_RUNNER,
) -> bool:
    """Whether the installed hz starts a web app server that answers with `version`, before this one restarts into it.

    It runs `hz ui serve` as the restart would, on a free port with its own token, against a throwaway copy of the
    configuration and empty folders (`trial_environment`), asks `/api/lifecycle` once, then stops it and deletes what
    it wrote. Nothing of this server or its live folders is touched, so a failure changes nothing here.
    """
    command = list(command or [sys.executable, "-m", "heinzel", "ui", "serve", "--port", "0", "--no-browser"])
    with tempfile.TemporaryDirectory(prefix="hz-trial-") as scratch:
        try:
            env = trial_environment(Path(scratch), os.environ, user_config_dir())
            child = runner.spawn(command, env=env, capture=True)
        except OSError:
            return False
        lines: list[str] = []
        reader = threading.Thread(target=lambda: lines.append(child.stdout.readline() if child.stdout else ""))
        reader.daemon = True
        reader.start()
        try:
            reader.join(timeout)
            return _answers(lines[0] if lines else "", version)
        finally:
            child.send_signal(signal.SIGINT)
            try:
                child.wait(15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(15)
            if child.stdout is not None:
                child.stdout.close()


def _answers(line: str, version: str) -> bool:
    """Whether the server whose session link is `line` answers `/api/lifecycle` with `version`."""
    url = urllib.parse.urlsplit(line.strip())
    token = urllib.parse.parse_qs(url.fragment).get("token", [""])[0]
    if url.scheme != "http" or url.hostname != "127.0.0.1" or not token:
        return False
    request = urllib.request.Request(
        f"http://127.0.0.1:{url.port}/api/lifecycle", headers={"Authorization": f"Bearer {token}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - checked above
            answer: object = json.loads(response.read(64 * 1024))
    except (OSError, ValueError):
        return False
    return isinstance(answer, dict) and cast("dict[str, object]", answer).get("version") == version
