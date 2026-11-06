"""Validate the complete member graph before extracting an attested desktop bundle."""

import posixpath
import tarfile
import unicodedata
from pathlib import Path, PurePosixPath

from heinzel import HeinzelError

BUNDLE_NAME = "Heinzelmaennchen.app"
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_EXPANDED_BYTES = 1024 * 1024 * 1024
MAX_MEMBERS = 20_000


def _validate_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    entries: dict[str, tarfile.TarInfo] = {}
    aliases: set[str] = set()
    expanded = 0
    problem = "The desktop archive contains an unsafe or unsupported member."
    for member in archive:
        path = PurePosixPath(member.name)
        name = path.as_posix()
        alias = unicodedata.normalize("NFC", name).casefold()
        if (
            not path.parts
            or path.parts[0] != BUNDLE_NAME
            or ".." in path.parts
            or name != member.name.rstrip("/")
            or alias in aliases
            or member.mode & 0o7000
            or not (member.isfile() or member.isdir() or member.issym())
            or member.size < 0
        ):
            raise HeinzelError(problem)
        if member.issym():
            link = member.linkname
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), link))
            if (
                not link
                or ".." in PurePosixPath(link).parts
                or (resolved != BUNDLE_NAME and not resolved.startswith(f"{BUNDLE_NAME}/"))
            ):
                raise HeinzelError(problem)
        expanded += member.size
        if expanded > MAX_EXPANDED_BYTES or len(entries) >= MAX_MEMBERS:
            raise HeinzelError(problem)
        entries[name] = member
        aliases.add(alias)
    root = entries.get(BUNDLE_NAME)
    if root is None or not root.isdir():
        raise HeinzelError(problem)
    _validate_parents(entries, problem)
    return list(entries.values())


def _validate_parents(entries: dict[str, tarfile.TarInfo], problem: str) -> None:
    for name in entries:
        for parent in PurePosixPath(name).parents:
            if parent == PurePosixPath():
                continue
            entry = entries.get(parent.as_posix())
            if entry is None or not entry.isdir():
                raise HeinzelError(problem)


def extract_bundle(archive_path: Path, staging: Path) -> Path:
    """Extract a validated archive into an empty, private directory controlled by the caller.

    Raises:
        HeinzelError: If archive members are unsafe or the bundle cannot be extracted.
    """
    try:
        if archive_path.stat().st_size > MAX_ARCHIVE_BYTES or any(staging.iterdir()):
            msg = "The desktop archive or its staging directory exceeds the extraction policy."
            raise HeinzelError(msg)
        with tarfile.open(archive_path, "r:gz") as archive:
            members = _validate_members(archive)
            archive.extractall(staging, members=members, filter="data")
        return staging / BUNDLE_NAME
    except (tarfile.TarError, OSError, ValueError) as exc:
        msg = "The desktop archive could not be extracted safely."
        raise HeinzelError(msg) from exc
