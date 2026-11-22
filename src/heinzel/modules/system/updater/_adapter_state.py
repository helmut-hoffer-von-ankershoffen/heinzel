"""The real adapter of the `InstallationState` port: the state folder on disk, locks with `fcntl`, journals as files."""

from __future__ import annotations

import contextlib
import fcntl
import os
import re
import time
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator

__all__ = ["FileInstallationState"]

_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
OWNER_ONLY_DIRECTORY = 0o700
OWNER_ONLY_FILE = 0o600


def _checked(name: str) -> str:
    if not _NAME.fullmatch(name):
        msg = f"{name!r} is not a state name: lower-case letters, digits, dots, dashes, at most 64"
        raise ValueError(msg)
    return name


_POLL_SECONDS = 0.05


def _flock_within(descriptor: int, wait: float) -> None:
    """Take the exclusive `flock` of `descriptor` within `wait` seconds.

    Raises:
        TimeoutError: If another holder still has it after `wait` seconds.
    """
    deadline = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            left = deadline - time.monotonic()
            if left <= 0:
                msg = "the lock is still held by another holder"
                raise TimeoutError(msg) from None
            time.sleep(min(_POLL_SECONDS, left))
        else:
            return


class FileInstallationState:
    """One installation's state folder (mode 0700); its files are written for this user only."""

    def __init__(self, folder: Path, home: Path | None = None) -> None:
        """Use `folder`, created on first use; `home` is where the desktop app's files live (the person's home)."""
        self._folder = folder
        self._home = home

    @property
    def folder(self) -> Path:
        """The state folder, created with owner-only access when it did not exist."""
        self._folder.mkdir(mode=OWNER_ONLY_DIRECTORY, parents=True, exist_ok=True)
        return self._folder

    @contextlib.contextmanager
    def lock(self, name: str, wait: float | None = None) -> Generator[None]:
        """Hold the exclusive `flock` of `<name>.lock`; another process or thread that asks waits for it.

        Raises:
            TimeoutError: If `wait` seconds passed and another holder still has it.
        """
        path = self.folder / f"{_checked(name)}.lock"
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, OWNER_ONLY_FILE)
        try:
            if wait is None:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            else:
                _flock_within(descriptor, wait)
            yield
        finally:
            os.close(descriptor)  # closing releases the lock

    def journal(self, name: str) -> Iterator[str]:
        """The lines of `<name>.journal`, oldest first; none if it was never written."""
        path = self.folder / f"{_checked(name)}.journal"
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return iter(())
        return iter(text.splitlines())

    def home_file(self, relative: str) -> str | None:
        """The text of `relative` under the home folder; None if it is missing, unreadable or names a path outside.

        Only the path as written is checked (no `..`, not absolute): symlinks under home are followed, so a receipt
        under a `~/.config` that links to a dotfiles checkout is found, as it was before this port.
        """
        parts = PurePosixPath(relative)
        if parts.is_absolute() or ".." in parts.parts:
            return None
        try:
            return ((self._home or Path.home()) / parts).read_text(encoding="utf-8")
        except (OSError, RuntimeError, UnicodeDecodeError):  # RuntimeError: no home to look in
            return None

    def append(self, name: str, line: str) -> None:
        """Add one line to `<name>.journal` and flush it to disk before returning.

        Raises:
            ValueError: If `line` holds a line break, which would be two entries.
        """
        if "\n" in line or "\r" in line:
            msg = "a journal entry is one line"
            raise ValueError(msg)
        path = self.folder / f"{_checked(name)}.journal"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, OWNER_ONLY_FILE)
        try:
            os.write(descriptor, f"{line}\n".encode())
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
