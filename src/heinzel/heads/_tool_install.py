"""Scoped standard-app installation; existing tools use the kernel's guarded recovery path."""

import hashlib
import json
import os
import shlex
import stat
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from email.parser import BytesParser
from importlib.metadata import distributions
from itertools import islice
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

from heinzel import HeinzelError, Progress
from heinzel.kernel import Installation, subprocess_environment

ENTRYPOINTS = ("hz", "hz-install", "heinzel")
MAX_WHEEL = 128 * 1024 * 1024
MAX_METADATA = 1024 * 1024
MAX_METADATA_TOTAL = 16 * MAX_METADATA
MAX_SITE_DIRS = 32
MAX_SITE_ENTRIES = 10000
MAX_HEADER_LENGTH = 256
VERSION_ERROR = "Installed app versions cannot be safely inspected; inspect or update the composition manually."


@dataclass(frozen=True, slots=True)
class ToolTarget:
    """The uv-resolved store and executable directory for one installation."""

    store: Path
    binary: Path

    @property
    def prefix(self) -> Path:
        """The managed application environment."""
        return self.store / "heinzel"

    @property
    def environment(self) -> dict[str, str]:
        """Bind both uv target directories explicitly."""
        return {"UV_TOOL_DIR": str(self.store), "UV_TOOL_BIN_DIR": str(self.binary)}


def resolve_target(uv: str) -> ToolTarget:
    """Resolve uv's configured destinations before selecting an existing tool.

    Raises:
        HeinzelError: If uv does not identify absolute, owned destinations.
    """
    paths: list[Path] = []
    for options in ([], ["--bin"]):
        result = subprocess.run(  # noqa: S603 - fixed uv query without a shell
            [uv, "tool", "dir", *options],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
            env=subprocess_environment(),
        )
        path = Path(result.stdout.strip())
        if not path.is_absolute() or path.is_symlink():
            msg = "Cannot verify uv's tool directories. Choose owned directories and retry."
            raise HeinzelError(msg)
        path = path.resolve()
        path.mkdir(parents=True, exist_ok=True)
        paths.append(path)
    target = ToolTarget(*paths)
    if target.binary == target.prefix or target.prefix in target.binary.parents:
        msg = "The executable directory cannot be inside Heinzel's tool environment."
        raise HeinzelError(msg)
    _identities(target)
    return target


def _identities(target: ToolTarget) -> tuple[tuple[int, int], ...]:
    """Verify owned, stable directories and refuse unresolved executable links.

    Raises:
        HeinzelError: If a destination is replaced, foreign or unresolved.
    """
    identities: list[tuple[int, int]] = []
    for path in (target.store, target.binary):
        info = path.lstat()
        if path.is_symlink() or not path.is_dir() or info.st_uid != os.getuid() or path.resolve() != path:
            msg = "Cannot verify ownership of uv's tool directories."
            raise HeinzelError(msg)
        identities.append((info.st_dev, info.st_ino))
    for name in ENTRYPOINTS:
        path = target.binary / name
        if path.is_symlink() and not path.exists():
            msg = "Repair unresolved Heinzel command symlinks before installing."
            raise HeinzelError(msg)
    return tuple(identities)


def _check_entrypoints(target: ToolTarget, *, existing: bool) -> None:
    """Refuse names belonging to another tool before uv can change an environment.

    Raises:
        HeinzelError: If an existing command is not a link to this managed application.
    """
    for name in ENTRYPOINTS:
        path = target.binary / name
        if not os.path.lexists(path):
            continue
        if not existing or not path.is_symlink() or path.resolve(strict=True) != target.prefix / "bin" / name:
            msg = f"The command {path} belongs to another installation. Move it before installing Heinzel."
            raise HeinzelError(msg)


class _TargetInstallation(Installation):
    """A transient bootstrap SDK must probe the managed tool, including after rollback."""

    def __init__(self, prefix: Path, uv: str) -> None:
        """Bind the managed prefix; kernel transactions capture its current interpreter."""
        super().__init__(Progress(), prefix=prefix, uv=uv)

    def hz(self, *args: str) -> str:
        """Run the selected managed interpreter, never the transient bootstrap interpreter.

        Raises:
            HeinzelError: If the managed application cannot start.
        """
        try:
            result = subprocess.run(  # noqa: S603 - fixed selected interpreter and argument vector
                [str(self.prefix / "bin/python"), "-I", "-m", "heinzel", *args],
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
                env=subprocess_environment() | {"HZ_UPDATE_CHECK": "false"},
            )
        except (OSError, subprocess.SubprocessError) as exc:
            msg = "The managed Heinzel application could not start."
            raise HeinzelError(msg) from exc
        return result.stdout


