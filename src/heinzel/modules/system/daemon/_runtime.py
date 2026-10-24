"""What a tick needs from the machine: a lock, a log, a time limit for jobs and subprocesses with timeouts."""

import fcntl
import json
import logging
import subprocess
import threading
from collections import deque
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
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

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
TimeLimit = Callable[[Callable[[], object], float], str | None]


def run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run a command; a timeout becomes exit code 124 (as with timeout(1)), so callers handle it like any failure."""
    try:
        return subprocess.run(command, capture_output=True, text=True, check=False, timeout=COMMAND_TIMEOUT)  # noqa: S603 - executables resolved with shutil.which, no shell
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, 124, "", f"{command[0]} timed out after {COMMAND_TIMEOUT}s")


def describe(exc: BaseException) -> str:
    """One line for a receipt: a HeinzelError's message, anything else with its type, since it is a bug."""
    return str(exc) if isinstance(exc, HeinzelError) else f"{type(exc).__name__}: {exc}"


def within(func: Callable[[], object], seconds: float) -> str | None:
    """Run `func` in a daemon thread for at most `seconds`; return what went wrong, or None.

    A job that overruns is abandoned, not stopped: Python can't kill a thread. The thread is a daemon thread so the
    tick process still exits; per-job subprocesses that can be killed come later (ADR 0020).
    """
    problems: list[str] = []

    def target() -> None:
        try:
            func()
        except BaseException as exc:  # noqa: BLE001 - a job's failure is recorded in its receipt, never raised
            problems.append(describe(exc))

    thread = threading.Thread(target=target, name="hz-job", daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        return f"timed out after {seconds:g}s"
    return problems[0] if problems else None


@contextmanager
def lock(path: Path) -> Generator[bool]:
    """Hold an exclusive lock on `path` while the block runs.

    Yields:
        True while holding the lock; False, without waiting, if another process holds it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class TickLog:
    """The daemon's JSON-lines log, rotated by size because launchd never rotates its own output files."""

    def __init__(self, path: Path, tick: str) -> None:
        """Log to `path` on behalf of one tick."""
        self.path = path
        self._tick = tick
        self._handler: RotatingFileHandler | None = None

    def write(self, at: datetime, level: int, message: str, **fields: Any) -> None:  # noqa: ANN401 - JSON fields
        """Append one line."""
        if self._handler is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handler = RotatingFileHandler(
                self.path, maxBytes=LOG_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
            )
        line = {"at": at.isoformat(), "level": logging.getLevelName(level), "message": message, "tick": self._tick}
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
