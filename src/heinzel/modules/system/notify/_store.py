"""Where notices are kept: one JSON file in the state directory, bounded, pruned on every write, private.

`hz ui` and the daemon's tick are separate processes that both write here (one records and marks shown, the other
too), so every change holds an exclusive lock and replaces the file at once; a crash never leaves half a file.
Notices name contracts and what failed, so the directory is 0700 and the files 0600.
"""

import fcntl
import json
import logging
import os
import secrets
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from heinzel import Notice

logger = logging.getLogger(__name__)
MAX_NOTICES = 200
"""The most notices kept; the oldest go first."""
MAX_AGE = timedelta(days=30)
"""Notices older than this are pruned."""
PRIVATE_DIR = 0o700
PRIVATE_FILE = 0o600
_NOTICES = TypeAdapter(list[Notice])


class Store:
    """The notices on this machine, oldest first, and when a head last said it shows them."""

    def __init__(self, directory: Path) -> None:
        """Keep notices in `directory` (created 0700 on the first write)."""
        self.directory = directory
        self.path = directory / "notices.json"
        self._viewer = directory / "viewer.json"
        self._lock = directory / "notices.lock"
        self._native = directory / "native.json"
        self._looked = directory / "looked.json"

    def read(self) -> list[Notice]:
        """Every notice kept, oldest first; a missing or damaged file is no notices (and is logged)."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError:
            logger.warning("can't read %s; showing no notices", self.path, exc_info=True)
            return []
        try:
            return _NOTICES.validate_json(text)
        except ValidationError:
            logger.warning("%s is damaged; it is replaced on the next notice", self.path)
            return []

    def change[T](self, now: datetime, change: Callable[[list[Notice]], T]) -> T:
        """Apply `change` to the notices under the lock, prune them and write them back; returns what it returned.

        Raises:
            OSError: If the state directory can't be written.
        """
        with self._locked():
            notices = self.read()
            result = change(notices)
            kept = [notice for notice in notices if now - notice.at <= MAX_AGE][-MAX_NOTICES:]
            _write_private(self.path, _NOTICES.dump_json(kept, indent=1).decode() + "\n")
        return result

    def watched(self, now: datetime) -> None:
        """Remember that a head shows notices now."""
        try:
            _write_private(self._viewer, json.dumps({"at": now.isoformat()}) + "\n")
        except OSError:
            logger.warning("can't write %s", self._viewer, exc_info=True)

    def watched_at(self) -> datetime | None:
        """When a head last said it shows notices; None if never or unreadable."""
        try:
            value = json.loads(self._viewer.read_text(encoding="utf-8")).get("at")
            return datetime.fromisoformat(value) if isinstance(value, str) else None
        except (OSError, ValueError, AttributeError):
            return None

    def looked(self, now: datetime) -> None:
        """Remember when the daemon last looked for doctor's findings."""
        try:
            _write_private(self._looked, json.dumps({"at": now.isoformat()}) + "\n")
        except OSError:
            logger.warning("can't write %s", self._looked, exc_info=True)

    def looked_at(self) -> datetime | None:
        """When the daemon last looked for doctor's findings; None if never or unreadable."""
        try:
            value = json.loads(self._looked.read_text(encoding="utf-8")).get("at")
            return datetime.fromisoformat(value) if isinstance(value, str) else None
        except (OSError, ValueError, AttributeError):
            return None

    def native_result(self, now: datetime, problem: str | None) -> None:
        """Remember how the last native pop-up went: None when it was shown, else why not."""
        try:
            _write_private(self._native, json.dumps({"at": now.isoformat(), "problem": problem}) + "\n")
        except OSError:
            logger.warning("can't write %s", self._native, exc_info=True)

    def native_problem(self) -> str | None:
        """Why the last native pop-up failed; None if it was shown or none was tried."""
        try:
            problem = json.loads(self._native.read_text(encoding="utf-8")).get("problem")
        except (OSError, ValueError, AttributeError):
            return None
        return problem if isinstance(problem, str) else None

    @contextmanager
    def _locked(self) -> Generator[None]:
        _private_dir(self.directory)
        descriptor = os.open(self._lock, os.O_WRONLY | os.O_CREAT | os.O_APPEND, PRIVATE_FILE)
        with os.fdopen(descriptor, "a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)  # a write takes milliseconds; waiting is right
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR)
    path.chmod(PRIVATE_DIR)


def _write_private(path: Path, text: str) -> None:
    """Write `text` to a 0600 temporary file, flush it to disk and rename it over `path`."""
    _private_dir(path.parent)
    partial = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}")  # threads of one process too
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_FILE)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    partial.replace(path)
