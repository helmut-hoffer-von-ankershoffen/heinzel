"""Read a selected tool's distribution metadata without starting its interpreter."""

import hashlib
import json
import os
import re
import stat
from collections.abc import Sequence
from importlib.machinery import EXTENSION_SUFFIXES
from importlib.metadata import distributions
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit

from packaging.requirements import Requirement
from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename
from packaging.version import Version

from heinzel.kernel._errors import HeinzelError

_MAX_PTH_FILES = 128
_MAX_PTH_BYTES = 65_536
_MAX_EXTENSIONS = 256
_MAX_PATH_BYTES = 4096


class _StartupPathError(ValueError):
    """The tool has Python startup paths that cannot be inspected safely."""


def _source_matches(requirement: Requirement, direct: dict[str, object] | None) -> bool:
    if requirement.url is None:
        return direct is None
    if direct is None:
        return False
    if urlsplit(requirement.url).scheme.startswith("git+"):
        return _git_source_matches(requirement.url, direct)
    return _archive_source_matches(requirement.url, direct)


def _archive_source_matches(url: str, direct: dict[str, object]) -> bool:
    selected = urlsplit(url)
    expected = urlunsplit((selected.scheme, selected.netloc, selected.path, selected.query, ""))
    actual_url = direct.get("url")
    if not isinstance(actual_url, str):
        return False
    actual = urlsplit(actual_url)
    if urlunsplit((actual.scheme, actual.netloc, actual.path, actual.query, "")) != expected:
        return False
    hashes = parse_qs(selected.fragment).get("sha256", [])
    if not hashes:
        return not selected.fragment
    return len(hashes) == 1 and _archive_hash_matches(hashes[0], actual.fragment, direct)


def _archive_hash_matches(expected: str, fragment: str, direct: dict[str, object]) -> bool:
    if not re.fullmatch(r"[a-fA-F0-9]{64}", expected):
        return False
    if parse_qs(fragment).get("sha256", []) == [expected]:
        return True
    archive = direct.get("archive_info")
    if not isinstance(archive, dict):
        return False
    archive = cast("dict[str, object]", archive)
    values = archive.get("hashes")
    if isinstance(values, dict):
        values = cast("dict[str, object]", values)
        return values.get("sha256") == expected
    return archive.get("hash") == f"sha256={expected}"


def _git_source_matches(url: str, direct: dict[str, object]) -> bool:
    expected = urlsplit(url.removeprefix("git+"))
    path, separator, revision = expected.path.rpartition("@")
    if not separator:
        path, revision = expected.path, ""
    expected_url = urlunsplit((expected.scheme, expected.netloc, path, "", ""))
    vcs = direct.get("vcs_info")
    if not isinstance(vcs, dict):
        return False
    vcs = cast("dict[str, object]", vcs)
    if vcs.get("vcs") != "git" or direct.get("url") != expected_url:
        return False
    commit = vcs.get("commit_id")
    if not isinstance(commit, str) or not re.fullmatch(r"[a-f0-9]{40,64}", commit):
        return False
    commit_matches = bool(re.fullmatch(r"[a-f0-9]{7,64}", revision)) and commit.startswith(revision)
    subdirectories = parse_qs(expected.fragment).get("subdirectory", [])
    subdirectory = subdirectories[0] if subdirectories else None
    return (not revision or vcs.get("requested_revision") == revision or commit_matches) and direct.get(
        "subdirectory"
    ) == subdirectory


def inspect_requirements(
    prefix: Path,
    requirements: list[str],
    expected_version: str | None,
    *,
    forbidden: tuple[str, ...] = (),
    constraints: tuple[str, ...] = (),
) -> dict[str, str]:
    """Check the selected distributions' names, versions and declared sources, without executing target code.

    Raises:
        HeinzelError: If metadata is missing, ambiguous or does not match the selected requirements.
    """
    try:
        return _inspect(prefix, requirements, expected_version, forbidden, constraints)
    except _StartupPathError as exc:
        msg = (
            "Automatic update cannot safely inspect this tool's Python startup paths; inspect the installation "
            "or reinstall it from verified sources without executable or external startup hooks."
        )
        raise HeinzelError(msg) from exc
    except (OSError, ValueError, KeyError, TypeError) as exc:
        msg = "The installed tool metadata does not match the selected sources and versions; no code was started."
        raise HeinzelError(msg) from exc


def inspect_core_version(prefix: Path) -> str:
    """Observe one installed core version without asserting its source or starting its interpreter.

    Raises:
        HeinzelError: If startup paths or core metadata cannot be safely inspected.
    """
    try:
        locations = _locations(prefix)
        matches = _selected_metadata(locations, {"heinzel"}, set()).get("heinzel", [])
        version = _one_core_version(matches)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        msg = "The installed core version cannot be safely observed; no code was started."
        raise HeinzelError(msg) from exc
    else:
        return version


def inspect_versions(prefix: Path, names: Sequence[str]) -> dict[str, str]:
    """Observe the installed version of each distribution that has exactly one, without starting its interpreter.

    Raises:
        HeinzelError: If startup paths or metadata cannot be safely inspected.
    """
    wanted = {str(canonicalize_name(name)) for name in names}
    try:
        found = _selected_metadata(_locations(prefix), wanted, set())
        return {name: _one_core_version(matches) for name, matches in found.items() if matches}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        msg = "The installed plugin versions cannot be safely observed; no code was started."
        raise HeinzelError(msg) from exc


def _one_core_version(matches: list[tuple[str, dict[str, object] | None]]) -> str:
    if len(matches) != 1:
        raise ValueError
    version = matches[0][0]
    Version(version)
    return version


def _locations(prefix: Path) -> list[Path]:
    locations = list(prefix.glob("lib/python*/site-packages"))
    if not locations or any(
        not path.resolve(strict=True).is_relative_to(prefix.resolve(strict=True)) for path in locations
    ):
        raise ValueError
    return _startup_locations(prefix.resolve(strict=True), locations)


def _inspect(
    prefix: Path,
    requirements: list[str],
    expected_version: str | None,
    forbidden: tuple[str, ...],
    constraints: tuple[str, ...],
) -> dict[str, str]:
    wanted = [Requirement(item) for item in requirements]
    names = [canonicalize_name(item.name) for item in wanted]
    excluded: set[str] = {canonicalize_name(item) for item in forbidden}
    if not names or len(names) != len(set(names)):
        raise ValueError
    try:
        locations = _locations(prefix)
    except (OSError, UnicodeError) as exc:
        raise _StartupPathError from exc
    found = _selected_metadata(locations, set(names), excluded)
    result: dict[str, str] = {}
    policies = _requirement_policies(requirements, constraints)
    for (requirement, applicable, source), name in zip(policies, names, strict=True):
        matches = found.get(name, [])
        if len(matches) != 1:
            raise ValueError
        version, direct = matches[0]
        if (
            Version(version) not in requirement.specifier
            or any(Version(version) not in item.specifier for item in applicable)
            or not _source_matches(source, direct)
        ):
            raise ValueError
        result[name] = version
    if expected_version is not None and Version(result["heinzel"]) != Version(expected_version):
        raise ValueError
    return result


def preserved_wheel_constraints(prefix: Path, requirements: list[str]) -> tuple[str, ...]:
    """Capture hash-bound wheel sources of unchanged index requirements; the caller supplies continuity authority.

    This does not approve an installation. Only a previously verified startup checkpoint may authorize carrying
    these sources into a mutation. New or replaced requirements must be excluded by the caller.

    Raises:
        HeinzelError: If selected metadata is ambiguous or its wheel identity is inconsistent.
    """
    try:
        return _preserved_wheel_constraints(prefix, requirements)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        msg = "The unchanged tool wheel sources cannot be preserved safely; no package change was started."
        raise HeinzelError(msg) from exc


