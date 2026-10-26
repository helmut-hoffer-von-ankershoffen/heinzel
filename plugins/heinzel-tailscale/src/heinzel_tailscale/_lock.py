"""One private lock serializes sharing actions across web, CLI and stdio MCP processes."""

import fcntl
import os
import stat
from collections.abc import Generator
from contextlib import contextmanager

from heinzel import HeinzelError, Settings


class SharingLock:
    """Serialize explicit actions without creating a scheduler or backend registry."""

    def __init__(self, settings: Settings) -> None:
        """Use only the owner's configured state folder."""
        self.directory = settings.state_dir / "tailscale"

    @contextmanager
    def hold(self) -> Generator[None]:
        """Hold one owner-only regular lock file.

        Yields:
            Nothing while this process owns the action lock.

        Raises:
            HeinzelError: If the lock folder or file is unsafe or unavailable.
        """
        folder = descriptor = -1
        try:
            try:
                self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                folder = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                _private(os.fstat(folder), directory=True)
                descriptor = os.open("sharing.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=folder)
                _private(os.fstat(descriptor), directory=False)
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            except OSError as error:
                msg = "The sharing action lock could not be opened safely; inspect the private state folder."
                raise HeinzelError(msg) from error
            yield
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if folder >= 0:
                os.close(folder)


def _private(info: os.stat_result, *, directory: bool) -> None:
    correct = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not correct or info.st_uid != os.getuid() or info.st_mode & 0o077 or (not directory and info.st_nlink != 1):
        msg = "The sharing state must be one private owner-only folder and regular lock file."
        raise HeinzelError(msg)
