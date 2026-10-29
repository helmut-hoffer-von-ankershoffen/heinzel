"""Where notices are kept: one JSON file in the state directory, bounded, pruned on every write, private.

`hz ui` and the daemon's tick are separate processes that both write here (one records and marks shown, the other
too), so every change holds an exclusive lock and replaces the file at once; a crash never leaves half a file.
Notices name contracts and what failed, so the directory is 0700 and the files 0600.
"""

import fcntl
import hashlib
import json
import logging
import os
import secrets
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from heinzel import Notice

logger = logging.getLogger(__name__)
MAX_NOTICES = 200
"""The most notices kept; the oldest go first."""
MAX_AGE = timedelta(days=30)
"""Notices older than this are pruned."""
PRIVATE_DIR = 0o700
PRIVATE_FILE = 0o600
_NOTICES = TypeAdapter(list[Notice])


def _empty_notices() -> list[Notice]:
    return []


class _State(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notices: list[Notice] = Field(default_factory=_empty_notices)
    durable_keys: set[str] = Field(default_factory=set)


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
        self._durable = directory / "durable-notices"

    def _read_state(self, *, strict: bool = False) -> _State:
        try:
            text = self.path.read_text(encoding="utf-8")
            value = json.loads(text)
        except (OSError, ValueError, ValidationError) as exc:
            return self._damaged_state(strict, exc)
        if isinstance(value, list):
            if strict:
                return self._damaged_state(strict, ValueError("legacy notification list lost replay keys"))
            try:
                return _State(notices=_NOTICES.validate_python(value))
            except ValidationError as exc:
                return self._damaged_state(strict, exc)
        try:
            return _State.model_validate(value)
        except ValidationError as exc:
            return self._damaged_state(strict, exc)

    def _damaged_state(self, strict: bool, exc: Exception) -> _State:
        if strict:
            msg = (
                "The durable notification store is unavailable or lost replay keys; restore it before recording notices"
            )
            raise OSError(msg) from exc
        if not isinstance(exc, FileNotFoundError):
            logger.warning("%s cannot be read (%s); showing no notices", self.path, exc)
        return _State()

    def read(self) -> list[Notice]:
        """Every notice kept, oldest first; a missing or damaged file is no notices (and is logged)."""
        return self._read_state().notices

    def _write_state(self, now: datetime, state: _State, *, durable: bool = False) -> None:
        state.notices = [notice for notice in state.notices if now - notice.at <= MAX_AGE][-MAX_NOTICES:]
        payload = (
            state.model_dump_json(indent=1)
            if durable or self._durable.exists() or state.durable_keys
            else _NOTICES.dump_json(state.notices, indent=1).decode()
        )
        _write_private(self.path, payload + "\n")

    def change[T](self, now: datetime, change: Callable[[list[Notice]], T]) -> T:
        """Apply a display-state change under the lock, preserving durable keys.

        Raises:
            OSError: If the store cannot be safely read or written.
        """
        with self._locked():
            state = self._read_state(strict=self._durable.exists())
            result = change(state.notices)
            self._write_state(now, state)
        return result

    def change_once(self, now: datetime, key: str, change: Callable[[list[Notice]], None]) -> bool:
        """Commit the notice and its owner-scoped key together, or recognize a previous commit.

        Raises:
            OSError: If the store cannot be safely read or written.
        """
        digest = hashlib.sha256(key.encode()).hexdigest()
        with self._locked():
            state = self._read_state(strict=self._durable.exists())
            if not self._durable.exists():
                # Persist the key-bearing shape first, so a crash cannot leave a marker beside a legacy list.
                self._write_state(now, state, durable=True)
                _write_private(self._durable, "1\n")
            if digest in state.durable_keys:
                return True
            change(state.notices)
            state.durable_keys.add(digest)
            self._write_state(now, state)
        return True

    def forget_once(self, now: datetime, key: str) -> bool:
        """Remove a replay key after its authoritative outbox has recorded completion.

        The notice itself stays in the bounded display history. A failed forget only leaves a harmless stale key;
        a later outbox sweep can retry it.

        Raises:
            OSError: If a versioned durable store is damaged or its replay keys are unavailable.
        """
        return self.forget_completed(now, {key})

    def forget_completed(self, now: datetime, keys: set[str]) -> bool:
        """Remove completed replay keys in one durable write; leave all notices intact."""
        if not keys:
            return True
        digests = {hashlib.sha256(key.encode()).hexdigest() for key in keys}
        with self._locked():
            state = self._read_state(strict=self._durable.exists())
            if state.durable_keys.isdisjoint(digests):
                return True
            state.durable_keys.difference_update(digests)
            self._write_state(now, state)
        return True

    def has_once(self, key: str) -> bool:
        """Check the durable replay set and retained notices under the notification lock.

        Raises:
            OSError: If the marker exists without state or the private store is damaged.
        """
        digest = hashlib.sha256(key.encode()).hexdigest()
        with self._locked():
            if not self.path.exists():
                if self._durable.exists():
                    msg = "The durable notification receipt marker has no notification state"
                    raise OSError(msg)
                return False
            state = self._read_state(strict=True)
            return digest in state.durable_keys or any(notice.key == key for notice in state.notices)

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
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