def target_installation(target: ToolTarget, uv: str) -> Installation:
    """Create the selected installation without resolving it from the bootstrap process."""
    return _TargetInstallation(target.prefix, uv)


def verify_fresh_python_policy(environment: Mapping[str, str]) -> None:
    """Refuse a system-only preference before a standard-install pending marker is written.

    Raises:
        HeinzelError: If the current Python policy forbids managed Python.
    """
    if (
        environment.get("UV_NO_MANAGED_PYTHON", "").casefold() in {"1", "true", "yes", "on"}
        or environment.get("UV_PYTHON_PREFERENCE", "").casefold() == "only-system"
    ):
        msg = "The standard app needs a uv-managed Python. Remove the system-only Python preference and retry."
        raise HeinzelError(msg)


def _fresh_selection(command: list[str], target: ToolTarget) -> tuple[list[str], dict[str, str]]:
    if any(item in {"--python", "--no-managed-python"} or item.startswith(("--python=", "-p")) for item in command[3:]):
        msg = "A fresh standard installation requires a uv-managed Python."
        raise HeinzelError(msg)
    environment = subprocess_environment() | target.environment
    verify_fresh_python_policy(environment)
    # uv treats an explicit UV_PYTHON path as authoritative even with --managed-python. The fresh
    # standard app must let uv select a managed interpreter compatible with the verified composition.
    environment.pop("UV_PYTHON", None)
    return [*command[:3], "--managed-python", *command[3:]], environment


def _fresh_failure(target: ToolTarget, uv: str, environment: Mapping[str, str]) -> str:
    problem = "The initial installation did not complete"
    if environment.get("UV_PYTHON_DOWNLOADS", "").casefold() == "never":
        problem += "; Python downloads are disabled, so ensure a compatible uv-managed Python is installed"
    return inspection_guidance(target, uv, problem)


def run_tool(  # noqa: PLR0913, PLR0917 - explicit selected installation and verified artifact inputs
    command: list[str],
    target: ToolTarget,
    expected: Mapping[str, tuple[str, str]],
    wheels: Mapping[str, Path],
    installation: Installation,
    preserved: Sequence[str],
) -> str:
    """Install with explicit authority; a refresh delegates rollback to the kernel.

    Raises:
        HeinzelError: If the destination is unsafe or installation fails.
    """
    if "--force" in command:
        msg = "The standard installer cannot overwrite foreign commands."
        raise HeinzelError(msg)
    identities = _identities(target)
    _verify_wheels(expected, wheels)
    existing = os.path.lexists(target.prefix)
    if target.prefix.is_symlink():
        msg = "The Heinzel tool environment is a symlink; repair it explicitly with uv."
        raise HeinzelError(msg)
    _check_entrypoints(target, existing=existing)
    if existing:
        before = _verify_existing_versions(target, installation, expected)
        constraints = tuple(installation.tool_constraints())
        selected_artifacts = [f"{name} @ {path.as_uri()}#sha256={expected[name][1]}" for name, path in wheels.items()]
        installation.validate_install_constraints([*selected_artifacts, *preserved], constraints)
        # The kernel verifies the genuine receipt, target and owned links, and recovers a failed refresh.
        with installation.tool_transaction():
            if installation.startup_fingerprint() != before:
                msg = "The installation changed before refresh; inspect it and retry."
                raise HeinzelError(msg)
            recovery = installation.tool_install_command("heinzel", [])
            python = recovery[recovery.index("--python") + 1]
            selected = [*command[:3], "--reinstall", *command[3:], "--python", python]
            installation.run(selected, "The app refresh failed.", "Installing Heinzelmännchen", target.environment)
        try:
            generation = installation.startup_fingerprint()
            generation = _verify_and_normalize(
                target, expected, wheels, installation, preserved, constraints, generation
            )
        except HeinzelError as exc:
            # A successful uv command followed by a provenance mismatch does not prove ownership of the
            # newly observed state. Retain it rather than rolling back a possible concurrent operation.
            raise HeinzelError(
                inspection_guidance(target, command[0], "The app refresh could not be verified")
            ) from exc
        return generation
    if "--reinstall" in command:
        msg = "A fresh installation cannot replace a concurrently installed tool."
        raise HeinzelError(msg)
    if _identities(target) != identities or os.path.lexists(target.prefix):
        msg = "The installation destination changed; retry after other installations finish."
        raise HeinzelError(msg)
    managed_command, environment = _fresh_selection(command, target)
    try:
        subprocess.run(  # noqa: S603 - verified wheel command, scoped target and no shell
            managed_command, env=environment, stdout=sys.stderr, check=True, timeout=600
        )
        generation = installation.startup_fingerprint()
        generation = _verify_and_normalize(target, expected, wheels, installation, preserved, (), generation)
    except (OSError, subprocess.SubprocessError, HeinzelError) as exc:
        # A newly appearing prefix may belong to a concurrent installer. A valid-looking receipt does not
        # establish operation ownership, so never recursively delete it or remove its executable links.
        msg = _fresh_failure(target, command[0], environment)
        raise HeinzelError(msg) from exc
    return generation


