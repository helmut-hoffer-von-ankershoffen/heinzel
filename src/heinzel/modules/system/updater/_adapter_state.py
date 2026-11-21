"""The real adapter of the `InstallationState` port: the state folder on disk, locks with `fcntl`, journals as files."""

from __future__ import annotations

import contextlib
import fcntl
import os
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator
    from pathlib import Path

__all__ = ["FileInstallationState"]

_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
OWNER_ONLY_DIRECTORY = 0o700
OWNER_ONLY_FILE = 0o600


def _checked(name: str) -> str:
    if not _NAME.fullmatch(name):
        msg = f"{name!r} is not a state name: lower-case letters, digits, dots, dashes, at most 64"
        raise ValueError(msg)
    return name


class FileInstallationState:
    """One installation's state folder (mode 0700); its files are written for this user only."""

    def __init__(self, folder: Path) -> None:
        """Use `folder`; it is created on first use."""
        self._folder = folder

    @property
    def folder(self) -> Path:
        """The state folder, created with owner-only access when it did not exist."""
        self._folder.mkdir(mode=OWNER_ONLY_DIRECTORY, parents=True, exist_ok=True)
        return self._folder

    @contextlib.contextmanager
    def lock(self, name: str) -> Generator[None]:
        """Hold the exclusive `flock` of `<name>.lock`; another process or thread that asks waits for it."""
        path = self.folder / f"{_checked(name)}.lock"
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, OWNER_ONLY_FILE)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
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
