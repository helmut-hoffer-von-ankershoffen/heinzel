"""Process-held command barrier; closing one inherited copy never revokes another process's lease."""

import fcntl
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

from heinzel import HeinzelError


@dataclass(slots=True)
class WriterLease:
    """Own one shared or exclusive OS descriptor until execution finishes or the process exits."""

    descriptor: int

    def close(self) -> None:
        """Release this descriptor only; a forwarded child may still own the same open-file description."""
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1

    def __enter__(self) -> Self:
        """Keep the lease alive while its caller executes."""
        return self

    def __exit__(
        self, _kind: type[BaseException] | None, _error: BaseException | None, _trace: TracebackType | None
    ) -> None:
        """Release normally and on exceptional exit without explicitly unlocking a child's inherited descriptor."""
        self.close()


def writer_lease(directory: Path, *, exclusive: bool = False) -> WriterLease:
    """Acquire an owner-private nonblocking process barrier beneath the already locked lifecycle directory.

    Lock order is lifecycle authority, writer barrier, then backend ownership. Never take a shared lease for the
    private transition authority itself: it must be able to acquire the exclusive barrier without self-deadlock.

    Raises:
        HeinzelError: If commands remain active or the lock file is unsafe.
    """
    descriptor: int | None = None
    acquired = False
    try:
        path = directory / "writers.lock"
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
        value = os.fstat(descriptor)
        if (
            not stat.S_ISREG(value.st_mode)
            or value.st_uid != os.getuid()
            or value.st_mode & 0o077
            or value.st_nlink != 1
        ):
            msg = "The managed command writer barrier is unsafe; repair this desktop profile."
            raise HeinzelError(msg)
        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(descriptor, mode | fcntl.LOCK_NB)
        acquired = True
        return WriterLease(descriptor)
    except OSError:
        msg = "Managed commands are still running, or their private writer barrier is unsafe; finish them and retry."
        raise HeinzelError(msg) from None
    finally:
        if descriptor is not None and not acquired:
            os.close(descriptor)
