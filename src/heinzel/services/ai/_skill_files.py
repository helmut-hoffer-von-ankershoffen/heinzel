"""Read and fingerprint the local files that a skill ships."""

import hashlib
import os
import stat
from pathlib import Path, PurePosixPath

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)


def fingerprint(contents: dict[str, bytes]) -> str:
    """SHA-256 over sorted, length-prefixed paths and contents, so no two different skills can share one."""
    digest = hashlib.sha256()
    for path in sorted(contents):
        encoded = path.encode("utf-8")
        digest.update(f"{len(encoded)}:".encode())
        digest.update(encoded)
        digest.update(f"{len(contents[path])}:".encode())
        digest.update(contents[path])
    return digest.hexdigest()


def read_beneath(root: Path, path: str, limit: int, where: str) -> bytes:
    """The content of `path` below `root`, opening each component without following a link; at most `limit` bytes.

    Each directory is opened relative to its parent's descriptor, so a component swapped for a link after the check
    can't lead out of `root`; the file must be a regular file.

    Raises:
        ValueError: If `path` leaves `root`, a component is a link or no directory, or the file is too large.
    """
    parts = PurePosixPath(path).parts
    if not parts or path.startswith("/") or any(part in {"", ".", ".."} for part in path.split("/")):
        msg = f"{where}: {path!r} isn't a path below the skill's directory"
        raise ValueError(msg)
    try:
        content = _read_parts(root, parts, limit + 1)
    except OSError as exc:
        msg = f"{where}: {path} can't be read ({exc.strerror or type(exc).__name__})"
        raise ValueError(msg) from exc
    if len(content) > limit:
        msg = f"{where}: {path} is larger than {limit // 1024} KB"
        raise ValueError(msg)
    return content


def _read_parts(root: Path, parts: tuple[str, ...], limit: int) -> bytes:
    if os.open not in os.supports_dir_fd:  # pragma: no cover - Windows: no descriptor-relative open
        target = root.joinpath(*parts)
        if any(_is_link(root.joinpath(*parts[:index])) for index in range(1, len(parts) + 1)):
            raise OSError(0, "a symbolic link")
        return _read_regular(os.open(target, os.O_RDONLY | _NOFOLLOW | _NONBLOCK), limit)
    directory = os.open(root, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
    try:
        for part in parts[:-1]:
            inner = os.open(part, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = inner
        return _read_regular(os.open(parts[-1], os.O_RDONLY | _NOFOLLOW | _NONBLOCK, dir_fd=directory), limit)
    finally:
        os.close(directory)


def _read_regular(descriptor: int, limit: int) -> bytes:
    with os.fdopen(descriptor, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise OSError(0, "not a regular file")
        return handle.read(limit)


def _is_link(path: Path) -> bool:
    try:
        return stat.S_ISLNK(path.lstat().st_mode)
    except OSError:
        return False


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


# Preserve the old qualified names for existing pickles and introspection.
for _function in (fingerprint, read_beneath, _read_parts, _read_regular, _is_link, _sha):
    _function.__module__ = "heinzel.kernel.ai._skills"
