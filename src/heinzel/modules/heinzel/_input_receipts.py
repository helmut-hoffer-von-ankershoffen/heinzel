"""Internal owner-bound snapshots of successful, approved connected reads.

This compares exact whole results, not provider events, message identity or source completeness. It does not schedule,
notify, invoke tools, or provide a model-accessible receipt API. Content is never persisted by this component.
"""

import contextlib
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

from pydantic import BaseModel, Field, JsonValue

from heinzel import ConnectedItemIdentity, CurrentUser, HeinzelError, SessionEvent, SessionWriter
from heinzel.modules.heinzel._connected_grants import ConnectedReadFailedError, ConnectedReadGrants
from heinzel.modules.heinzel._item_evidence import EvidenceContext, ItemReference, extract_items
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionId

_KEY = "input-receipt-key-v1"
_KEY_GUARD = "input-receipt-key-id-v1"
_KEY_BYTES = 32
_MAX_HISTORY = 100
_DIGEST = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


def _owner_history(current: CurrentUser) -> list[SessionEvent]:
    """Read every reserved owner journal, including incomplete or damaged sessions."""
    from heinzel.modules.heinzel._session_repository import PersonalSessionRepository  # noqa: PLC0415 - avoid cycle

    repository = PersonalSessionRepository(current)
    events: list[SessionEvent] = []
    sessions = repository.reserved_session_ids()
    repository.verify_reserved_session_stores(sessions)
    for session in sessions:
        events.extend(
            event
            for event in repository.control_read(session).events
            if event.kind in {"input-snapshot.observed", "connected-set-input.observed"}
        )
    return events


def _key_id(key: bytes) -> str:
    return hashlib.sha256(b"heinzel-input-receipt-v1\0" + key).hexdigest()


