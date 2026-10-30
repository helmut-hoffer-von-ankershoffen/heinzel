"""Authenticated per-entry receipts for internal read-set calls; no provider access or content storage."""

import hashlib
import hmac
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from heinzel import CurrentUser, HeinzelError, SessionEvent
from heinzel.modules.heinzel._input_receipts import (
    ApprovedInputSnapshots,
    input_receipt_fingerprint,
    input_receipt_key,
)
from heinzel.modules.heinzel._session_crud import SessionCrud

if TYPE_CHECKING:
    from heinzel.modules.heinzel._connected_read_set_approvals import ConnectedReadSetApprovals
    from heinzel.modules.heinzel._connected_read_set_reviews import ReadSetReview

_HEX = re.compile(r"^[0-9a-f]{64}$")


class ReadSetCall(BaseModel):
    """Host-owned selector and current-turn call identity, not a bearer credential."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    handle: str = Field(pattern=r"^read-[0-9a-f]{32}$")
    turn: str = Field(pattern=r"^interactive-[0-9a-f]{64}$")
    call: int = Field(ge=1)


def set_input_key(current: CurrentUser, crud: SessionCrud, session: str) -> tuple[bytes, str]:
    """Load the receipt key, never regenerating it if either receipt history exists."""
    events = crud.repository.control_read(session).events
    has_history = any(e.kind in {"input-snapshot.observed", "connected-set-input.observed"} for e in events)
    key = input_receipt_key(current, create=not has_history)
    key_id = hashlib.sha256(b"heinzel-input-receipt-v1\0" + key).hexdigest()
    ApprovedInputSnapshots.verified_history(crud, session, key_id)
    verified_set_inputs(crud, session, key, key_id)
    return key, key_id


def verified_set_inputs(crud: SessionCrud, session: str, key: bytes, key_id: str) -> list[SessionEvent]:
    """Validate every set-input fingerprint and turn/call binding before trusting history.

    Raises:
        HeinzelError: If a receipt is damaged, replayed, or belongs to another key.
    """
    history = crud.repository.control_read(session).events
    events = [e for e in history if e.kind == "connected-set-input.observed"]
    seen: set[tuple[str, int]] = set()
    for event in events:
        data = event.data
        turn, call, handle, scope, digest, mac = (
            data.get(k) for k in ("turn", "call", "handle", "scope", "digest", "mac")
        )
        attempts = [
            item
            for item in history
            if item.kind == "connected-set-input.started"
            and item.key == f"{turn}-{call}-set-started"
            and item.turn == turn
        ]
        if (
            set(data) != {"version", "key_id", "turn", "call", "handle", "scope", "digest", "mac"}
            or type(data.get("version")) is not int
            or data.get("version") != 1
            or data.get("key_id") != key_id
            or not isinstance(turn, str)
            or not re.fullmatch(r"interactive-[0-9a-f]{64}", turn)
            or type(call) is not int
            or call < 1
            or event.turn != turn
            or event.key != f"{turn}-{call}-set-input"
            or (turn, call) in seen
            or len(attempts) != 1
            or not isinstance(handle, str)
            or not re.fullmatch(r"read-[0-9a-f]{32}", handle)
            or attempts[0].data != {"handle": handle}
            or attempts[0].seq >= event.seq
            or not isinstance(scope, str)
            or not _HEX.fullmatch(scope)
            or not isinstance(digest, str)
            or not _HEX.fullmatch(digest)
            or not isinstance(mac, str)
            or not _HEX.fullmatch(mac)
            or not hmac.compare_digest(
                mac,
                input_receipt_fingerprint(
                    key,
                    [
                        "connected-set-input-v1",
                        crud.repository.principal_id,
                        session,
                        {k: v for k, v in data.items() if k != "mac"},
                    ],
                ),
            )
        ):
            msg = "The private read-set input history needs repair"
            raise HeinzelError(msg)
        seen.add((turn, call))
    return events


def verify_set_call(crud: SessionCrud, session: str, call: ReadSetCall, *, started: bool = False) -> ReadSetCall:
    """Require one live host turn and its exact requested call, and reject receipt replay.

    Raises:
        HeinzelError: If the call was not requested in an active host turn or was already observed.
    """
    if type(call) is not ReadSetCall:
        msg = "The read-set call is invalid"
        raise HeinzelError(msg)
    call = ReadSetCall.model_validate(call.model_dump(mode="json"))
    events = crud.repository.control_read(session).events
    starts = [e for e in events if e.kind == "interactive.started" and e.key == call.turn and e.turn == call.turn]
    requests = [
        e
        for e in events
        if e.kind == "interactive.tool"
        and e.key == f"{call.turn}-{call.call}-requested"
        and e.turn == call.turn
        and e.data == {"tool": "connected_read_set", "status": "requested", "handle": call.handle}
    ]
    attempts = [
        e
        for e in events
        if e.kind == "connected-set-input.started"
        and e.key == f"{call.turn}-{call.call}-set-started"
        and e.turn == call.turn
    ]
    message = starts[0].data.get("message_id") if len(starts) == 1 else None
    accepted = [e for e in events if e.kind == "message.accepted" and e.key == f"message-{message}"]
    if (
        not isinstance(message, str)
        or "interactive-" + hashlib.sha256(message.encode()).hexdigest() != call.turn
        or len(accepted) != 1
        or accepted[0].data.get("message_id") != message
        or len(starts) != 1
        or accepted[0].seq >= starts[0].seq
        or len(requests) != 1
        or starts[0].seq >= requests[0].seq
        or (len(attempts) != 1 if started else len(attempts) != 0)
        or (started and (attempts[0].data != {"handle": call.handle} or attempts[0].seq <= requests[0].seq))
        or any(e.kind in {"message.replied", "interactive.abandoned"} and call.turn in (e.turn, e.key) for e in events)
        or any(
            e.kind == "interactive.tool"
            and e.turn == call.turn
            and e.key in {f"{call.turn}-{call.call}-{status}" for status in ("completed", "failed", "denied")}
            for e in events
        )
        or any(
            e.kind == "connected-set-input.observed" and e.turn == call.turn and e.data.get("call") == call.call
            for e in events
        )
        or any(e.kind == "input-snapshot.observed" and e.key == f"{call.turn}-{call.call}-input" for e in events)
    ):
        msg = "The read-set call is unavailable or was already completed"
        raise HeinzelError(msg)
    return call


def start_set_call(crud: SessionCrud, session: str, call: ReadSetCall) -> None:
    """Fence a call before any provider invocation, so interrupted attempts are not replayed."""
    with crud.repository.control_writer(session) as writer:
        verify_set_call(crud, session, call)
        writer.append(
            "connected-set-input.started",
            turn=call.turn,
            key=f"{call.turn}-{call.call}-set-started",
            data={"handle": call.handle},
        )


@dataclass(frozen=True, slots=True)
class ReadSetReceiptBinding:
    """Host-owned proof inputs for one validated set read."""

    call: ReadSetCall
    expected: "ReadSetReview"
    key: bytes = field(repr=False, compare=False)
    key_id: str


def record_set_input(
    approvals: "ConnectedReadSetApprovals", session: str, binding: ReadSetReceiptBinding, value: JsonValue
) -> int:
    """Record only after the guarded read and cleanup; do not retain provider content.

    Raises:
        HeinzelError: If approval or turn authority changed or receipt history is invalid.
    """
    call, expected, key, key_id = binding.call, binding.expected, binding.key, binding.key_id
    crud = approvals.crud
    owner = crud.repository.principal_id
    scope = input_receipt_fingerprint(
        key, ["read-set-scope-v1", owner, session, expected.model_dump(mode="json"), call.handle]
    )
    digest = input_receipt_fingerprint(key, ["read-set-value-v1", owner, session, scope, value])
    with crud.repository.control_writer(session) as writer:
        verify_set_call(crud, session, call, started=True)
        verified_set_inputs(crud, session, key, key_id)
        current = approvals.approved(session)
        if not hmac.compare_digest(
            input_receipt_fingerprint(key, current.model_dump(mode="json")),
            input_receipt_fingerprint(key, expected.model_dump(mode="json")),
        ):
            msg = "The read-set approval changed"
            raise HeinzelError(msg)
        data: dict[str, JsonValue] = {
            "version": 1,
            "key_id": key_id,
            "turn": call.turn,
            "call": call.call,
            "handle": call.handle,
            "scope": scope,
            "digest": digest,
        }
        data["mac"] = input_receipt_fingerprint(key, ["connected-set-input-v1", owner, session, data])
        event = writer.append(
            "connected-set-input.observed", turn=call.turn, key=f"{call.turn}-{call.call}-set-input", data=data
        )
        return event.seq
