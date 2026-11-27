"""The guarded state folder the plan is saved in, and the lock that lets one server at a time write it."""

import contextlib
import fcntl
import os
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from heinzel import HeinzelError


@dataclass(frozen=True, slots=True)
class StateFiles:
    """The guarded state folder the plan lives in: the client context's checks, handed in by the web head."""

    private_directory: Callable[[Path], AbstractContextManager[int]]
    """Open and tighten a directory; yields its verified descriptor."""
    open_updater_file: Callable[[int, str, int], int]
    """Open an updater file relative to a verified directory descriptor."""


@contextmanager
def locked(path: Path, files: StateFiles) -> Generator[int | None]:
    """Lock one verified directory for the complete read/edit/write operation.

    Yields:
        The stable directory descriptor, or None for in-memory-only operation when storage is unsafe/unavailable.
    """
    with contextlib.ExitStack() as stack:
        try:
            folder = stack.enter_context(files.private_directory(path.parent))
            descriptor = files.open_updater_file(folder, path.name, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
            handle = stack.enter_context(os.fdopen(descriptor, "a", encoding="utf-8"))
            fcntl.flock(handle, fcntl.LOCK_EX)
        except (OSError, HeinzelError):
            yield None
            return
        try:
            yield folder
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def try_lock(path: Path, files: StateFiles) -> IO[str] | None:
    """An exclusive lock on `path` if no other holder has it, kept until released or this process ends."""
    try:
        with files.private_directory(path.parent) as folder:
            descriptor = files.open_updater_file(folder, path.name, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
            handle = os.fdopen(descriptor, "a", encoding="utf-8")
    except (OSError, HeinzelError):
        return None
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def unlock(handle: IO[str] | None) -> None:
    if handle is not None and not handle.closed:
        with contextlib.suppress(OSError):
            fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()