def _write_guard(directory: int, key_id: str) -> None:
    descriptor = CurrentUser.open_private(directory, _KEY_GUARD, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(key_id.encode("ascii"))
        stream.flush()
        os.fsync(stream.fileno())
    os.fsync(directory)


def _read_guard(directory: int) -> bytes | None:
    try:
        guard_fd = CurrentUser.open_private(directory, _KEY_GUARD, os.O_RDONLY)
    except FileNotFoundError:
        return None
    with os.fdopen(guard_fd, "rb") as stream:
        guard = stream.read(65)
    if not re.fullmatch(rb"[0-9a-f]{64}", guard):
        msg = "The private input receipt key needs repair"
        raise HeinzelError(msg)
    return guard


def _create_key(directory: int) -> bytes:
    key = secrets.token_bytes(_KEY_BYTES)
    # The marker precedes the key: a crash at any later step leaves an explicit repair state.
    _write_guard(directory, _key_id(key))
    temporary = f".input-receipt-{secrets.token_hex(12)}"
    fd = CurrentUser.open_private(directory, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(key)
            stream.flush()
            os.fsync(stream.fileno())
        os.rename(temporary, _KEY, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=directory)
    return key


def _load_key(current: CurrentUser, *, create: bool) -> bytes:
    """Load one owner-wide receipt key, never replacing a missing or changed initialized key.

    The guard is durable loss detection, not protection from an actor controlling the owner's files.

    Raises:
        HeinzelError: If the key, guard or any owner history cannot be safely verified.
    """
    current.owner()
    directory = os.open(current.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        CurrentUser.verify_private(os.fstat(directory), directory=True)
        lock = CurrentUser.open_private(directory, "owner.lock", os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            guard = _read_guard(directory)
            try:
                fd = CurrentUser.open_private(directory, _KEY, os.O_RDONLY)
            except FileNotFoundError:
                if guard is not None or not create or _owner_history(current):
                    msg = "The private input snapshot history needs repair"
                    raise HeinzelError(msg) from None
                return _create_key(directory)
            with os.fdopen(fd, "rb") as stream:
                key = stream.read(_KEY_BYTES + 1)
            if len(key) != _KEY_BYTES:
                msg = "The private input receipt key needs repair"
                raise HeinzelError(msg)
            actual = _key_id(key)
            if guard is None:
                if any(event.data.get("key_id") != actual for event in _owner_history(current)):
                    msg = "The private input snapshot history needs repair"
                    raise HeinzelError(msg)
                _write_guard(directory, actual)
            elif not hmac.compare_digest(guard, actual.encode("ascii")):
                msg = "The private input receipt key needs repair"
                raise HeinzelError(msg)
            return key
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)
    finally:
        os.close(directory)


def _key(current: CurrentUser, *, create: bool) -> bytes:
    try:
        return _load_key(current, create=create)
    except OSError:
        msg = "The private input receipt key is unavailable"
        raise HeinzelError(msg) from None


def existing_input_receipt_key(current: CurrentUser) -> bytes:
    """Read the existing owner-private receipt key without creating a new one.

    Raises:
        HeinzelError: If the owner-private key is unavailable or invalid.
    """
    return _key(current, create=False)


def _fingerprint(key: bytes, value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hmac.new(key, encoded, hashlib.sha256).hexdigest()


def input_receipt_key(current: CurrentUser, *, create: bool) -> bytes:
    """Return the owner-private receipt key under its existing guarded creation rules."""
    return _key(current, create=create)


def input_receipt_fingerprint(key: bytes, value: object) -> str:
    """Compute a domain-separated caller-specified private receipt fingerprint."""
    return _fingerprint(key, value)


@dataclass(frozen=True, slots=True)
class InputSnapshot:
    """Local exact-snapshot comparison, never a claim about event identity or complete source history."""

    status: Literal["first", "unchanged", "changed"]
    sequence: int
    items: tuple[ItemReference, ...] = ()
    item_collection: tuple[str, ...] | None = None


class InputSnapshotView(BaseModel):
    """A private redacted exact-result comparison for a recorded connected read."""

    seq: int = Field(description="Sequence in the owner's control journal.")
    at: datetime = Field(description="When the host recorded this successful read.")
    turn_id: str = Field(description="The durable turn which performed the approved read.")
    status: Literal["first", "unchanged", "changed"] = Field(
        description="Comparison with the prior exact result under the same approved grant."
    )


class InputSnapshotPage(BaseModel):
    """Bounded recent snapshot observations; not a complete event history."""

    session_id: SessionId = Field(description="The authenticated owner's durable session.")
    items: list[InputSnapshotView] = Field(max_length=100, description="Recent actual host-recorded observations.")


class ApprovedInputSnapshots:
    """Compare only values obtained by the real host-approved read under its active main writer."""

    def __init__(self, grants: ConnectedReadGrants, current: CurrentUser) -> None:
        self._grants, self._current = grants, current

    @staticmethod
    def verified_history(crud: SessionCrud, session: str, key_id: str) -> list[SessionEvent]:
        """Validate the private snapshot history against its installation key.

        Raises:
            HeinzelError: If a saved snapshot is invalid or belongs to a different key.
        """
        events = [
            item for item in crud.repository.control_read(session).events if item.kind == "input-snapshot.observed"
        ]
        for event in events:
            if (
                set(event.data) != {"version", "key_id", "scope", "digest"}
                or type(event.data.get("version")) is not int
                or event.data.get("version") != 1
                or event.data.get("key_id") != key_id
                or not isinstance(event.data.get("scope"), str)
                or not _DIGEST.fullmatch(str(event.data["scope"]))
                or not isinstance(event.data.get("digest"), str)
                or not _DIGEST.fullmatch(str(event.data["digest"]))
            ):
                msg = "The private input snapshot history needs repair"
                raise HeinzelError(msg)
        return events

    def read(
        self, session: str, writer: SessionWriter, *, turn: str | None = None, call: int | None = None
    ) -> tuple[JsonValue, InputSnapshot]:
        """Read and durably record an exact snapshot before returning it to the host.

        Raises:
            HeinzelError: If ownership, approval, recorded key or history cannot be verified.
            ConnectedReadFailedError: If a completed read cannot be durably recorded.
        """
        repo = self._grants.crud.repository
        context = self._current.context()
        if (
            context.principal != self._current.owner()
            or context.principal.id != repo.principal_id
            or not repo.is_main_writer(session, writer)
            or (turn is None) != (call is None)
            or (
                turn is not None
                and (not re.fullmatch(r"interactive-[0-9a-f]{64}", turn) or type(call) is not int or call < 1)
            )
        ):
            msg = "The approved input snapshot is unavailable"
            raise HeinzelError(msg)
        grant = self._grants.approved(session)
        terms = grant.model_dump(mode="json")
        existing = repo.control_read(session).events
        key = _key(self._current, create=not any(item.kind == "input-snapshot.observed" for item in existing))
        key_id = hashlib.sha256(b"heinzel-input-receipt-v1\0" + key).hexdigest()
        self.verified_history(self._grants.crud, session, key_id)
        value = self._grants.read(session, writer)
        try:
            return value, self._record(session, turn, call, key, key_id, terms, value, grant.review.item_identity)
        except Exception:  # noqa: BLE001 - provider read completed; never expose receipt diagnostics
            msg = "The connected read completed but its input snapshot could not be recorded"
            raise ConnectedReadFailedError(msg) from None

    def _record(  # noqa: PLR0913, PLR0917 - exact host turn and read bindings
        self,
        session: str,
        turn: str | None,
        call: int | None,
        key: bytes,
        key_id: str,
        terms: dict[str, JsonValue],
        value: JsonValue,
        item_identity: ConnectedItemIdentity | None,
    ) -> InputSnapshot:
        repo = self._grants.crud.repository
        owner = self._current.context().principal.id
        scope = _fingerprint(key, ["input-scope-v1", owner, session, terms])
        digest = _fingerprint(key, ["input-value-v1", owner, session, terms, value])
        item_values = (
            extract_items(item_identity, value, EvidenceContext(key, scope, turn, call))
            if item_identity is not None and turn is not None and call is not None
            else None
        )
        with repo.control_writer(session) as control:
            if not hmac.compare_digest(
                _fingerprint(key, self._grants.approved(session).model_dump(mode="json")), _fingerprint(key, terms)
            ):
                msg = "The connected read approval changed"
                raise HeinzelError(msg)
            events = self.verified_history(self._grants.crud, session, key_id)
            previous = next((event for event in reversed(events) if event.data["scope"] == scope), None)
            status: Literal["first", "unchanged", "changed"] = (
                "first"
                if previous is None
                else "unchanged"
                if hmac.compare_digest(str(previous.data["digest"]), digest)
                else "changed"
            )
            event = control.append(
                "input-snapshot.observed",
                turn=turn,
                key=f"{turn}-{call}-input" if turn is not None else None,
                data={"version": 1, "key_id": key_id, "scope": scope, "digest": digest},
            )
            references: tuple[ItemReference, ...] = ()
            if item_values is not None and turn is not None and call is not None:
                data: dict[str, JsonValue] = {
                    "version": 1,
                    "scope": scope,
                    "read_seq": event.seq,
                    "call": call,
                    "items": cast("list[JsonValue]", item_values),
                }
                data["mac"] = _fingerprint(key, ["item-observation-v1", turn, data])
                control.append("input-item.observed", turn=turn, key=f"{turn}-{call}-items", data=data)
                references = tuple(
                    ItemReference(index=index, reference=str(item["ref"])) for index, item in enumerate(item_values)
                )
            return InputSnapshot(
                status, event.seq, references, item_identity.collection if item_identity is not None else None
            )


def snapshot_history(crud: SessionCrud, current: CurrentUser, session: str, limit: int = 20) -> InputSnapshotPage:
    """Project actual owner-private recent comparisons without exposing fingerprints or source content.

    Raises:
        HeinzelError: If the owner, bounds, saved history or tool-event provenance cannot be verified.
    """
    context = current.context()
    if (
        context.principal != current.owner()
        or context.principal.id != crud.repository.principal_id
        or context.authentication not in {"cli", "api-bearer", "web-session", "web-device"}
        or "private:read" not in context.permissions
        or type(limit) is not int
        or not 1 <= limit <= _MAX_HISTORY
    ):
        msg = "The input snapshot history is unavailable"
        raise HeinzelError(msg)
    events = crud.repository.control_read(session).events
    if not any(event.kind == "input-snapshot.observed" for event in events):
        return InputSnapshotPage(session_id=session, items=[])
    key = _key(current, create=False)
    key_id = hashlib.sha256(b"heinzel-input-receipt-v1\0" + key).hexdigest()
    observations = ApprovedInputSnapshots.verified_history(crud, session, key_id)
    starts = {
        event.turn: event.seq
        for event in events
        if event.kind == "interactive.started"
        and isinstance(event.data.get("message_id"), str)
        and event.turn == "interactive-" + hashlib.sha256(str(event.data["message_id"]).encode()).hexdigest()
    }
    requested = {
        (event.turn, event.key): event.seq
        for event in events
        if event.kind == "interactive.tool" and event.data == {"tool": "connected_read", "status": "requested"}
    }
    previous: dict[str, str] = {}
    items: list[InputSnapshotView] = []
    for event in observations:
        scope, digest = str(event.data["scope"]), str(event.data["digest"])
        prior = previous.get(scope)
        status: Literal["first", "unchanged", "changed"] = (
            "first" if prior is None else "unchanged" if hmac.compare_digest(prior, digest) else "changed"
        )
        previous[scope] = digest
        if event.turn is None:
            continue  # Early internal prototype records had no host turn provenance.
        if event.key is None or not re.fullmatch(re.escape(event.turn) + r"-[1-9][0-9]*-input", event.key):
            msg = "The input snapshot provenance is invalid"
            raise HeinzelError(msg)
        requested_key = event.key.removesuffix("-input") + "-requested"
        request_seq = requested.get((event.turn, requested_key))
        if event.turn not in starts or request_seq is None or not starts[event.turn] < request_seq < event.seq:
            msg = "The input snapshot has no matching host read"
            raise HeinzelError(msg)
        items.append(InputSnapshotView(seq=event.seq, at=event.ts, turn_id=event.turn, status=status))
    return InputSnapshotPage(session_id=session, items=items[-limit:])
