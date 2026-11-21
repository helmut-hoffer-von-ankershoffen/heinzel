"""The daemon's handover files: physical paths and files opened only through trusted directories."""

import os
import stat
import sys
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import BinaryIO, Never

from heinzel import HeinzelError


def _handover_absolute_path(path: Path) -> Path:
    absolute = path if path.is_absolute() else Path.cwd() / path
    if ".." in absolute.parts:
        invalid_handover()
    # These root-owned aliases are part of macOS's filesystem layout. Other symlinks
    # are refused, including aliases in user-controlled directories.
    if sys.platform == "darwin" and len(absolute.parts) > 1 and absolute.parts[1] in {"tmp", "var", "etc"}:
        alias = Path("/") / absolute.parts[1]
        info = os.lstat(alias)
        if not stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or alias.readlink() != Path(f"private/{alias.name}"):
            invalid_handover()
        absolute = Path("/private") / absolute.relative_to("/")
    return absolute


def _check_handover_directory(descriptor: int) -> None:
    info = os.fstat(descriptor)
    sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
    if info.st_uid not in (0, os.getuid()) or (info.st_mode & 0o022 and not sticky_root):
        invalid_handover()


@contextmanager
def _open_verified_handover_file(path: Path) -> Generator[BinaryIO]:
    """Open through trusted directories.

    Yields:
        The opened file while directory and entry identities remain checked.
    """
    parts = _handover_absolute_path(path).parts[1:]
    with ExitStack() as stack:
        root = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, root)
        parents: list[tuple[int, int | None, str | None]] = [(root, None, None)]
        current = root
        for name in parts[:-1]:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            stack.callback(os.close, child)
            parents.append((child, current, name))
            current = child
        for directory, _, _ in parents:
            _check_handover_directory(directory)
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current)
        with os.fdopen(descriptor, "rb") as handle:
            yield handle
            for directory, _, _ in parents:
                _check_handover_directory(directory)
            for child, parent, child_name in parents[1:]:
                if parent is None or child_name is None:
                    invalid_handover()
                current_info = os.stat(child_name, dir_fd=parent, follow_symlinks=False)
                initial = os.fstat(child)
                if (current_info.st_dev, current_info.st_ino) != (initial.st_dev, initial.st_ino):
                    invalid_handover()
            entry = os.stat(parts[-1], dir_fd=current, follow_symlinks=False)
            file_info = os.fstat(handle.fileno())
            if (entry.st_dev, entry.st_ino) != (file_info.st_dev, file_info.st_ino):
                invalid_handover()


def safely_absent(path: Path) -> bool:
    """Confirm absence through physical, trusted parents without following directory links."""
    try:
        parts = _handover_absolute_path(path).parts[1:]
        with ExitStack() as stack:
            current = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, current)
            _check_handover_directory(current)
            for part in parts[:-1]:
                try:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
                except FileNotFoundError:
                    return True
                stack.callback(os.close, child)
                _check_handover_directory(child)
                current = child
            try:
                os.stat(parts[-1], dir_fd=current, follow_symlinks=False)
            except FileNotFoundError:
                return True
            return False
    except (OSError, HeinzelError):
        return False


def invalid_handover() -> Never:
    msg = "The existing daemon unit could not be verified; no handover was requested."
    raise HeinzelError(msg)


def handover_absolute_path(path: Path) -> Path:
    """Resolve the existing physical handover path for sibling recovery code."""
    return _handover_absolute_path(path)


@contextmanager
def verified_handover_file(path: Path) -> Generator[BinaryIO]:
    """Open a file through the existing bounded handover authority boundary.

    Yields:
        The physically verified bounded handover file handle.
    """
    with _open_verified_handover_file(path) as handle:
        yield handle
