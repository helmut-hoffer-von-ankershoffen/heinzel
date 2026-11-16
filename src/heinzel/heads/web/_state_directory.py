"""Private web state shared by pairing and the updater; existing unsafe state is never repaired online."""

import os
import stat
from collections.abc import Generator
from contextlib import contextmanager, suppress
from pathlib import Path

from heinzel.kernel import CurrentUser, HeinzelError

PRIVATE_DIRECTORY = 0o700


def _owned_read_only_to_others(info: os.stat_result, *, directory: bool) -> bool:
    kind = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    mode = stat.S_IMODE(info.st_mode)
    return kind and info.st_uid == os.getuid() and not mode & ~0o755 and (directory or info.st_nlink == 1)


def _verify_updater_entry(info: os.stat_result) -> None:
    """Reject updater entries that could redirect reads or writes outside owned state.

    Raises:
        HeinzelError: The entry is not owned, regular, unlinked and non-writable by others.
    """
    if not _owned_read_only_to_others(info, directory=False):
        msg = "The updater state must be owned, regular, unlinked and not writable by others"
        raise HeinzelError(msg)


@contextmanager
def private_directory(directory: Path) -> Generator[int]:
    """Open private state by descriptor; credential readers never repair unsafe permissions.

    An updater-only directory scan cannot exclude an uncooperative legacy writer
    adding credentials before chmod. Advisory locks cannot make that predicate
    atomic, so existing nonprivate directories require separately verified offline
    recovery. Online readers and writers never chmod or replace them.

    Yields:
        The verified private directory descriptor, valid until the context exits.

    Raises:
        HeinzelError: Existing state or its parent is unsafe.
        OSError: A path cannot be opened without following a link.
    """
    directory.parent.mkdir(parents=True, exist_ok=True)
    parent = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if not _owned_read_only_to_others(os.fstat(parent), directory=True):
            msg = "The web state parent is not trusted"
            raise HeinzelError(msg)
        with suppress(FileExistsError):
            os.mkdir(directory.name, mode=PRIVATE_DIRECTORY, dir_fd=parent)
        folder = os.open(directory.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            CurrentUser.verify_private(os.fstat(folder), directory=True)
            yield folder
        finally:
            os.close(folder)
    finally:
        os.close(parent)


def open_updater_file(folder: int, name: str, flags: int) -> int:
    """Open updater state relative to a verified directory, retaining safe legacy file modes.

    Raises:
        HeinzelError: An updater entry is not owned, regular, unlinked and non-writable by others.
        OSError: The entry cannot be opened without following links.
    """
    protected = flags | os.O_NOFOLLOW | os.O_NONBLOCK
    if flags & os.O_CREAT and not flags & os.O_EXCL:
        try:
            descriptor = os.open(name, protected | os.O_EXCL, 0o600, dir_fd=folder)
        except FileExistsError:
            descriptor = os.open(name, protected & ~os.O_CREAT, dir_fd=folder)
    else:
        descriptor = os.open(name, protected, 0o600, dir_fd=folder)
    try:
        _verify_updater_entry(os.fstat(descriptor))
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor
