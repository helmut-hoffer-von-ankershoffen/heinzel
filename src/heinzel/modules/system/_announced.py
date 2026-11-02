"""What an automatic update needs around `uv tool install` (ADR 0116): a copy to go back to, and a trial start.

The copy is of the whole uv tool environment, taken before anything is installed, on the same file system. Putting it
back moves each entry of the copy into the tool folder, whose own directory is never replaced: hz, every plugin, their
dependencies and the receipt come back byte for byte, and the installation's continuity fingerprint (ADR 0108), which
binds the folder itself and the content of everything in it, is the one recorded before the update. Each move is an
atomic rename; an interrupted put-back is finished by `ToolSnapshot.recover` from what is left on disk.

The trial start runs the newly installed `hz ui` on throwaway folders before anything restarts into it.
"""

import contextlib
import importlib.metadata
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from heinzel.kernel import HeinzelError, user_config_dir

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


@dataclass(frozen=True)
class ToolSnapshot:
    """The tool folder `prefix` as it was, copied to `copy`; `aside` takes what an update left while it is put back."""

    prefix: Path
    copy: Path
    aside: Path

    @classmethod
    def take(cls, prefix: Path, folder: Path) -> "ToolSnapshot":
        """Copy the tool folder into `folder`, which must be on its file system so putting it back is a rename.

        Raises:
            HeinzelError: If `folder` is on another file system; nothing may be installed then.
            OSError: If it can't be copied, for example for lack of space; nothing may be installed then.
        """
        snapshot = cls(prefix, folder / "before-update", folder / "after-update")
        snapshot.recover()
        for stale in (snapshot.copy, snapshot.aside):
            if stale.exists():
                shutil.rmtree(stale)
        folder.mkdir(parents=True, exist_ok=True)
        if folder.stat().st_dev != prefix.stat().st_dev:
            msg = "The state folder is on another disk than hz, so an update couldn't be put back; nothing installed."
            raise HeinzelError(msg)
        snapshot.copy.mkdir()
        for source in sorted(prefix.iterdir()):
            if source.name in KEPT:
                continue
            target = snapshot.copy / source.name
            if source.is_dir() and not source.is_symlink():
                shutil.copytree(source, target, symlinks=True)
            else:
                shutil.copy2(source, target, follow_symlinks=False)
        return snapshot

    def restore(self) -> None:
        """Put the copy back into the tool folder, entry by entry, each an atomic rename; the folder itself stays.

        First everything the update left goes aside, then a marker says so, then the copy moves in.

        Raises:
            OSError: If an entry can't be moved; `recover` finishes the put-back from what is left.
        """
        self.aside.mkdir(exist_ok=True)
        self.recover()

    def recover(self) -> None:
        """Finish a put-back that stopped half way; nothing happens unless one began (`aside` exists).

        Raises:
            OSError: If an entry can't be moved.
        """
        if not self.aside.is_dir() or not self.copy.is_dir():
            return
        moved_in = self.aside / MOVED_ASIDE
        if not moved_in.exists():
            for entry in sorted(self.prefix.iterdir()):
                if entry.name not in KEPT:
                    entry.rename(self.aside / entry.name)
            moved_in.touch()
        for entry in sorted(self.copy.iterdir()):
            entry.rename(self.prefix / entry.name)
        self.discard()

    def discard(self) -> None:
        """Delete the copy and what an update left."""
        for folder in (self.copy, self.aside):
            with contextlib.suppress(OSError):
                shutil.rmtree(folder)


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


def trial_start(version: str, timeout: float = TRIAL_SECONDS, command: Sequence[str] | None = None) -> bool:
    """Whether the installed hz starts a web app server that answers with `version`, before this one restarts into it.

    It runs `hz ui` as the restart would, on a free port with its own token, against a throwaway copy of the
    configuration and empty folders (`trial_environment`), asks `/api/lifecycle` once, then stops it and deletes what
    it wrote. Nothing of this server or its live folders is touched, so a failure changes nothing here.
    """
    command = list(command or [sys.executable, "-m", "heinzel", "ui", "--port", "0", "--no-browser"])
    with tempfile.TemporaryDirectory(prefix="hz-trial-") as scratch:
        try:
            env = trial_environment(Path(scratch), os.environ, user_config_dir())
            child = subprocess.Popen(  # noqa: S603 - this interpreter and fixed arguments, or a test's
                command, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, text=True
            )
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
