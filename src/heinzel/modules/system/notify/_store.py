"""Where notices are kept: one JSON file in the state directory, bounded, pruned on every write, private.

`hz ui serve` and the daemon's tick are separate processes that both write here (one records and marks shown, the other
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
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from heinzel import Notice
from heinzel.modules.system.notify._models import Archived, Recovered

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
INTEGRITY_LEGACY = (
    "Durable notification history cannot be verified: a legacy notice list replaced its replay keys; "
    "preserve the store and its replay evidence."
)
"""The one damaged shape `hz system notify recover` may archive and restart."""


class LegacyListError(ValueError):
    """A legacy notice list sits where the durable marker requires key-bearing state."""


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
        LegacyListError: If a legacy list sits beside the plain durable marker.
        ValueError: If the state or durable evidence cannot be verified.
    """
    value = json.loads(text)
    if isinstance(value, list):
        if strict:
            msg = "legacy notification list lost replay keys"
            # Only the plain durable marker has a supported recovery; deadline-scoped receipts need a backup.
            raise (ValueError if expiring else LegacyListError)(msg)
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
        except (OSError, ValueError, RecursionError) as exc:
            return INTEGRITY_LEGACY if isinstance(exc, LegacyListError) else INTEGRITY_UNAVAILABLE
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

    def locked(self) -> AbstractContextManager[None]:
        """The writer lock every change holds; recovery holds it too."""
        return self._locked()

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


# Recovery -----------------------------------------------------------------------------------------------------------
"""Explicit recovery of one known damaged notification store shape, with an archive and an audit receipt.

The shape: the durable marker (`durable-notices`, the single line `1`) beside `notices.json` with a legacy list.
A writer from before durable notices replaced the key-bearing state with a plain list, so the replay keys it held are
gone and no file here can restore them. Every write then fails closed (ADR 0041's durable receipts) until a person
decides. Recovery never reconstructs the lost keys: it archives every original file with its SHA-256, mode and
modification time, writes a receipt, and starts a new durable state whose keys come only from the retained notices'
own `key` fields, so no retained notice can be recorded twice. Old notices stay in the history and never pop up again.

Anything else (an expiring marker, a key-bearing state, a missing list, a link, a foreign owner, a loose mode, an
unknown or half-written file) is refused untouched: that store may hold replay evidence a backup must restore.
"""

RECEIPT_VERSION = 1
MAX_FILE_BYTES = 1024 * 1024
"""Recovery reads whole files into memory; a larger notification store is not this legacy shape."""
MARKER = b"1\n"
KNOWN = frozenset(
    {
        "durable-notices",
        "notices.json",
        "notices.lock",
        "looked.json",
        "native.json",
        "viewer.json",
        "strays-forgotten-at",
    }
)
"""Every file a notification store of the legacy shape may hold; anything else is refused."""
STRICT = frozenset({"durable-notices", "notices.json"})
"""Files that must be private (0600) to be trusted as replay evidence."""


def _refused(detail: str) -> Recovered:
    return Recovered(state="refused", detail=detail)


def _read_files(store: Store) -> dict[str, tuple[bytes, os.stat_result]] | str:
    """Every file in the store, read through verified no-follow descriptors, or why the store is refused."""
    directory = _health_directory(store.directory)
    if directory is None:
        return "There is no notification store."
    try:
        names = set(os.listdir(directory))  # noqa: PTH208 - list the verified descriptor, not a re-resolved path
        if unknown := sorted(names - KNOWN):
            return f"The notification store holds files recovery does not know ({', '.join(unknown)}); nothing changed."
        files: dict[str, tuple[bytes, os.stat_result]] = {}
        for name in sorted(names):
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(descriptor, "rb") as source:
                info = os.fstat(source.fileno())
                loose = info.st_mode & (0o077 if name in STRICT else 0o022)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or loose:
                    return f"{name} is not a private regular file of this user; nothing changed."
                if info.st_size > MAX_FILE_BYTES:
                    return f"{name} is larger than this recovery reads; nothing changed."
                files[name] = (source.read(MAX_FILE_BYTES + 1), info)
    except OSError:
        return "The notification store cannot be read safely; nothing changed."
    finally:
        os.close(directory)
    return files


def _parse(data: bytes) -> list[Notice] | Recovered:
    """The legacy notices in `notices.json`, or what it holds instead."""
    try:
        value = json.loads(data)
    except (ValueError, RecursionError):
        return _refused("The notification state is not valid JSON; restore a trusted backup.")
    if not isinstance(value, list):
        try:
            _decode_state(data, strict=True)
        except (ValueError, RecursionError):
            return _refused("The key-bearing notification state is damaged; restore a trusted backup.")
        return Recovered(
            state="healthy", detail="The durable notification state verifies; there is nothing to recover."
        )
    try:
        return _NOTICES.validate_python(value)
    except ValidationError:
        return _refused("The legacy notice list does not validate; nothing changed.")


def _legacy(files: dict[str, tuple[bytes, os.stat_result]]) -> list[Notice] | Recovered:
    """The legacy notices, or what the store is instead."""
    if "durable-notices" not in files:
        return Recovered(state="healthy", detail="The store has no durable marker; there is nothing to recover.")
    if files["durable-notices"][0] != MARKER:
        problem = "The durable marker has unknown contents; nothing changed."
    elif "notices.json" not in files:
        problem = "The durable marker has no notification state; restore a trusted backup."
    else:
        return _parse(files["notices.json"][0])
    return _refused(problem)


