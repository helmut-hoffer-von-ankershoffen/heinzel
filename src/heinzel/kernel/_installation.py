"""How Heinzelmännchen is installed, and the uv commands that change the installation.

Heinzelmännchen is meant to be installed with `uv tool install`. uv records the tool's requirements — Heinzelmännchen
plus every
`--with <plugin>` — in `uv-receipt.toml`, and `uv tool upgrade` keeps them, whereas packages put into the tool
environment any other way are dropped on the next upgrade (ADR 0006). So adding a plugin means reinstalling the tool
with one more `--with`, and updating Heinzelmännchen means reinstalling it with the same `--with`s. Outside a uv tool
environment (a development checkout) packages are installed with `uv pip install` instead.
"""

import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable, Generator, Mapping, MutableMapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import parse_qs, urlsplit, urlunsplit

import tomlkit
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import (
    InvalidSdistFilename,
    InvalidWheelFilename,
    canonicalize_name,
    parse_sdist_filename,
    parse_wheel_filename,
)
from packaging.version import InvalidVersion, Version

from heinzel.kernel import _installation_continuity, _installed_metadata
from heinzel.kernel._credentials import subprocess_environment
from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._progress import Progress

RECEIPT_FILENAME = "uv-receipt.toml"
_MAX_RECEIPT_BYTES = 1024 * 1024
CORE_DISTRIBUTION = "heinzel"

Runner = Callable[..., subprocess.CompletedProcess[str]]
"""Runs a command and returns the completed process: `runner(command)`, or `runner(command, environment)` with
variables to add to the environment."""


COMMAND_TIMEOUT = 900  # seconds; a uv tool install that downloads a lot is slow, one that hangs is broken
HZ_TIMEOUT = 120


def _read_tool_receipt(prefix: Path) -> dict[str, Any]:
    return tomllib.loads(_read_tool_receipt_bytes(prefix).decode("utf-8"))


