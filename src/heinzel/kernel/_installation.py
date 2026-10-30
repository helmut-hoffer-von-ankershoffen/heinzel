"""How Heinzelmännchen is installed, and the uv commands that change the installation.

Heinzelmännchen is meant to be installed with `uv tool install`. uv records the tool's requirements — Heinzelmännchen
plus every
`--with <plugin>` — in `uv-receipt.toml`, and `uv tool upgrade` keeps them, whereas packages put into the tool
environment any other way are dropped on the next upgrade (ADR 0006). So adding a plugin means reinstalling the tool
with one more `--with`, and updating Heinzelmännchen means reinstalling it with the same `--with`s. Outside a uv tool
environment (a development checkout) packages are installed with `uv pip install` instead.
"""

import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, urlsplit, urlunsplit

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import (
    InvalidSdistFilename,
    InvalidWheelFilename,
    canonicalize_name,
    parse_sdist_filename,
    parse_wheel_filename,
)
from packaging.version import InvalidVersion, Version

from heinzel.kernel._credentials import subprocess_environment
from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._progress import Progress

RECEIPT_FILENAME = "uv-receipt.toml"
CORE_DISTRIBUTION = "heinzel"

Runner = Callable[..., subprocess.CompletedProcess[str]]
"""Runs a command and returns the completed process: `runner(command)`, or `runner(command, environment)` with
variables to add to the environment."""


COMMAND_TIMEOUT = 900  # seconds; a uv tool install that downloads a lot is slow, one that hangs is broken
HZ_TIMEOUT = 120


