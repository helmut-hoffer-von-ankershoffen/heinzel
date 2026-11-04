"""Remove one continuity-verified uv tool offline; never resolve, install or restore packages."""

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from heinzel.kernel._errors import HeinzelError


def physical_directory(path: Path) -> None:
    """Reject redirected, foreign or writable removal authorities.

    Raises:
        HeinzelError: If a path component is not physically safe.
        OSError: If the path is unavailable.
    """
    if not path.is_absolute() or path.resolve(strict=True) != path:
        msg = "The Heinzel installation path is redirected; inspect it locally before removing it."
        raise HeinzelError(msg)
    for parent in (path, *path.parents):
        info = parent.lstat()
        sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid not in {0, os.getuid()}
            or (info.st_mode & 0o022 and not sticky_root)
        ):
            msg = "The Heinzel installation path has unsafe ownership or permissions."
            raise HeinzelError(msg)
    if path.stat().st_uid != os.getuid():
        msg = "The Heinzel installation directory is not owned by you."
        raise HeinzelError(msg)


def uv_identity(executable: str) -> tuple[Path, tuple[int, ...]]:
    """Pin an existing owned executable; no bootstrap or network lookup is permitted.

    Raises:
        HeinzelError: If uv is not a safely owned executable.
        OSError: If uv cannot be inspected.
    """
    path = Path(executable).resolve(strict=True)
    for parent in path.parents:
        info = parent.lstat()
        sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if info.st_uid not in {0, os.getuid()} or (info.st_mode & 0o022 and not sticky_root):
            msg = "The local uv executable has an unsafe parent directory."
            raise HeinzelError(msg)
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid not in {0, os.getuid()}
        or info.st_mode & 0o022
        or not info.st_mode & 0o111
    ):
        msg = "The local uv executable is not trusted; repair uv locally before uninstalling Heinzel."
        raise HeinzelError(msg)
    return path, (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@dataclass(frozen=True)
class OwnedFile:
    """One physical file's bytes and identity, captured before any service or tool mutation."""

    path: Path
    content: bytes
    identity: tuple[int, ...]


def owned_file(path: Path) -> OwnedFile:
    physical_directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o022
            or info.st_size > 1024 * 1024
        ):
            msg = "A Heinzel service file has unsafe ownership or permissions; it was left unchanged."
            raise HeinzelError(msg)
        content = os.read(fd, 1024 * 1024 + 1)
        after = os.fstat(fd)
        identity = (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_uid,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )
        if len(content) != info.st_size or identity != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_uid,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            msg = "A Heinzel service file changed during inspection; it was left unchanged."
            raise HeinzelError(msg)
        return OwnedFile(path, content, identity)
    finally:
        os.close(fd)