def _archive(
    root: Path, stamp: str, files: dict[str, tuple[bytes, os.stat_result]]
) -> tuple[Path, list[Archived]] | str:
    """Copy every original, mode and time preserved, into a new private directory under `root`."""
    target = root / f"{stamp}-{secrets.token_hex(4)}"
    try:
        _private_dir(root)
        target.mkdir(mode=PRIVATE_DIR)
        archived: list[Archived] = []
        for name, (data, info) in files.items():
            mode = stat.S_IMODE(info.st_mode)
            descriptor = os.open(target / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
            with os.fdopen(descriptor, "wb") as output:
                output.write(data)
                output.flush()
                os.fchmod(output.fileno(), mode)
                os.fsync(output.fileno())
            os.utime(target / name, ns=(info.st_atime_ns, info.st_mtime_ns))
            archived.append(
                Archived(
                    name=name,
                    sha256=hashlib.sha256(data).hexdigest(),
                    size=len(data),
                    mode=f"{mode:04o}",
                    modified=datetime.fromtimestamp(info.st_mtime, UTC),
                )
            )
        for name, entry in zip(files, archived, strict=True):
            if hashlib.sha256((target / name).read_bytes()).hexdigest() != entry.sha256:
                return "The archive does not match the originals; the store was not changed."
        _fsync_dir(target)
    except OSError:
        return "The originals cannot be archived; the store was not changed."
    return target, archived


def _fsync_dir(path: Path) -> None:
    directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _exists(store: Store) -> bool:
    directory = _health_directory(store.directory)
    if directory is None:
        return False
    os.close(directory)
    return True


def _inspect(store: Store) -> tuple[dict[str, tuple[bytes, os.stat_result]], list[Notice]] | Recovered:
    files = _read_files(store)
    if isinstance(files, str):
        return _refused(files)
    legacy = _legacy(files)
    return legacy if isinstance(legacy, Recovered) else (files, legacy)


def _keys(legacy: list[Notice]) -> set[str]:
    return {hashlib.sha256(notice.key.encode()).hexdigest() for notice in legacy if notice.key}


def recover(store: Store, now: datetime, *, apply: bool) -> Recovered:
    """Inspect the store and, with `apply`, archive the legacy shape and restart its durable state.

    Without `apply` nothing is written, not even the lock. With it, the notification writer lock is held throughout,
    so no head or tick writes in between. Crash-safe: the originals and a receipt naming the new state's digest are on
    disk before one atomic replacement of `notices.json`; a crash before it leaves the store as it was (a rerun
    archives again), a crash after it leaves a state that verifies.

    Raises:
        OSError: If the new state was written but does not verify (never expected; the archive holds the originals).
    """
    try:
        if not _exists(store):
            return Recovered(state="healthy", detail="There is no notification store; there is nothing to recover.")
    except OSError:
        return _refused("The notification store's path is linked or not private to this user; nothing changed.")
    if not apply:
        found = _inspect(store)
        if isinstance(found, Recovered):
            return found
        _, legacy = found
        return Recovered(
            state="recoverable",
            detail="A legacy notice list replaced the durable replay keys. Run again with --apply to archive the "
            "store and start a new replay-key set; the lost keys cannot be restored from this store.",
            notices=len(legacy),
            keys=len(_keys(legacy)),
        )
    return _apply(store, now)


def _apply(store: Store, now: datetime) -> Recovered:
    """Archive, write the receipt, then replace the state once, all under the writer lock.

    Raises:
        OSError: If the new state was written but does not verify.
    """
    with store.locked():
        found = _inspect(store)
        if isinstance(found, Recovered):
            return found
        files, legacy = found
        keys = _keys(legacy)
        instant = now.astimezone(UTC)
        archive = _archive(store.directory.parent / "notify-recovery", f"{instant:%Y%m%dT%H%M%S%fZ}", files)
        if isinstance(archive, str):
            return _refused(archive)
        target, archived = archive
        kept = [
            notice
            if notice.delivered_by is not None
            else notice.model_copy(update={"popup": False, "held": "recovered"})
            for notice in legacy
        ]
        state = _State(notices=kept, durable_keys=keys)
        payload = state.model_dump_json(indent=1, exclude={"expiring_keys", "expiring_watermark"}) + "\n"
        receipt = {
            "version": RECEIPT_VERSION,
            "recovered_at": instant.isoformat(),
            "shape": "durable marker beside a legacy notice list without replay keys",
            "originals": [entry.model_dump(mode="json") for entry in archived],
            "replay_keys_before": None,
            "replay_keys_after": {"count": len(keys), "source": "the kept notices' own key fields"},
            "notices_kept": len(kept),
            "new_state_sha256": hashlib.sha256(payload.encode()).hexdigest(),
        }
        try:
            _write_private(target / "receipt.json", json.dumps(receipt, indent=1) + "\n")
            _write_private(store.path, payload)
        except OSError:
            return _refused(f"The new state could not be written; the originals are archived in {target}.")
        try:
            _decode_state(store.path.read_bytes(), strict=True)
        except ValueError as exc:
            msg = f"The recovered notification state does not verify; the originals are archived in {target}"
            raise OSError(msg) from exc
    return Recovered(
        state="recovered",
        detail="Archived the originals and started a new replay-key set; notices can be recorded again.",
        notices=len(kept),
        keys=len(keys),
        receipt=str(target),
    )