def _run(command: Sequence[str], environment: Mapping[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command; a timeout becomes exit code 124 (as with timeout(1)), so callers handle it like any failure."""
    env = subprocess_environment() | dict(environment or {})
    try:
        return subprocess.run(  # noqa: S603 - trusted parts, no shell
            command, capture_output=True, text=True, check=False, timeout=COMMAND_TIMEOUT, env=env
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, 124, "", f"{command[0]} timed out after {COMMAND_TIMEOUT}s")


def _core_requirement(source: str) -> str:  # noqa: C901 - validate several source forms before invoking uv
    """Bind a selected source to the Heinzel distribution before asking uv to resolve it.

    Raises:
        HeinzelError: If a requirement or identifiable artifact names another distribution.
    """
    message = "The selected source must install the heinzel distribution."
    try:
        requirement = Requirement(source)
    except InvalidRequirement:
        parts = urlsplit(source)
        if parts.scheme in {"https", "http", "file", "git+https", "git+ssh", "git+file"}:
            url = source
        else:
            try:
                path = Path(source).expanduser().resolve(strict=True)
                url = path.as_uri()
            except (OSError, ValueError) as exc:
                raise HeinzelError(message) from exc
        try:
            requirement = Requirement(f"{CORE_DISTRIBUTION} @ {url}")
        except InvalidRequirement as exc:
            raise HeinzelError(message) from exc
    if canonicalize_name(requirement.name) != CORE_DISTRIBUTION or requirement.marker:
        raise HeinzelError(message)
    if requirement.url:
        filename = Path(urlsplit(requirement.url).path).name
        name: str
        try:
            if filename.endswith(".whl"):
                name, _, _, _ = parse_wheel_filename(filename)
            elif filename.endswith((".tar.gz", ".zip")):
                name, _ = parse_sdist_filename(filename)
            else:
                name = CORE_DISTRIBUTION
        except (InvalidWheelFilename, InvalidSdistFilename) as exc:
            raise HeinzelError(message) from exc
        if canonicalize_name(name) != CORE_DISTRIBUTION:
            raise HeinzelError(message)
    return str(requirement)


def requirement_from_receipt(entry: dict[str, Any]) -> str:
    """Turn a requirement from a uv tool receipt back into a PEP 508 string uv accepts on the command line."""
    name = str(entry["name"])
    if extras := entry.get("extras"):
        name = f"{name}[{','.join(extras)}]"
    if "git" in entry:
        parts = urlsplit(str(entry["git"]))
        query = parse_qs(parts.query)
        ref = next((query[key][0] for key in ("rev", "tag", "branch") if key in query), None)
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        fragment = f"#subdirectory={query['subdirectory'][0]}" if "subdirectory" in query else ""
        return f"{name} @ git+{url}{'@' + ref if ref else ''}{fragment}"
    for key in ("directory", "editable", "path"):
        if key in entry:
            return f"{name} @ {Path(entry[key]).resolve().as_uri()}"
    if "url" in entry:
        return f"{name} @ {entry['url']}"
    return f"{name}{entry.get('specifier', '')}"


type PinKind = Literal["release", "ref", "commit", "default", "local", "url", "index"]
"""What the core requirement installs from: a release tag `vX.Y.Z`, another git ref (a branch or a tag that is no
release), a commit, a git repository's default branch, a local directory or file, a URL, or a package index."""

_COMMIT = re.compile(r"[0-9a-f]{7,40}")
_RELEASE_PARTS = 3  # vX.Y.Z


def _is_release_tag(ref: str) -> bool:
    """Whether `ref` is a release tag, `v` and a SemVer version such as `v0.4.0` or `v0.5.0rc1`."""
    if not ref.startswith("v"):
        return False
    try:
        return len(Version(ref[1:]).release) == _RELEASE_PARTS
    except InvalidVersion:
        return False


@dataclass(frozen=True, slots=True)
class CorePin:
    """Where the core requirement in the uv tool receipt installs Heinzelmännchen from, and what it is pinned to."""

    kind: PinKind
    requirement: str
    """The requirement as uv takes it on the command line, e.g. `heinzel @ git+https://github.com/org/heinzel@v0.2.0`."""
    ref: str | None = None
    """The git ref, the path, the URL or the version specifier it is pinned to; None if it follows a default."""
    name: str = CORE_DISTRIBUTION
    """The distribution with its extras, e.g. `heinzel[web]`."""
    git: str | None = None
    """The git repository's URL without ref, query or fragment; None if it isn't installed from git."""
    subdirectory: str | None = None

    @property
    def version(self) -> str | None:
        """The version of a release tag (`v0.2.0` is `0.2.0`); None for anything else."""
        return self.ref[1:] if self.kind == "release" and self.ref else None

    def source_at(self, ref: str) -> str:
        """The same git repository at `ref`, as `hz system update --source` takes it.

        Raises:
            HeinzelError: If Heinzelmännchen isn't installed from git.
        """
        if self.git is None:
            msg = f"{self.requirement} isn't installed from git, so it has no ref to move."
            raise HeinzelError(msg)
        fragment = f"#subdirectory={self.subdirectory}" if self.subdirectory else ""
        return f"git+{self.git}@{ref}{fragment}"

    def at(self, ref: str) -> str:
        """The same requirement at another git ref, e.g. a newer release tag.

        Raises:
            HeinzelError: If Heinzelmännchen isn't installed from git.
        """
        return f"{self.name} @ {self.source_at(ref)}"


def pin_from_receipt(entry: dict[str, Any]) -> CorePin:
    """What a requirement from a uv tool receipt installs from and is pinned to.

    uv records `git+URL@ref` as `?rev=ref` whether the ref is a branch, a tag or a commit, so a ref that looks like
    neither a release tag nor a commit is a `ref`: a branch, or a tag that is no release.
    """
    name = str(entry["name"])
    if extras := entry.get("extras"):
        name = f"{name}[{','.join(extras)}]"
    requirement = requirement_from_receipt(entry)
    if "git" in entry:
        parts = urlsplit(str(entry["git"]))
        query = parse_qs(parts.query)
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        subdirectory = query["subdirectory"][0] if "subdirectory" in query else None
        ref = next((query[key][0] for key in ("rev", "tag", "branch") if key in query), None)
        kind: PinKind
        if ref is None:
            kind = "default"
        elif "branch" not in query and _is_release_tag(ref):
            kind = "release"
        elif "branch" not in query and "tag" not in query and _COMMIT.fullmatch(ref):
            kind = "commit"
        else:
            kind = "ref"
        return CorePin(kind, requirement, ref, name, url, subdirectory)
    for key in ("directory", "editable", "path"):
        if key in entry:
            return CorePin("local", requirement, str(Path(entry[key]).resolve()), name)
    if "url" in entry:
        return CorePin("url", requirement, str(entry["url"]), name)
    return CorePin("index", requirement, entry.get("specifier") or None, name)


class Installation:
    """The environment Heinzelmännchen runs in, and the commands that install into it. Injected by type."""

    def __init__(
        self,
        progress: Progress,
        prefix: Path | None = None,
        runner: Runner = _run,
        uv: str | None = None,
    ) -> None:
        """Create the installation; `progress` is injected, the rest is for tests.

        Args:
            progress: Shows that a command is running.
            prefix: Environment Heinzelmännchen runs in; defaults to `sys.prefix`.
            runner: Runs a command and returns the completed process.
            uv: Path to the uv executable; defaults to the one on `PATH`.
        """
        self._progress = progress
        self.prefix = prefix or Path(sys.prefix)
        self._runner = runner
        self._uv = uv or shutil.which("uv")
        self._tool_scope: ContextVar[
            tuple[dict[str, str], tuple[tuple[int, int], ...], dict[str, tuple[int, int, str]]] | None
        ] = ContextVar("installation_tool_scope", default=None)

    @property
    def is_tool(self) -> bool:
        """Whether Heinzelmännchen runs from a `uv tool` environment."""
        return (self.prefix / RECEIPT_FILENAME).is_file()

    def requirements(self) -> list[str] | None:
        """Requirements of the uv tool environment, Heinzelmännchen first; None outside of one."""
        entries = self._receipt()
        return None if entries is None else [requirement_from_receipt(entry) for entry in entries]

    def core_pin(self) -> CorePin | None:
        """Where the uv tool environment installs Heinzelmännchen from and what it is pinned to; None outside of one."""
        entries = self._receipt()
        return pin_from_receipt(entries[0]) if entries else None

    def plugin_pins(self) -> list[CorePin]:
        """What each plugin in the uv tool environment installs from, as the receipt has it; empty outside of one."""
        return [pin_from_receipt(entry) for entry in (self._receipt() or [])[1:]]

    def _receipt(self) -> list[dict[str, Any]] | None:
        if not self.is_tool:
            return None
        data = tomllib.loads((self.prefix / RECEIPT_FILENAME).read_text(encoding="utf-8"))
        return list(data["tool"]["requirements"])

    def tool_install_command(self, core: str, plugins: Sequence[str], *, refresh: bool = False) -> list[str]:
        """Reinstall the tool environment from `core` with `plugins`, on the Python Heinzelmännchen runs on.

        With `refresh` uv asks the indexes and git remotes again instead of trusting its cache, so a branch is
        resolved to its newest commit and an index to its newest release.
        """
        python = f"{sys.version_info.major}.{sys.version_info.minor}"
        command = [
            self.uv,
            "tool",
            "install",
            "--reinstall",
            *(["--refresh"] if refresh else []),
            "--python",
            python,
            _core_requirement(core),
        ]
        for plugin in plugins:
            command += ["--with", plugin]
        return command

    def pip_command(self, action: str, spec: str) -> list[str]:
        """`uv pip install` or `uninstall` into the environment Heinzelmännchen runs in."""
        return [self.uv, "pip", action, "--python", sys.executable, spec]

    def _tool_target(self) -> dict[str, str]:
        """Bind uv's tool store and executable directory to the running installation.

        Raises:
            HeinzelError: If the receipt does not identify the running tool and its installed entry point.
        """
        msg = "Cannot verify the running uv tool and its hz entry point; update it explicitly with uv."
        try:
            prefix = self.prefix.resolve(strict=True)
            if prefix.name != CORE_DISTRIBUTION:
                raise HeinzelError(msg)
            data = tomllib.loads((prefix / RECEIPT_FILENAME).read_text(encoding="utf-8"))
            entries = data["tool"]["entrypoints"]
            if (prefix / "bin").is_symlink():
                raise HeinzelError(msg)
            matches = [item for item in entries if item.get("name") == "hz"]
            if len(matches) != 1:
                raise HeinzelError(msg)
            executable = Path(matches[0]["install-path"])
            directory = executable.parent.resolve(strict=True)
            for item in entries:
                name, path = item["name"], Path(item["install-path"])
                if (
                    not isinstance(name, str)
                    or not name
                    or name in {".", ".."}
                    or Path(name).name != name
                    or not path.is_absolute()
                    or path.name != name
                    or path.parent.resolve(strict=True) != directory
                    or not path.is_symlink()
                    or (prefix / "bin" / name).is_symlink()
                    or not (prefix / "bin" / name).is_file()
                    or path.resolve(strict=True) != (prefix / "bin" / name).resolve(strict=True)
                ):
                    raise HeinzelError(msg)
            return {"UV_TOOL_DIR": str(prefix.parent), "UV_TOOL_BIN_DIR": str(directory)}
        except (OSError, ValueError, TypeError, KeyError, AttributeError, tomllib.TOMLDecodeError) as exc:
            raise HeinzelError(msg) from exc

    @contextmanager
    def tool_transaction(self) -> Generator[None]:
        """Keep a validated target only for one update and its recovery attempt."""
        target = self._tool_target()
        identities = self._target_directories(target)
        owned = self._owned_tool_links(target)
        token = self._tool_scope.set((target, identities, owned))
        try:
            yield
        finally:
            self._tool_scope.reset(token)

    def _owned_tool_links(self, target: Mapping[str, str]) -> dict[str, tuple[int, int, str]]:
        """Capture only receipt-owned, presently verified links for this operation's recovery.

        Raises:
            HeinzelError: If the entrypoints cannot be verified.
        """
        msg = "The verified uv tool entrypoints changed; update it explicitly with uv."
        try:
            if self._tool_target() != target:
                raise HeinzelError(msg)
            prefix = self.prefix.resolve(strict=True)
            data = tomllib.loads((prefix / RECEIPT_FILENAME).read_text(encoding="utf-8"))
            owned = {}
            for entry in data["tool"]["entrypoints"]:
                path = Path(entry["install-path"])
                name = entry["name"]
                if path.parent.resolve(strict=True) != Path(target["UV_TOOL_BIN_DIR"]) or path.name != name:
                    raise HeinzelError(msg)
                info = path.lstat()
                link = str(path.readlink())
                if path.resolve(strict=True) != (prefix / "bin" / name).resolve(strict=True):
                    raise HeinzelError(msg)
                owned[name] = (info.st_dev, info.st_ino, link)
            if self._tool_target() != target:
                raise HeinzelError(msg)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, tomllib.TOMLDecodeError) as exc:
            raise HeinzelError(msg) from exc
        else:
            return owned

    @staticmethod
    def _target_directories(
        target: Mapping[str, str], owned: Mapping[str, tuple[int, int, str]] | None = None
    ) -> tuple[tuple[int, int], ...]:
        """Refuse a missing, replaced or symlinked authority directory.

        Raises:
            HeinzelError: If a target directory cannot be verified.
        """
        result = []
        msg = "The verified uv tool target changed; update it explicitly with uv."
        try:
            for key in ("UV_TOOL_DIR", "UV_TOOL_BIN_DIR"):
                path = Path(target[key])
                if path.is_symlink() or not path.is_dir() or path.resolve(strict=True) != path:
                    raise HeinzelError(msg)
                info = path.stat()
                if key == "UV_TOOL_BIN_DIR":
                    with os.scandir(path) as entries:
                        for entry in entries:
                            if entry.is_symlink():
                                try:
                                    entry.stat(follow_symlinks=True)
                                except OSError as exc:
                                    link_info = entry.stat(follow_symlinks=False)
                                    current = (link_info.st_dev, link_info.st_ino, str(Path(entry.path).readlink()))
                                    if owned is None or owned.get(entry.name) != current:
                                        detail = "Repair unresolved uv executable symlinks before updating."
                                        raise HeinzelError(detail) from exc
                result.append((info.st_dev, info.st_ino))
        except (OSError, KeyError) as exc:
            raise HeinzelError(msg) from exc
        return tuple(result)

    def _current_tool_target(self, *, recovery: bool = False) -> dict[str, str]:
        scope = self._tool_scope.get()
        if scope is None:
            target = self._tool_target()
            self._target_directories(target)
            return target
        target, identities, owned = scope
        if self._target_directories(target, owned if recovery else None) != identities:
            msg = "The verified uv tool target changed; update it explicitly with uv."
            raise HeinzelError(msg)
        return target

    def run(
        self, command: list[str], failure: str, activity: str, environment: Mapping[str, str] | None = None
    ) -> None:
        """Run a command with a progress indicator, with `environment` added to its environment if given.

        Raises:
            HeinzelError: With `failure` and the command's output, if it fails.
        """
        if command[1:3] == ["tool", "install"]:
            self._run_tool(command, failure, activity, environment)
            return
        result = self.attempt(command, activity, environment)
        if result.returncode != 0:
            msg = f"{failure}\n{result.stderr.strip() or result.stdout.strip()}"
            raise HeinzelError(msg)

    def _run_tool(self, command: list[str], failure: str, activity: str, environment: Mapping[str, str] | None) -> None:
        requirements = self.requirements()
        if not requirements:
            msg = "Cannot verify the running uv tool requirements; update it explicitly with uv."
            raise HeinzelError(msg)
        restore = self.tool_install_command(requirements[0], requirements[1:])
        with self.tool_transaction():
            target = self._current_tool_target()
            result = self.attempt(command, activity, environment)
            if result.returncode == 0:
                return
            detail = result.stderr.strip() or result.stdout.strip()
            try:
                intact = self._tool_target() == target
            except HeinzelError:
                intact = False
            recovery = ""
            if not intact:
                try:
                    restored = self.attempt(restore, "Restoring the previous tool", environment, recovery=True)
                    if restored.returncode == 0:
                        self.hz("--version")
                        recovery = "The previous tool was restored."
                except HeinzelError:
                    pass
                if not recovery:
                    recovery = f"The previous tool may not start; restore it with: {self.tool_restore_hint(restore)}"
            msg = "\n".join(part for part in (failure, detail, recovery) if part)
            raise HeinzelError(msg)

    def tool_restore_hint(self, command: Sequence[str]) -> str:
        """Show a restore command with its verified tool store and executable directory."""
        scope = self._tool_scope.get()
        target = scope[0] if scope else self._tool_target()
        variables = " ".join(f"{key}={shlex.quote(value)}" for key, value in target.items())
        return f"{variables} {shlex.join(command)}"

    def attempt(
        self, command: list[str], activity: str, environment: Mapping[str, str] | None = None, *, recovery: bool = False
    ) -> subprocess.CompletedProcess[str]:
        """Run a command with a progress indicator and return how it ended, for callers that explain a failure."""
        if command[1:3] == ["tool", "install"]:
            environment = dict(environment or {}) | self._current_tool_target(recovery=recovery)
        with self._progress.task(activity):
            return self._runner(command, environment) if environment else self._runner(command)

    def hz(self, *args: str) -> str:
        """Run `hz` in a fresh interpreter and return its output; this process still has the old environment loaded.

        Raises:
            HeinzelError: If the command fails.
        """
        try:
            result = subprocess.run(  # noqa: S603 - our own interpreter and module
                [sys.executable, "-m", "heinzel", *args],
                capture_output=True,
                text=True,
                check=False,
                timeout=HZ_TIMEOUT,
                # A probe, not a person: no update check (ADR 0031) to wait for or to spend a request on.
                env=subprocess_environment() | {"HZ_UPDATE_CHECK": "false"},
            )
        except subprocess.TimeoutExpired as exc:
            msg = f"`hz {' '.join(args)}` did not finish within {HZ_TIMEOUT}s after the change."
            raise HeinzelError(msg) from exc
        except OSError as exc:
            msg = f"`hz {' '.join(args)}` could not start after the change."
            raise HeinzelError(msg) from exc
        if result.returncode != 0:
            msg = f"`hz {' '.join(args)}` failed after the change:\n{result.stderr}"
            raise HeinzelError(msg)
        return result.stdout

    @property
    def uv(self) -> str:
        """The uv executable.

        Raises:
            HeinzelError: If uv is not installed.
        """
        if not self._uv:
            msg = "uv is required to manage Heinzelmännchen's installation: https://docs.astral.sh/uv/"
            raise HeinzelError(msg)
        return self._uv