def _verify_existing_versions(
    target: ToolTarget, installation: Installation, expected: Mapping[str, tuple[str, str]]
) -> str:
    """Bracket local version observations with the verified original installation generation.

    Raises:
        HeinzelError: If a selected version is newer or the existing generation cannot be inspected.
    """
    installation.verify_tool_destination(target.store, target.binary)
    before = installation.startup_fingerprint()
    current = installation.installed_core_version()
    requirements = installation.requirements()
    if not requirements:
        msg = "The existing tool requirements cannot be verified; inspect the installation before refreshing."
        raise HeinzelError(msg)
    required = {str(canonicalize_name(Requirement(item).name)) for item in requirements} & set(expected)
    versions = _installed_versions(target.prefix, set(expected), required)
    if versions.get("heinzel") != Version(current) or installation.startup_fingerprint() != before:
        msg = "The installation changed during version inspection; inspect it before refreshing."
        raise HeinzelError(msg)
    for name, version in versions.items():
        if version > Version(expected[name][0]):
            msg = f"The selected app profile would downgrade {name}; inspect the selected versions before installing."
            raise HeinzelError(msg)
    return before


def _installed_versions(prefix: Path, selected: set[str], required: set[str]) -> dict[str, Version]:  # noqa: C901
    """Read bounded selected local versions without claiming source provenance or starting target code.

    Raises:
        HeinzelError: If metadata or startup paths cannot be observed safely.
    """
    result: dict[str, Version] = {}
    sites = list(islice(prefix.glob("lib/python*/site-packages"), MAX_SITE_DIRS + 1))
    if not sites or len(sites) > MAX_SITE_DIRS:
        raise HeinzelError(VERSION_ERROR)
    count = 0
    total = 0
    try:
        for site in sites:
            directory = os.open(site, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        count += 1
                        if count > MAX_SITE_ENTRIES or entry.name.endswith(".pth"):
                            raise HeinzelError(VERSION_ERROR)
                        if not entry.name.endswith(".dist-info"):
                            continue
                        if not entry.is_dir(follow_symlinks=False):
                            raise HeinzelError(VERSION_ERROR)
                        name, version, size = _metadata_version(directory, entry.name)
                        total += size
                        if total > MAX_METADATA_TOTAL:
                            raise HeinzelError(VERSION_ERROR)
                        if name in selected:
                            if name in result:
                                raise HeinzelError(VERSION_ERROR)
                            result[name] = version
            finally:
                os.close(directory)
    except (OSError, ValueError, TypeError) as exc:
        raise HeinzelError(VERSION_ERROR) from exc
    if not required.issubset(result):
        raise HeinzelError(VERSION_ERROR)
    return result


def _metadata_version(directory: int, name: str) -> tuple[str, Version, int]:
    """Read bounded, regular metadata through no-follow descriptors.

    Raises:
        HeinzelError: If metadata is unsafe or malformed.
        OSError: If metadata cannot be opened.
    """
    metadata_dir = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
    try:
        descriptor = os.open("METADATA", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=metadata_dir)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            content = stream.read(MAX_METADATA + 1)
            after = os.fstat(stream.fileno())
    finally:
        os.close(metadata_dir)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o022
        or len(content) > MAX_METADATA
        or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
    ):
        raise HeinzelError(VERSION_ERROR)
    headers = BytesParser().parsebytes(content, headersonly=True)
    names, versions = headers.get_all("Name", []), headers.get_all("Version", [])
    if (
        headers.defects
        or len(names) != 1
        or len(versions) != 1
        or len(str(names[0])) > MAX_HEADER_LENGTH
        or len(str(versions[0])) > MAX_HEADER_LENGTH
    ):
        raise HeinzelError(VERSION_ERROR)
    return canonicalize_name(str(names[0]), validate=True), Version(str(versions[0])), len(content)