def _preserved_wheel_constraints(prefix: Path, requirements: list[str]) -> tuple[str, ...]:
    wanted = [Requirement(item) for item in requirements]
    names: set[str] = {canonicalize_name(item.name) for item in wanted}
    found = _selected_metadata(_locations(prefix), names, set())
    result: list[str] = []
    for requirement in wanted:
        if requirement.url is not None:
            continue
        name = canonicalize_name(requirement.name)
        matches = found.get(name, [])
        if len(matches) != 1:
            raise ValueError
        version, direct = matches[0]
        if Version(version) not in requirement.specifier:
            raise ValueError
        if direct is None:
            continue
        if set(direct) != {"url", "archive_info"} or not isinstance(direct.get("archive_info"), dict):
            raise ValueError
        url = direct.get("url")
        if not isinstance(url, str):
            raise TypeError
        source = urlsplit(url)
        hashes = parse_qs(source.fragment).get("sha256", [])
        if (
            source.scheme != "file"
            or source.netloc
            or source.query
            or not source.path.startswith("/")
            or source.username
            or source.password
            or len(hashes) != 1
            or not re.fullmatch(r"[a-f0-9]{64}", hashes[0])
            or source.fragment != f"sha256={hashes[0]}"
        ):
            raise ValueError
        wheel_name, wheel_version, _, _ = parse_wheel_filename(unquote(source.path.rsplit("/", 1)[-1]))
        if wheel_name != name or wheel_version != Version(version) or not _archive_source_matches(url, direct):
            raise ValueError
        result.append(f"{name} @ {url}")
    return tuple(result)


def validate_constraint_policy(requirements: list[str], constraints: tuple[str, ...]) -> None:
    """Refuse ambiguous selected sources and impossible pinned wheel versions before any tool mutation.

    Raises:
        HeinzelError: If selected requirement and constraint policy cannot be safely combined.
    """
    try:
        names = [canonicalize_name(Requirement(item).name) for item in requirements]
    except ValueError as exc:
        msg = "The selected tool requirements are invalid; inspect them before retrying."
        raise HeinzelError(msg) from exc
    if len(set(names)) != len(names):
        msg = "The selected tool requirements name the same distribution more than once; inspect them before retrying."
        raise HeinzelError(msg)
    try:
        _requirement_policies(requirements, constraints)
    except ValueError as exc:
        msg = (
            "The selected requirements conflict with existing constraints. Inspect existing constraints; "
            "to change them intentionally, choose an explicit uv command."
        )
        raise HeinzelError(msg) from exc


def _local_wheel_identity(candidate: Requirement) -> tuple[Path, str | None]:
    """Read one canonical absolute local wheel identity.

    Raises:
        ValueError: If the URL or hash fragment is ambiguous or unsupported.
    """
    raw_base, separator, fragment = (candidate.url or "").partition("#")
    if separator and not fragment:
        raise ValueError
    source = urlsplit(candidate.url or "")
    if source.scheme != "file" or source.netloc or source.query or not source.path.startswith("/"):
        raise ValueError
    path = Path(unquote(source.path))
    if ".." in path.parts or path.as_uri() != raw_base:
        raise ValueError
    name, _, _, _ = parse_wheel_filename(path.name)
    if name != canonicalize_name(candidate.name):
        raise ValueError
    if source.fragment and not re.fullmatch(r"sha256=[a-f0-9]{64}", source.fragment):
        raise ValueError
    return path, source.fragment.removeprefix("sha256=") if source.fragment else None


