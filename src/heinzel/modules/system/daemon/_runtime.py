"""What a tick needs from the machine: private files, a lock, a log, a time limit for jobs and subprocesses.

Receipts, state and logs can contain job output and errors, so directories are created 0700 and files 0600,
whatever the umask, and every file is written to a temporary name, flushed to disk and renamed, so a crash or a full
disk never leaves half a file.
"""

import contextvars
import fcntl
import io
import json
import logging
import os
import subprocess
import threading
from collections import deque
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from heinzel import HeinzelError
from heinzel.modules.system.daemon._models import LogLine

COMMAND_TIMEOUT = 60
"""Seconds launchctl, systemctl and uv may take; they answer in milliseconds unless something is broken."""
LOG_BYTES = 1_000_000
LOG_BACKUPS = 5
PRIVATE_DIR = 0o700
PRIVATE_FILE = 0o600

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class Outcome:
    """How one attempt to run a job ended."""

    value: object = None
    """What the job returned."""
    error: str | None = None
    """What went wrong, or None."""
    abandoned: threading.Thread | None = None
    """The thread of a job that overran its timeout and may still be running."""


TimeLimit = Callable[[Callable[[], object], float], Outcome]
_HELD_LIFECYCLE_LOCKS: list[io.TextIOWrapper] = []


def run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run a command; a timeout becomes exit code 124 (as with timeout(1)), so callers handle it like any failure."""
    try:
        return subprocess.run(command, capture_output=True, text=True, check=False, timeout=COMMAND_TIMEOUT)  # noqa: S603 - executables resolved with shutil.which, no shell
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, 124, "", f"{command[0]} timed out after {COMMAND_TIMEOUT}s")


def describe(exc: BaseException) -> str:
    """One line for a receipt: a HeinzelError's message, anything else with its type, since it is a bug."""
    return str(exc) if isinstance(exc, HeinzelError) else f"{type(exc).__name__}: {exc}"


def within(func: Callable[[], object], seconds: float) -> Outcome:
    """Run `func` in a daemon thread for at most `seconds`.

    A job that overruns is abandoned, not stopped: Python can't kill a thread, so it may go on and still have side
    effects. The thread is a daemon thread so the tick process still exits; per-job subprocesses that can be killed
    come later (ADR 0020).
    """
    results: list[Outcome] = []

    def target() -> None:
        try:
            results.append(Outcome(value=func()))
        except BaseException as exc:  # noqa: BLE001 - a job's failure is recorded in its receipt, never raised
            results.append(Outcome(error=describe(exc)))

    context = contextvars.copy_context()
    thread = threading.Thread(target=context.run, args=(target,), name="hz-job", daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        error = f"timed out after {seconds:g}s and was abandoned; it may still be running and have side effects"
        return Outcome(error=error, abandoned=thread)
    return results[0]


def private_dir(path: Path) -> Path:
    """Create `path` and its missing parents as 0700, and make `path` itself 0700."""
    missing = [p for p in (path, *path.parents) if not p.exists()]
    for directory in reversed(missing):
        directory.mkdir(mode=PRIVATE_DIR, exist_ok=True)
    path.chmod(PRIVATE_DIR)
    return path


def fsync_dir(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_private(path: Path, text: str, *, shared_dir: bool = False) -> None:
    """Write `text` atomically and durably: a temporary 0600 file, fsync, rename, fsync of the directory.

    The directory is made private (0700) unless `shared_dir`, e.g. for unit files in ~/Library/LaunchAgents.
    """
    if shared_dir:
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        private_dir(path.parent)
    temporary = path.with_name(f".{path.name}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_FILE)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.chmod(PRIVATE_FILE)  # the umask may have narrowed os.open's mode
    temporary.replace(path)
    fsync_dir(path.parent)


def _open_private(path: Path, flags: int) -> int:
    private_dir(path.parent)
    return os.open(path, flags | os.O_CREAT, PRIVATE_FILE)


@contextmanager
def lock(path: Path) -> Generator[bool]:
    """Hold an exclusive lock on `path` while the block runs.

    Yields:
        True while holding the lock; False, without waiting, if another process holds it.
    """
    with os.fdopen(_open_private(path, os.O_WRONLY | os.O_APPEND), "a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def lock_until_finished(path: Path, pending: Callable[[], Sequence[threading.Thread]]) -> Generator[bool]:
    """Hold the lifecycle lock until this tick's abandoned job threads actually finish.

    Yields:
        True if the lock was acquired, or False immediately if another writer holds it.
    """
    handle = os.fdopen(_open_private(path, os.O_WRONLY | os.O_APPEND), "a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        yield False
        return
    except BaseException:
        handle.close()
        raise
    try:
        yield True
    finally:
        try:
            alive = [thread for thread in pending() if thread.is_alive()]
            if alive:
                watcher = threading.Thread(target=_release_after, args=(handle, alive), daemon=True)
                watcher.start()
            else:
                _release_lock(handle)
        except Exception:  # noqa: BLE001 - retaining the lock is safer than overlapping unfinished work
            _HELD_LIFECYCLE_LOCKS.append(handle)


def _release_lock(handle: io.TextIOWrapper) -> None:
    fcntl.flock(handle, fcntl.LOCK_UN)
    handle.close()


def _release_after(handle: io.TextIOWrapper, threads: list[threading.Thread]) -> None:
    try:
        for thread in threads:
            thread.join()
        _release_lock(handle)
    except Exception:  # noqa: BLE001 - keep the lock held if work completion cannot be established
        _HELD_LIFECYCLE_LOCKS.append(handle)


class _PrivateRotatingFileHandler(RotatingFileHandler):
    """Opens the log and each new file after a rotation as 0600."""

    def _open(self) -> io.TextIOWrapper:
        descriptor = _open_private(Path(self.baseFilename), os.O_WRONLY | os.O_APPEND)
        return os.fdopen(descriptor, "a", encoding=self.encoding)


class TickLog:
    """The daemon's JSON-lines log, rotated by size because launchd never rotates its own output files."""

    def __init__(self, path: Path, tick: str, clock: Callable[[], datetime]) -> None:
        """Log to `path` on behalf of one tick; each line carries the time `clock` gives when it is written."""
        self.path = path
        self._tick = tick
        self._clock = clock
        self._handler: RotatingFileHandler | None = None

    def write(self, level: int, message: str, **fields: Any) -> None:  # noqa: ANN401 - JSON fields
        """Append one line."""
        if self._handler is None:
            self._handler = _PrivateRotatingFileHandler(
                self.path, maxBytes=LOG_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
            )
        line = {
            "at": self._clock().isoformat(),
            "level": logging.getLevelName(level),
            "message": message,
            "tick": self._tick,
        }
        line |= {key: value for key, value in fields.items() if value is not None}
        self._handler.emit(logging.makeLogRecord({"msg": json.dumps(line), "levelno": level}))

    def close(self) -> None:
        """Release the file."""
        if self._handler is not None:
            self._handler.close()


def tail(path: Path, lines: int) -> list[LogLine]:
    """The last `lines` readable lines of the log and its rotated backups, oldest first."""
    files = [path.with_name(f"{path.name}.{n}") for n in range(LOG_BACKUPS, 0, -1)] + [path]
    kept: deque[LogLine] = deque(maxlen=lines)
    for file in files:
        if not file.is_file():
            continue
        for line in file.read_text(encoding="utf-8").splitlines():
            try:
                kept.append(LogLine.model_validate_json(line))
            except ValidationError:
                continue
    return list(kept)