def _verify_and_normalize(  # noqa: PLR0913, PLR0917 - bind all verification inputs to one candidate generation
    target: ToolTarget,
    expected: Mapping[str, tuple[str, str]],
    wheels: Mapping[str, Path],
    installation: Installation,
    preserved: Sequence[str],
    constraints: tuple[str, ...],
    generation: str,
) -> str:
    """Verify exact selected artifacts and startup paths before any target interpreter call.

    Raises:
        HeinzelError: If the selected generation or runtime cannot be verified.
    """
    requirements = [f"{name} @ {path.as_uri()}#sha256={expected[name][1]}" for name, path in wheels.items()]
    _verify_wheels(expected, wheels)
    installation.verify_installed_requirements(
        [*requirements, *preserved], expected_version=expected["heinzel"][0], constraints=constraints
    )
    verify_artifacts(target, expected)
    if installation.startup_fingerprint() != generation:
        msg = "The app changed during artifact verification; inspect the selected tool."
        raise HeinzelError(msg)
    generation = installation.remove_tool_overrides(expected=generation, overrides=wheels)
    version = installation.hz("--version")
    if not version.splitlines() or version.splitlines()[0].strip() != f"hz {expected['heinzel'][0]}":
        msg = "The selected app reported an unexpected version; inspect the selected tool."
        raise HeinzelError(msg)
    if installation.startup_fingerprint() != generation:
        msg = "The app changed during its runtime check; inspect the selected tool."
        raise HeinzelError(msg)
    return generation


def _verify_wheels(expected: Mapping[str, tuple[str, str]], wheels: Mapping[str, Path]) -> None:
    """Recheck the independently approved wheel bytes without following a replacement symlink.

    Raises:
        HeinzelError: If an artifact changed or is unsafe.
    """
    msg = "The approved app artifacts changed or are unsafe; inspect them before retrying."
    if set(expected) != set(wheels):
        raise HeinzelError(msg)
    for name, path in wheels.items():
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                before = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(before.st_mode)
                    or before.st_size > MAX_WHEEL
                    or before.st_uid != os.getuid()
                    or before.st_mode & 0o022
                ):
                    raise HeinzelError(msg)
                hasher = hashlib.sha256()
                remaining = MAX_WHEEL + 1
                while remaining:
                    chunk = stream.read(min(remaining, 65536))
                    if not chunk:
                        break
                    hasher.update(chunk)
                    remaining -= len(chunk)
                if not remaining:
                    raise HeinzelError(msg)
                digest = hasher.hexdigest()
                after = os.fstat(stream.fileno())
            if digest != expected[name][1] or (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise HeinzelError(msg)
        except OSError as exc:
            raise HeinzelError(msg) from exc


def inspection_guidance(target: ToolTarget, uv: str, reason: str) -> str:
    """Make retained partial state explicit without pointing a recovery command at an ambient tool."""
    scope = " ".join(f"{key}={shlex.quote(value)}" for key, value in target.environment.items())
    program = shlex.quote(uv)
    return (
        f"{reason}; partial state was kept for inspection. "
        f"Inspect the selected store {target.store} and executable directory {target.binary}. "
        f"List that store with `{scope} {program} tool list`. "
        "Only after confirming it is your incomplete installation, "
        f"remove it with `{scope} {program} tool uninstall heinzel`, then retry."
    )


def verify_artifacts(target: ToolTarget, expected: Mapping[str, tuple[str, str]]) -> None:
    """Read installed metadata without executing the target; require the approved wheel versions and hashes.

    Raises:
        HeinzelError: If the installed distributions do not identify the verified composition.
    """
    locations = [str(path) for path in target.prefix.glob("lib/python*/site-packages")]
    if (
        not expected
        or not locations
        or any(not Path(path).resolve().is_relative_to(target.prefix) for path in locations)
    ):
        msg = "The selected app has no verifiable private package directory."
        raise HeinzelError(msg)
    actual: dict[str, list[tuple[str, str | None]]] = {}
    try:
        for distribution in distributions(path=locations):
            name = distribution.metadata["Name"].lower().replace("_", "-")
            if name not in expected:
                continue
            direct = cast("dict[str, Any]", json.loads(distribution.read_text("direct_url.json") or "{}"))
            archive = direct.get("archive_info", {})
            digest = archive.get("hashes", {}).get("sha256") or archive.get("hash", "").removeprefix("sha256=")
            source = urlsplit(str(direct.get("url", "")))
            hashes = parse_qs(source.fragment).get("sha256", [])
            # uv retains an explicitly verified local wheel hash in its source URL, with empty archive_info.
            if not digest and len(hashes) == 1:
                digest = hashes[0]
            if source.scheme not in {"file", "https"} or "vcs_info" in direct or "dir_info" in direct:
                digest = None
            actual.setdefault(name, []).append((distribution.version, digest))
        matches = all(
            len(actual.get(name, [])) == 1
            and Version(actual[name][0][0]) == Version(version)
            and actual[name][0][1] == digest
            for name, (version, digest) in expected.items()
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        msg = "The installed app's artifact identity could not be verified; no successful app receipt was written."
        raise HeinzelError(msg) from exc
    if not matches:
        msg = (
            "The installed app does not match the approved artifact versions and hashes; inspect the selected uv store."
        )
        raise HeinzelError(msg)