def _read_tool_receipt_bytes(prefix: Path) -> bytes:
    """Read a bounded regular receipt without following a receipt symlink.

    Raises:
        ValueError: If the receipt is unsafe, oversized or changes during the read.
    """
    directory = os.open(prefix, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(RECEIPT_FILENAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o022
                or info.st_size > _MAX_RECEIPT_BYTES
            ):
                msg = "The uv tool receipt is unsafe or too large."
                raise ValueError(msg)
            parts: list[bytes] = []
            remaining = _MAX_RECEIPT_BYTES + 1
            while remaining:
                chunk = os.read(fd, min(remaining, 65536))
                if not chunk:
                    break
                parts.append(chunk)
                remaining -= len(chunk)
            data = b"".join(parts)
            after = os.fstat(fd)
            if len(data) > _MAX_RECEIPT_BYTES or (
                info.st_dev,
                info.st_ino,
                info.st_size,
                info.st_mode,
                info.st_uid,
                info.st_mtime_ns,
                info.st_ctime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mode,
                after.st_uid,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                msg = "The uv tool receipt changed during inspection."
                raise ValueError(msg)
        finally:
            os.close(fd)
    finally:
        os.close(directory)
    return data


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


def _validate_receipt_policy(data: dict[str, Any]) -> dict[str, Any]:
    tool = data["tool"]
    if not isinstance(tool, dict):
        msg = "The receipt policy is malformed."
        raise TypeError(msg)
    tool = cast("dict[str, Any]", tool)
    known = {
        "requirements",
        "constraints",
        "overrides",
        "excludes",
        "build-constraint-dependencies",
        "python",
        "entrypoints",
        "options",
    }
    if set(tool) - known:
        msg = "The receipt has an unsupported policy field."
        raise ValueError(msg)
    for key in ("overrides", "excludes", "build-constraint-dependencies"):
        value = tool.get(key, [])
        if not isinstance(value, list) or value:
            msg = "The receipt has unsupported resolver policy."
            raise ValueError(msg)
    options = tool.get("options", {})
    if not isinstance(options, dict) or options:
        msg = "The receipt has unsupported tool options."
        raise ValueError(msg)
    return tool


def _constraints_from_receipt(data: dict[str, Any]) -> list[str]:
    tool = _validate_receipt_policy(data)
    entries = tool.get("constraints", [])
    if not isinstance(entries, list):
        msg = "The receipt constraints are malformed."
        raise TypeError(msg)
    result: list[str] = []
    for entry in cast("list[object]", entries):
        if isinstance(entry, str):
            text = entry
        elif isinstance(entry, dict):
            entry = cast("dict[str, Any]", entry)
            allowed = {"name", "extras", "specifier", "git", "url", "directory", "path", "marker"}
            if set(entry) - allowed or "name" not in entry:
                msg = "The receipt constraint has unsupported fields."
                raise ValueError(msg)
            sources = [key for key in ("specifier", "git", "url", "directory", "path") if key in entry]
            if (
                "git" in entry
                or len(sources) > 1
                or any(
                    key in entry and (not isinstance(entry[key], str) or not Path(entry[key]).is_absolute())
                    for key in ("directory", "path")
                )
            ):
                msg = "The receipt constraint has an ambiguous source."
                raise ValueError(msg)
            text = requirement_from_receipt(entry)
            if entry.get("marker"):
                text += f"; {entry['marker']}"
        else:
            msg = "The receipt constraint is malformed."
            raise TypeError(msg)
        result.append(str(Requirement(text)))
    return result


_OVERRIDE_ERROR = "The uv receipt overrides do not match the verified artifacts; inspect the installation manually."
_CONSTRAINT_ERROR = "The uv receipt constraints changed unexpectedly; inspect the installation manually."


@dataclass(frozen=True, slots=True)
class ToolConstraintPolicy:
    """Opaque snapshot of the validated pre-mutation receipt constraints."""

    present: bool
    entries: tuple[str, ...] = field(repr=False)


def _constraint_policy(data: dict[str, Any]) -> ToolConstraintPolicy:
    _constraints_from_receipt(data)
    tool = data["tool"]
    return ToolConstraintPolicy(
        present="constraints" in tool,
        entries=tuple(
            json.dumps(entry, sort_keys=True, separators=(",", ":")) for entry in tool.get("constraints", [])
        ),
    )


def _remove_transient_constraint(raw: bytes, original: ToolConstraintPolicy, added: str) -> bytes:
    """Remove exactly the last, temporary exact-version constraint from a matching policy.

    Raises:
        HeinzelError: If the policy or added constraint does not match exactly.
    """
    try:
        snapshot = cast("object", original)
        if (
            not isinstance(snapshot, ToolConstraintPolicy)
            or type(cast("object", snapshot.present)) is not bool
            or not isinstance(cast("object", snapshot.entries), tuple)
            or any(not isinstance(entry, str) for entry in cast("tuple[object, ...]", snapshot.entries))
            or (not snapshot.present and snapshot.entries)
        ):
            raise HeinzelError(_CONSTRAINT_ERROR)
        requirement = Requirement(added)
        specifiers = list(requirement.specifier)
        if requirement.url or requirement.marker or requirement.extras or len(specifiers) != 1:
            raise HeinzelError(_CONSTRAINT_ERROR)
        specifier = specifiers[0]
        if specifier.operator != "==" or "*" in specifier.version:
            raise HeinzelError(_CONSTRAINT_ERROR)
        Version(specifier.version)
        added_entry = {"name": canonicalize_name(requirement.name, validate=True), "specifier": str(specifier)}
        data = tomllib.loads(raw.decode("utf-8"))
        actual = _constraint_policy(data)
        expected = json.dumps(added_entry, sort_keys=True, separators=(",", ":"))
        if not actual.present or actual.entries != (*original.entries, expected):
            raise HeinzelError(_CONSTRAINT_ERROR)
        document = tomlkit.parse(raw.decode("utf-8"))
        table = cast("MutableMapping[str, Any]", document["tool"])
        if original.present:
            table["constraints"].pop()
        else:
            del table["constraints"]
        replacement = tomlkit.dumps(document).encode("utf-8")
        if original.present:
            data["tool"]["constraints"].pop()
        else:
            del data["tool"]["constraints"]
        if tomllib.loads(replacement.decode("utf-8")) != data:
            raise HeinzelError(_CONSTRAINT_ERROR)
    except (KeyError, TypeError, ValueError, UnicodeError, InvalidRequirement, InvalidVersion) as exc:
        raise HeinzelError(_CONSTRAINT_ERROR) from exc
    else:
        return replacement


def _expected_overrides(overrides: Mapping[str, Path]) -> dict[str, str]:
    expected: dict[str, str] = {}
    for name, path in overrides.items():
        normalized = canonicalize_name(name, validate=True)
        if normalized in expected or not path.is_absolute() or ".." in path.parts:
            raise HeinzelError(_OVERRIDE_ERROR)
        expected[normalized] = str(path)
    if not expected or len(set(expected.values())) != len(expected):
        raise HeinzelError(_OVERRIDE_ERROR)
    return expected


def _receipt_overrides(entries: object) -> dict[str, str]:
    if not isinstance(entries, list):
        raise HeinzelError(_OVERRIDE_ERROR)
    actual: dict[str, str] = {}
    for entry in cast("list[object]", entries):
        if not isinstance(entry, dict):
            raise HeinzelError(_OVERRIDE_ERROR)
        values = cast("dict[str, Any]", entry)
        if (
            set(values) != {"name", "path"}
            or not isinstance(values["name"], str)
            or not isinstance(values["path"], str)
        ):
            raise HeinzelError(_OVERRIDE_ERROR)
        normalized = canonicalize_name(values["name"], validate=True)
        if normalized in actual:
            raise HeinzelError(_OVERRIDE_ERROR)
        actual[normalized] = values["path"]
    return actual


def _without_verified_overrides(raw: bytes, overrides: Mapping[str, Path]) -> bytes:
    """Remove only an exact set of named file overrides, preserving every other parsed receipt value.

    Raises:
        HeinzelError: If the receipt or the expected overrides cannot be verified.
    """
    try:
        expected = _expected_overrides(overrides)
        original = tomllib.loads(raw.decode("utf-8"))
        tool = original["tool"]
        actual = _receipt_overrides(tool["overrides"])
        document = tomlkit.parse(raw.decode("utf-8"))
        table = cast("MutableMapping[str, Any]", document["tool"])
        del table["overrides"]
        replacement = tomlkit.dumps(document).encode("utf-8")
        del tool["overrides"]
        preserved = tomllib.loads(replacement.decode("utf-8")) == original
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise HeinzelError(_OVERRIDE_ERROR) from exc
    if actual != expected or not preserved:
        raise HeinzelError(_OVERRIDE_ERROR)
    return replacement


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
            tuple[
                dict[str, str],
                tuple[tuple[int, int], ...],
                dict[str, tuple[int, int, str]],
                tuple[str, str, tuple[int, ...]],
            ]
            | None
        ] = ContextVar("installation_tool_scope", default=None)

    @property
    def is_tool(self) -> bool:
        """Whether Heinzelmännchen runs from a `uv tool` environment."""
        return (self.prefix / RECEIPT_FILENAME).is_file()

    def requirements(self) -> list[str] | None:
        """Requirements of the uv tool environment, Heinzelmännchen first; None outside of one."""
        entries = self._receipt()
        return None if entries is None else [requirement_from_receipt(entry) for entry in entries]

    def _continuity_authority(self) -> tuple[dict[str, str], tuple[tuple[int, int], ...]]:
        target = self._tool_target()
        return target, self._target_directories(target)

    def startup_fingerprint(self) -> str:
        """Inspect the owned tool without executing installed code.

        Raises:
            HeinzelError: If the installation cannot be inspected safely.
        """
        return _installation_continuity.fingerprint(self.prefix, self._continuity_authority)

    def remove_tool_overrides(self, *, expected: str, overrides: Mapping[str, Path]) -> str:
        """Remove exactly the independently verified transient wheel overrides, without recording continuity.

        Raises:
            HeinzelError: If the receipt, overrides, or expected installation cannot be verified.
            OSError: If the receipt cannot be safely replaced and synchronized.
        """

        def transform() -> bytes:
            try:
                receipt = _read_tool_receipt_bytes(self.prefix)
            except ValueError as exc:
                raise HeinzelError(_OVERRIDE_ERROR) from exc
            return _without_verified_overrides(receipt, overrides)

        return _installation_continuity.replace_receipt(self.prefix, self._continuity_authority, expected, transform)

    def capture_tool_constraint_policy(self) -> ToolConstraintPolicy:
        """Capture validated receipt constraints before a trusted tool mutation.

        Raises:
            HeinzelError: If the installation or policy changes during inspection.
        """
        before = self.startup_fingerprint()
        try:
            result = _constraint_policy(_read_tool_receipt(self.prefix))
        except (OSError, ValueError, TypeError, KeyError, tomllib.TOMLDecodeError) as exc:
            raise HeinzelError(_CONSTRAINT_ERROR) from exc
        if self.startup_fingerprint() != before:
            raise HeinzelError(_CONSTRAINT_ERROR)
        return result

    def remove_transient_tool_constraint(self, *, expected: str, original: ToolConstraintPolicy, added: str) -> str:
        """Remove one exact-version guard only from the independently verified post-mutation generation.

        Raises:
            HeinzelError: If the receipt, policy or installation generation cannot be verified.
            OSError: If the private receipt replacement cannot be synchronized.
        """

        def transform() -> bytes:
            try:
                receipt = _read_tool_receipt_bytes(self.prefix)
            except ValueError as exc:
                raise HeinzelError(_CONSTRAINT_ERROR) from exc
            return _remove_transient_constraint(receipt, original, added)

        return _installation_continuity.replace_receipt(self.prefix, self._continuity_authority, expected, transform)

    def installed_core_version(self) -> str:
        """Inspect the currently installed core version without starting its interpreter.

        Raises:
            HeinzelError: If the selected core or its local metadata cannot be verified.
        """
        self._current_tool_target()
        before = self.startup_fingerprint()
        version = _installed_metadata.inspect_core_version(self.prefix)
        if self.startup_fingerprint() != before:
            msg = "The installed tool changed during version inspection; no code was started."
            raise HeinzelError(msg)
        return version

    def rollback_identity(self) -> str:
        """Capture exact owned inode identities and content for a proven-unchanged rollback.

        Raises:
            HeinzelError: If the installation cannot be inspected safely.
        """
        return _installation_continuity.fingerprint(self.prefix, self._continuity_authority, strict_identity=True)

    def verify_installed_requirements(
        self,
        requirements: list[str],
        *,
        expected_version: str | None = None,
        forbidden: tuple[str, ...] = (),
        constraints: tuple[str, ...] = (),
    ) -> dict[str, str]:
        """Inspect selected metadata under a stable local generation without starting the installed tool.

        Raises:
            HeinzelError: If the selected sources, versions or local generation cannot be established.
        """
        self._current_tool_target()
        before = self.startup_fingerprint()
        result = _installed_metadata.inspect_requirements(
            self.prefix, requirements, expected_version, forbidden=forbidden, constraints=constraints
        )
        if self.startup_fingerprint() != before:
            msg = "The installed tool changed during metadata inspection; no installed code was started."
            raise HeinzelError(msg)
        return result

    def validate_install_constraints(self, requirements: list[str], constraints: tuple[str, ...]) -> None:
        """Validate captured requirement and constraint combinations without reading or executing the tool.

        Raises:
            HeinzelError: If selected requirements and constraints conflict or cannot be safely combined.
        """
        _installed_metadata.validate_constraint_policy(requirements, constraints)

    def record_startup_state(self, *, expected: str) -> None:
        """Record the exact fingerprint independently verified after an authorized installation change.

        Capture `expected` after the change and before independent source, version and runtime checks; those checks
        must all concern that same observed generation.

        Raises:
            HeinzelError: If inspection or confirmation after the write fails.
            OSError: If the private record cannot be written or synchronized.
        """
        _installation_continuity.record(self.prefix, self._continuity_authority, expected=expected)

    def startup_state_matches(self) -> bool:
        """Check existing continuity evidence, without creating or refreshing it."""
        return _installation_continuity.matches(self.prefix, self._continuity_authority)

    def verify_tool_destination(self, store: Path, binary: Path) -> None:
        """Verify requested uv store and executable directory against this tool's receipt.

        Raises:
            HeinzelError: If the tool or requested destinations cannot be verified.
        """
        msg = "The requested uv store or executable directory does not match the verified tool."
        try:
            target = self._tool_target()
            if store.resolve(strict=True) != Path(target["UV_TOOL_DIR"]) or binary.resolve(strict=True) != Path(
                target["UV_TOOL_BIN_DIR"]
            ):
                raise HeinzelError(msg)
        except OSError as exc:
            raise HeinzelError(msg) from exc

    def tool_constraints(self) -> list[str]:
        """Read existing uv tool constraints as normalized requirements for a subsequent install.

        Raises:
            HeinzelError: If the receipt constraints cannot be preserved without guessing.
        """
        try:
            data = _read_tool_receipt(self.prefix.resolve(strict=True))
            result = _constraints_from_receipt(data)
        except (OSError, ValueError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
            msg = (
                "The tool's existing constraints cannot be safely preserved; the update was not started. "
                "Inspect the uv receipt and update the tool manually with its original resolver policy."
            )
            raise HeinzelError(msg) from exc
        else:
            return result

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
        data = _read_tool_receipt(self.prefix.resolve(strict=True))
        return list(data["tool"]["requirements"])

    @staticmethod
    def core_requirement(source: str) -> str:
        """Normalize and validate the selected core source as a distribution requirement.

        Raises:
            HeinzelError: If the source does not identify the core distribution.
        """
        return _core_requirement(source)

    def tool_install_command(self, core: str, plugins: Sequence[str], *, refresh: bool = False) -> list[str]:
        """Reinstall the tool environment from `core` with `plugins`, on the Python Heinzelmännchen runs on.

        With `refresh` uv asks the indexes and git remotes again instead of trusting its cache, so a branch is
        resolved to its newest commit and an index to its newest release.
        """
        requirement = _core_requirement(core)
        scope = self._tool_scope.get()
        python = scope[3][0] if scope else _installation_continuity.tool_python(self.prefix)[0]
        command = [
            self.uv,
            "tool",
            "install",
            "--reinstall",
            *(["--refresh"] if refresh else []),
            "--python",
            python,
            requirement,
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
            data = _read_tool_receipt(prefix)
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
        """Keep a validated target only for one update and its recovery attempt.

        Raises:
            HeinzelError: If the target or interpreter cannot be verified.
        """
        scope = self._tool_scope.get()
        if scope is not None:
            self._current_tool_target()
            if self._tool_target() != scope[0] or _installation_continuity.tool_python(self.prefix) != scope[3]:
                msg = "The verified uv tool target or interpreter changed; inspect it before updating."
                raise HeinzelError(msg)
            yield
            return
        target = self._tool_target()
        identities = self._target_directories(target)
        owned = self._owned_tool_links(target)
        python = _installation_continuity.tool_python(self.prefix)
        if self._tool_target() != target or self._target_directories(target) != identities:
            msg = "The verified uv tool target changed; update it explicitly with uv."
            raise HeinzelError(msg)
        token = self._tool_scope.set((target, identities, owned, python))
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
            data = _read_tool_receipt(prefix)
            owned: dict[str, tuple[int, int, str]] = {}
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
        result: list[tuple[int, int]] = []
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
        target, identities, owned, _python = scope
        if self._target_directories(target, owned if recovery else None) != identities:
            msg = "The verified uv tool target changed; update it explicitly with uv."
            raise HeinzelError(msg)
        return target

    def run(
        self,
        command: list[str],
        failure: str,
        activity: str,
        environment: Mapping[str, str] | None = None,
        *,
        additional_constraints: tuple[str, ...] = (),
    ) -> None:
        """Run a command with a progress indicator, with `environment` added to its environment if given.

        `additional_constraints` applies only to uv tool installs and is combined with the verified receipt policy
        for both the attempt and any recovery. Conflicting or malformed constraints are refused before uv starts.

        Raises:
            HeinzelError: If the command fails or its tool target or constraints cannot be verified.
        """
        if command[1:3] == ["tool", "install"]:
            self._run_tool(command, failure, activity, environment, additional_constraints)
            return
        if additional_constraints:
            msg = "Tool constraints cannot be applied to this command."
            raise HeinzelError(msg)
        result = self.attempt(command, activity, environment)
        if result.returncode != 0:
            msg = f"{failure}\n{result.stderr.strip() or result.stdout.strip()}"
            raise HeinzelError(msg)

    def _run_tool(
        self,
        command: list[str],
        failure: str,
        activity: str,
        environment: Mapping[str, str] | None,
        additional_constraints: tuple[str, ...],
    ) -> None:
        with self.tool_transaction():
            requirements = self.requirements()
            if not requirements:
                msg = "Cannot verify the running uv tool requirements; update it explicitly with uv."
                raise HeinzelError(msg)
            try:
                constraints = [*self.tool_constraints(), *(str(Requirement(item)) for item in additional_constraints)]
            except InvalidRequirement as exc:
                msg = "The additional tool constraints are invalid; the update was not started."
                raise HeinzelError(msg) from exc
            if additional_constraints:
                try:
                    python = command.index("--python")
                    selected = [
                        command[python + 2],
                        *(command[index + 1] for index, item in enumerate(command) if item == "--with"),
                    ]
                except (ValueError, IndexError) as exc:
                    msg = "The selected tool requirements cannot be verified; the update was not started."
                    raise HeinzelError(msg) from exc
                self.validate_install_constraints(selected, tuple(constraints))
            if constraints and any(arg.startswith(("-c", "--constraints")) for arg in command):
                msg = "Cannot combine supplied constraints with the existing receipt policy; update manually."
                raise HeinzelError(msg)
            restore = self.tool_install_command(requirements[0], requirements[1:])
            with tempfile.TemporaryDirectory(prefix="heinzel-tool-constraints-") as directory:
                args: list[str] = []
                if constraints:
                    contents = ("\n".join(constraints) + "\n").encode("utf-8")
                    if len(contents) > 1024 * 1024:
                        msg = "The receipt constraints are too large to preserve safely; inspect and update manually."
                        raise HeinzelError(msg)
                    path = Path(directory) / "constraints.txt"
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(contents)
                    args = ["--constraints", str(path)]
                self._run_tool_with_policy(command + args, restore, args, (failure, activity), environment)

    def _run_tool_with_policy(
        self,
        command: list[str],
        restore: list[str],
        constraints: list[str],
        messages: tuple[str, str],
        environment: Mapping[str, str] | None,
    ) -> None:
        """Run the attempt and recovery with captured policy.

        Raises:
            HeinzelError: If the attempt fails.
        """
        failure, activity = messages
        target = self._current_tool_target()
        try:
            original = self.startup_fingerprint()
        except HeinzelError:
            original = None
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
                restored = self.attempt(
                    restore + constraints, "Restoring the previous tool", environment, recovery=True
                )
                if restored.returncode == 0 and original is not None and self.startup_fingerprint() == original:
                    self.hz("--version")
                    recovery = "The previous tool was restored."
            except HeinzelError:
                pass
            if not recovery:
                recovery = f"The previous tool may not start; restore it with: {self.tool_restore_hint(restore)}"
                if constraints:
                    recovery += " Inspect and reapply its original constraints when restoring manually."
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
        """Run a command with a progress indicator and return how it ended, for callers that explain a failure.

        Raises:
            HeinzelError: If the selected interpreter cannot be verified.
        """
        if command[1:3] == ["tool", "install"]:
            environment = dict(environment or {}) | self._current_tool_target(recovery=recovery)
            scope = self._tool_scope.get()
            python = scope[3] if scope else _installation_continuity.tool_python(self.prefix)
            if (
                _installation_continuity.external_python(Path(python[0])) != python
                or (not recovery and _installation_continuity.tool_python(self.prefix) != python)
                or command.count("--python") != 1
            ):
                msg = "The original tool interpreter changed; inspect the installation before updating."
                raise HeinzelError(msg)
            position = command.index("--python") + 1
            if position >= len(command) or command[position] != python[0]:
                msg = "The selected tool interpreter changed; reload before updating."
                raise HeinzelError(msg)
        with self._progress.task(activity):
            return self._runner(command, environment) if environment else self._runner(command)

    def hz(self, *args: str) -> str:
        """Run `hz` in a fresh interpreter and return its output; this process still has the old environment loaded.

        Raises:
            HeinzelError: If the command fails.
        """
        try:
            result = subprocess.run(  # noqa: S603 - our own interpreter and module
                [sys.executable, "-I", "-m", "heinzel", *args],
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
