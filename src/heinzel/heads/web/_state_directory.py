"""Private web state shared by pairing and the updater, with a narrow legacy migration."""

import os
import stat
from collections.abc import Generator
from contextlib import contextmanager, suppress
from pathlib import Path

from heinzel.kernel import CurrentUser, HeinzelError

UPDATER_FILES = frozenset({"auto-update.json", "auto-update.lock", "auto-update-apply.lock"})
PRIVATE_DIRECTORY = 0o700


def _owned_read_only_to_others(info: os.stat_result, *, directory: bool) -> bool:
    kind = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    mode = stat.S_IMODE(info.st_mode)
    return kind and info.st_uid == os.getuid() and not mode & ~0o755 and (directory or info.st_nlink == 1)


def _migrate_updater_only(folder: int) -> None:
    """Tighten only an owned, non-writable legacy updater directory without credential state.

    Raises:
        HeinzelError: The directory or any entry is not recognizable safe legacy updater state.
    """
    info = os.fstat(folder)
    names = os.listdir(folder)
    if not _owned_read_only_to_others(info, directory=True) or not set(names) <= UPDATER_FILES:
        msg = "The web state directory cannot be migrated safely"
        raise HeinzelError(msg)
    for name in names:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
        try:
            if not _owned_read_only_to_others(os.fstat(descriptor), directory=False):
                msg = "The legacy updater state is not an owned, unlinked regular file"
                raise HeinzelError(msg)
        finally:
            os.close(descriptor)
    os.fchmod(folder, PRIVATE_DIRECTORY)
    os.fsync(folder)


@contextmanager
def private_directory(directory: Path, *, migrate_updater: bool = False) -> Generator[int]:
    """Open private state by descriptor; credential readers never repair unsafe permissions.

    Yields:
        The verified private directory descriptor, valid until the context exits.

    Raises:
        HeinzelError: Existing state is unsafe or contains credentials that cannot be migrated.
        OSError: A path cannot be opened without following a link.
    """
    directory.parent.mkdir(parents=True, exist_ok=True)
    parent = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        with suppress(FileExistsError):
            os.mkdir(directory.name, mode=PRIVATE_DIRECTORY, dir_fd=parent)
        folder = os.open(directory.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            if migrate_updater and stat.S_IMODE(os.fstat(folder).st_mode) != PRIVATE_DIRECTORY:
                if not _owned_read_only_to_others(os.fstat(parent), directory=True):
                    msg = "The web state parent cannot be trusted for migration"
                    raise HeinzelError(msg)
                _migrate_updater_only(folder)
            CurrentUser.verify_private(os.fstat(folder), directory=True)
            yield folder
        finally:
            os.close(folder)
    finally:
        os.close(parent)