def _wheel_file_identity(info: os.stat_result) -> tuple[int, ...]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _verify_local_wheel_hash(wheel: Path, digest: str) -> None:
    """Verify stable owned source bytes without following an ambiguous or nonregular file.

    Raises:
        ValueError: If the selected wheel is missing, changed, foreign-owned or unsafe to read.
    """
    try:
        if wheel.resolve(strict=True) != wheel or not wheel.is_file():
            raise ValueError
        with os.fdopen(os.open(wheel, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or before.st_mode & 0o022:
                raise ValueError
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != digest or _wheel_file_identity(os.fstat(stream.fileno())) != _wheel_file_identity(before):
                raise ValueError
            if _wheel_file_identity(wheel.lstat()) != _wheel_file_identity(before):
                raise ValueError
    except OSError as exc:
        raise ValueError from exc


def _reconciled_wheel_source(direct: list[Requirement]) -> Requirement:
    """Reconcile uv's fragmentless local receipt path with the same verified hash-bound wheel.

    Raises:
        ValueError: If more than this exact serialization difference is present, or the wheel differs.
    """
    identities = [_local_wheel_identity(candidate) for candidate in direct]
    paths = {path for path, _ in identities}
    hashed = [
        (candidate, digest) for candidate, (_, digest) in zip(direct, identities, strict=True) if digest is not None
    ]
    if len(paths) != 1 or len({digest for _, digest in hashed}) != 1:
        raise ValueError
    selected, digest = hashed[0]
    _verify_local_wheel_hash(next(iter(paths)), digest)
    return selected


def _requirement_policies(
    requirements: list[str], constraints: tuple[str, ...]
) -> list[tuple[Requirement, list[Requirement], Requirement]]:
    parsed = [Requirement(item) for item in constraints]
    policies: list[tuple[Requirement, list[Requirement], Requirement]] = []
    for item in requirements:
        requirement = Requirement(item)
        name = canonicalize_name(requirement.name)
        relevant = [candidate for candidate in parsed if canonicalize_name(candidate.name) == name]
        if requirement.marker is not None or any(candidate.marker is not None for candidate in relevant):
            raise ValueError
        direct = [candidate for candidate in [requirement, *relevant] if candidate.url is not None]
        source = (
            _reconciled_wheel_source(direct)
            if len({candidate.url for candidate in direct}) > 1
            else direct[0]
            if direct
            else requirement
        )
        versions = [
            Version(spec.version)
            for candidate in [requirement, *relevant]
            for spec in candidate.specifier
            if spec.operator == "==" and not spec.version.endswith(".*")
        ]
        if source.url is not None:
            try:
                wheel_name, version, _, _ = parse_wheel_filename(unquote(urlsplit(source.url).path.rsplit("/", 1)[-1]))
            except InvalidWheelFilename:
                pass
            else:
                if wheel_name != name:
                    raise ValueError
                versions.append(version)
        if any(version not in candidate.specifier for version in versions for candidate in [requirement, *relevant]):
            raise ValueError
        policies.append((requirement, relevant, source))
    return policies


def _startup_locations(prefix: Path, sites: list[Path]) -> list[Path]:
    """Inspect site path extensions without executing any Python startup code.

    Raises:
        _StartupPathError: If a startup extension is unsupported or unsafe.
    """
    result: list[Path] = list(sites)
    for pth in _pth_paths(sites):
        for line in _read_pth(pth):
            if not line or line.startswith("#"):
                continue
            resolved = _physical_extension(prefix, pth, line)
            if resolved not in result:
                result.append(resolved)
            if len(result) > _MAX_EXTENSIONS:
                raise _StartupPathError
    suffixes = (".py", ".pyc", *EXTENSION_SUFFIXES)
    for location in result:
        for name in ("sitecustomize", "usercustomize"):
            if (location / name).exists() or any((location / (name + suffix)).exists() for suffix in suffixes):
                raise _StartupPathError
    return result


def _pth_paths(sites: list[Path]) -> list[Path]:
    pth_files: list[Path] = []
    for site in sites:
        for path in site.glob("*.pth"):
            pth_files.append(path)
            if len(pth_files) > _MAX_PTH_FILES:
                raise _StartupPathError
    return sorted(pth_files)


def _physical_extension(prefix: Path, pth: Path, line: str) -> Path:
    if line.startswith(("import ", "import\t")) or len(os.fsencode(line)) > _MAX_PATH_BYTES:
        raise _StartupPathError
    candidate = pth.parent / line
    if ".." in candidate.parts:
        raise _StartupPathError
    resolved = candidate.resolve(strict=True)
    if resolved != candidate or not resolved.is_relative_to(prefix) or not resolved.is_dir():
        raise _StartupPathError
    return resolved


def _read_pth(path: Path) -> list[str]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_PTH_BYTES:
            raise _StartupPathError
        data = os.read(descriptor, _MAX_PTH_BYTES + 1)
        after = os.fstat(descriptor)
        if len(data) != info.st_size or (
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise _StartupPathError
        return [line.rstrip() for line in data.decode("utf-8").splitlines()]
    finally:
        os.close(descriptor)


def _selected_metadata(
    locations: list[Path], names: set[str], excluded: set[str]
) -> dict[str, list[tuple[str, dict[str, object] | None]]]:
    found: dict[str, list[tuple[str, dict[str, object] | None]]] = {}
    for dist in distributions(path=[str(path) for path in locations]):
        name = canonicalize_name(dist.metadata["Name"])
        if name in excluded:
            raise ValueError
        if name in names:
            raw = dist.read_text("direct_url.json")
            direct = json.loads(raw) if raw is not None else None
            if direct is not None and not isinstance(direct, dict):
                raise ValueError
            found.setdefault(name, []).append((dist.version, cast("dict[str, object] | None", direct)))
    return found
