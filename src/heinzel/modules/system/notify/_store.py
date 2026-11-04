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
import stat
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter

from heinzel import Notice

logger = logging.getLogger(__name__)
MAX_NOTICES = 200
"""The most notices kept; the oldest go first."""
MAX_EXPIRING_KEYS = 1024
"""Bound concurrent deadline-scoped receipts; refuse new ones if the limit is reached."""
MAX_HEALTH_BYTES = 4 * 1024 * 1024
"""Health verification only; larger stores are unavailable to this check, not declared corrupt."""
MAX_AGE = timedelta(days=30)
"""Notices older than this are pruned."""
PRIVATE_DIR = 0o700
PRIVATE_FILE = 0o600
_NOTICES = TypeAdapter(list[Notice])
INTEGRITY_UNAVAILABLE = "Durable notification history cannot be verified; preserve the store and its replay evidence."


def _empty_notices() -> list[Notice]:
    return []


class _State(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notices: list[Notice] = Field(default_factory=_empty_notices)
    durable_keys: set[str] = Field(default_factory=set)
    expiring_keys: dict[str, AwareDatetime] = Field(default_factory=dict)
    expiring_watermark: AwareDatetime | None = None


def _advance_expiry(state: _State, now: datetime) -> datetime:
    instant = now.astimezone(UTC)
    watermark = max(instant, state.expiring_watermark or instant)
    state.expiring_watermark = watermark
    state.expiring_keys = {key: expiry for key, expiry in state.expiring_keys.items() if expiry > watermark}
    for index, notice in enumerate(state.notices):
        if notice.expires is not None and notice.expires <= watermark and notice.delivered_by is None:
            state.notices[index] = notice.model_copy(update={"popup": False, "held": "expired"})
    return watermark


def _decode_state(text: str | bytes, *, strict: bool, expiring: bool = False) -> _State:
    """Validate serialized state; durable records must explicitly retain both fields.

    Raises:
        ValueError: If the state or durable evidence cannot be verified.
    """
    value = json.loads(text)
    if isinstance(value, list):
        if strict:
            msg = "legacy notification list lost replay keys"
            raise ValueError(msg)
        return _State(notices=_NOTICES.validate_python(value))
    state = _State.model_validate(value)
    if strict and not {"notices", "durable_keys"} <= state.model_fields_set:
        msg = "durable notification state lacks explicit notices or replay keys"
        raise ValueError(msg)
    if expiring and (
        not {"expiring_keys", "expiring_watermark"} <= state.model_fields_set
        or state.expiring_watermark is None
        or len(state.expiring_keys) > MAX_EXPIRING_KEYS
    ):
        msg = "expiring notification state lacks replay evidence"
        raise ValueError(msg)
    return state


def _safe_health_parent(info: os.stat_result) -> bool:
    sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
    return stat.S_ISDIR(info.st_mode) and info.st_uid in {0, os.getuid()} and (not info.st_mode & 0o022 or sticky_root)


def _health_directory(path: Path) -> int | None:
    """Open a physical private store through safe directory descriptors; never create it.

    Raises:
        OSError: If an existing path is linked, unsafe or inaccessible.
    """
    if not path.is_absolute() or ".." in path.parts:
        raise OSError
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
    directory = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            if not _safe_health_parent(os.fstat(directory)):
                raise OSError
            try:
                child = os.open(part, flags, dir_fd=directory)
            except FileNotFoundError:
                return None
            os.close(directory)
            directory = child
        info = os.fstat(directory)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise OSError
        return os.dup(directory)
    finally:
        os.close(directory)


def _health_file(directory: int, name: str) -> int:
    """Open and verify one private regular file without following links or blocking on special files.

    Raises:
        OSError: If the file is missing, linked, unsafe or inaccessible.
    """
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        info = os.fstat(descriptor)
    except BaseException:
        os.close(descriptor)
        raise
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
        os.close(descriptor)
        raise OSError
    return descriptor


def _has_health_marker(directory: int, name: str) -> bool:
    try:
        descriptor = _health_file(directory, name)
    except FileNotFoundError:
        return False
    os.close(descriptor)
    return True


def _health_contents(directory: int) -> bytes | None:
    """Read only the already verified descriptor; an oversized store remains unverified.

    Raises:
        OSError: If the file is missing, linked, unsafe or inaccessible.
    """
    descriptor = _health_file(directory, "notices.json")
    with os.fdopen(descriptor, "rb") as source:
        if os.fstat(source.fileno()).st_size > MAX_HEALTH_BYTES:
            return None
        data = source.read(MAX_HEALTH_BYTES + 1)
    return data if len(data) <= MAX_HEALTH_BYTES else None


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
        self._expiring = directory / "expiring-notices"

    def _read_state(self, *, strict: bool = False) -> _State:
        expiring = self._expiring.exists()
        strict = strict or expiring
        if expiring and not self._durable.exists():
            msg = "Expiring notification history lost its durable marker"
            raise OSError(msg)
        try:
            return _decode_state(self.path.read_text(encoding="utf-8"), strict=strict, expiring=expiring)
        except (OSError, ValueError) as exc:
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

    def integrity_problem(self) -> str | None:
        """Verify bounded durable evidence without writes, locks, linked paths or notice disclosure."""
        try:
            directory = _health_directory(self.directory)
            if directory is None:
                return None
            try:
                durable = _has_health_marker(directory, "durable-notices")
                expiring = _has_health_marker(directory, "expiring-notices")
                if expiring and not durable:
                    return INTEGRITY_UNAVAILABLE
                try:
                    data = _health_contents(directory)
                except FileNotFoundError:
                    return INTEGRITY_UNAVAILABLE if durable or expiring else None
                if data is None:
                    return (
                        "Durable notification history exceeds the health-check read limit; verification is unavailable."
                    )
                _decode_state(data, strict=durable or expiring, expiring=expiring)
            finally:
                os.close(directory)
        except (OSError, ValueError, RecursionError):
            return INTEGRITY_UNAVAILABLE
        return None

    def read(self) -> list[Notice]:
        """Every notice kept, oldest first; a missing or damaged file is no notices (and is logged)."""
        return self._read_state().notices

    def _write_state(self, now: datetime, state: _State, *, durable: bool = False) -> None:
        state.notices = [notice for notice in state.notices if now - notice.at <= MAX_AGE][-MAX_NOTICES:]
        payload = (
            state.model_dump_json(
                indent=1,
                exclude=None
                if self._expiring.exists() or state.expiring_watermark is not None
                else {"expiring_keys", "expiring_watermark"},
            )
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

    def change_once(
        self, now: datetime, key: str, change: Callable[[list[Notice]], None], *, expires: datetime | None = None
    ) -> bool:
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
            if expires is not None and not self._expiring.exists():
                state.expiring_watermark = now.astimezone(UTC)
                self._write_state(now, state, durable=True)
                _write_private(self._expiring, "1\n")
            if expires is not None:
                watermark = _advance_expiry(state, now)
                if expires <= watermark:
                    self._write_state(now, state)
                    return True
            if digest in state.durable_keys or digest in state.expiring_keys:
                if expires is not None:
                    self._write_state(now, state)
                return True
            if expires is not None and len(state.expiring_keys) >= MAX_EXPIRING_KEYS:
                msg = "Too many outstanding expiring notification receipts"
                raise OSError(msg)
            change(state.notices)
            if expires is None:
                state.durable_keys.add(digest)
            else:
                state.expiring_keys[digest] = expires
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
            if state.durable_keys.isdisjoint(digests) and state.expiring_keys.keys().isdisjoint(digests):
                return True
            state.durable_keys.difference_update(digests)
            for digest in digests:
                state.expiring_keys.pop(digest, None)
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
            return (
                digest in state.durable_keys
                or digest in state.expiring_keys
                or any(notice.key == key for notice in state.notices)
            )

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
