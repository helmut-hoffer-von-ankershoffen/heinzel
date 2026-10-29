"""Durable personal identity, appearance and inbox acceptance shared by every protected head."""

import base64
import hashlib
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from heinzel import HeinzelError, JobInactive, NoticeKind, Notifications, SessionEvent, SessionWriter
from heinzel.kernel._approval import (
    ApprovalEvidence,
    ApprovalIntent,
    ApprovalIssuer,
    ApprovalRecord,
    approval_proof_hash,
    arguments_hash,
    consume_approval,
)
from heinzel.modules.heinzel._session_bodies import BodyRef, immutable_body
from heinzel.modules.heinzel._session_models import (
    ContractVersion,
    HeinzelAppearance,
    MessageAccepted,
    MessageRequest,
    RequestId,
    SessionCosts,
    SessionId,
    SessionList,
    SessionMessage,
    SessionSnapshot,
    SessionSummary,
    WakeScheduleView,
)
from heinzel.modules.heinzel._session_protocol import SessionRepository

_REQUEST = TypeAdapter(RequestId)
_SESSION_ID = TypeAdapter(SessionId)
_REVISION = TypeAdapter(int)
MAX_CURSOR = 1024
MAX_PAGE = 200
MAX_RECENT_HISTORY = 50
CHECK_IN_CRON = "*/5 * * * *"
CHECK_IN_TITLE = "Five-minute check-in"
CHECK_IN_CONTRACT = (
    "At each explicitly approved five-minute schedule slot, create one local check-in notification for the owner. "
    "Do not call a model, use external services, or perform other effects."
)
MAX_TIMEZONE_LENGTH = 128
HASH_LENGTH = 64


def _json(value: JsonValue) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def _digest(value: JsonValue) -> str:
    return hashlib.sha256(_json(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class _CheckInSlot:
    owner: str
    session_id: str
    contract_version: int
    schedule_revision: int
    profile_name: str
    slot: datetime
    journal_seq: int
    active_since: datetime


def _dispatch_check_in_slot(
    writer: SessionWriter,
    notifications: Notifications,
    request: _CheckInSlot,
    events: list[SessionEvent],
) -> JsonValue | JobInactive:
    """Resolve one pending slot with a constant-size, crash-safe checkpoint.

    Raises:
        HeinzelError: If the checkpoint or notification store cannot safely record this slot.
    """
    checkpoint_data = writer.checkpoint("checkin-outbox")
    checkpoint = (
        _CheckInCheckpoint.model_validate(checkpoint_data, strict=False)
        if checkpoint_data is not None
        else _CheckInCheckpoint(version=1)
    )
    if checkpoint.journal_seq > request.journal_seq:
        msg = "The check-in journal moved behind its delivery checkpoint; repair it before continuing"
        raise HeinzelError(msg)
    if checkpoint.journal_seq < request.journal_seq:
        checkpoint = _reconcile_legacy_check_in_checkpoint(writer, notifications, checkpoint, events, request)
    if checkpoint.cleanup_key is not None:
        if not notifications.forget_completed({checkpoint.cleanup_key}):
            msg = "The completed check-in receipt could not be compacted; retry after repairing notifications"
            raise HeinzelError(msg)
        checkpoint = checkpoint.model_copy(update={"cleanup_key": None})
        writer.replace_checkpoint("checkin-outbox", checkpoint.model_dump(mode="json"))

    checkpoint = _reconcile_stale_check_in(writer, notifications, checkpoint, request)
    pending = checkpoint.pending
    if pending is None:
        if checkpoint.last_completed is not None and request.slot <= checkpoint.last_completed:
            return {"session_id": request.session_id, "slot": request.slot.isoformat(), "notice": True}
        notice_key = (
            f"heinzel:{request.owner}:{request.session_id}:{request.contract_version}:check-in:"
            f"{request.slot.isoformat()}"
        )
        pending = _CheckInIntent(
            slot=request.slot,
            schedule_revision=request.schedule_revision,
            contract_version=request.contract_version,
            notice_key=notice_key,
            title=f"{request.profile_name} checked in",
            body="Your scheduled five-minute check-in is due.",
            route=f"/heinzel?chat={request.session_id}",
            command="hz heinzel sessions",
        )
        checkpoint = checkpoint.model_copy(update={"pending": pending})
        writer.replace_checkpoint("checkin-outbox", checkpoint.model_dump(mode="json"))
    elif pending.slot > request.slot:
        return JobInactive(reason="an earlier compiled check-in cannot move the durable slot backwards")

    # A pending slot is retried before a newer slot. Its persisted payload remains exact across rename/restart;
    # only this one intent is kept, so a long outage never backfills every missed five-minute slot.
    pending_slot = pending.slot
    if not notifications.emit_once(
        NoticeKind.HEINZEL,
        pending.title,
        pending.body,
        route=pending.route,
        command=pending.command,
        key=pending.notice_key,
    ):
        msg = "The scheduled check-in notification could not be recorded"
        raise HeinzelError(msg)
    checkpoint = checkpoint.model_copy(
        update={"last_completed": pending.slot, "pending": None, "cleanup_key": pending.notice_key}
    )
    writer.replace_checkpoint("checkin-outbox", checkpoint.model_dump(mode="json"))
    if not notifications.forget_completed({pending.notice_key}):
        msg = "The check-in was recorded, but its receipt cleanup is pending"
        raise HeinzelError(msg)
    checkpoint = checkpoint.model_copy(update={"cleanup_key": None})
    writer.replace_checkpoint("checkin-outbox", checkpoint.model_dump(mode="json"))
    return {"session_id": request.session_id, "slot": pending_slot.isoformat(), "notice": True}


def _reconcile_legacy_check_in_checkpoint(
    writer: SessionWriter,
    notifications: Notifications,
    checkpoint: "_CheckInCheckpoint",
    events: list[SessionEvent],
    request: _CheckInSlot,
) -> "_CheckInCheckpoint":
    updated, completed_notice_keys = _reconcile_legacy_check_in_events(
        checkpoint,
        [event for event in events if event.seq > checkpoint.journal_seq],
        request.journal_seq,
        request.schedule_revision,
    )
    # Completed legacy records are authoritative. Compact their notification receipts
    # before advancing the checkpoint; if the following checkpoint write fails, the
    # next attempt repeats this idempotent cleanup and replays no notification.
    if completed_notice_keys and not notifications.forget_completed(set(completed_notice_keys)):
        msg = "Legacy completed check-in receipts could not be compacted; repair notifications before continuing"
        raise HeinzelError(msg)
    writer.replace_checkpoint("checkin-outbox", updated.model_dump(mode="json"))
    return updated


def _reconcile_stale_check_in(
    writer: SessionWriter,
    notifications: Notifications,
    checkpoint: "_CheckInCheckpoint",
    request: _CheckInSlot,
) -> "_CheckInCheckpoint":
    """Resolve an older pending slot across a pause/resume without posting after the new activation.

    Raises:
        HeinzelError: If the prior notice store cannot determine whether the side effect already happened.
    """
    pending = checkpoint.pending
    if pending is None or (
        pending.schedule_revision == request.schedule_revision
        and pending.slot.astimezone(UTC) >= request.active_since.astimezone(UTC)
    ):
        return checkpoint
    already_recorded = notifications.has_once(pending.notice_key)
    if already_recorded is None:
        msg = "The prior check-in outcome cannot be inspected safely; repair notifications before continuing"
        raise HeinzelError(msg)
    last_completed = checkpoint.last_completed
    if already_recorded and (last_completed is None or pending.slot > last_completed):
        last_completed = pending.slot
    checkpoint = checkpoint.model_copy(
        update={
            "last_completed": last_completed,
            "pending": None,
            "cleanup_key": pending.notice_key if already_recorded else None,
        }
    )
    writer.replace_checkpoint("checkin-outbox", checkpoint.model_dump(mode="json"))
    if already_recorded:
        if not notifications.forget_completed({pending.notice_key}):
            msg = "The prior check-in is recorded, but its receipt cleanup is pending"
            raise HeinzelError(msg)
        checkpoint = checkpoint.model_copy(update={"cleanup_key": None})
        writer.replace_checkpoint("checkin-outbox", checkpoint.model_dump(mode="json"))
    return checkpoint


class PersonalProfile(BaseModel):
    """Private profile body, never sensitive display text in spine metadata or the creation catalog."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    name: str = Field(min_length=1, max_length=100)
    appearance: HeinzelAppearance = Field(default_factory=HeinzelAppearance)

    @field_validator("name")
    @classmethod
    def meaningful_name(cls, value: str) -> str:
        """Keep personal names readable.

        Raises:
            ValueError: If the name contains no visible text.
        """
        if not value.strip():
            msg = "Give this Heinzel a name"
            raise ValueError(msg)
        return value.strip()


class _Cursor(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    v: int = Field(ge=1, le=1)
    session_id: str
    after: int = Field(ge=0)


class _CheckInIntent(BaseModel):
    """One immutable, bounded payload held only while a slot's notification is unresolved."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    slot: datetime
    schedule_revision: int = Field(ge=1)
    contract_version: int = Field(ge=1)
    notice_key: str = Field(min_length=1, max_length=512)
    title: str = Field(min_length=1, max_length=256)
    body: str = Field(max_length=4096)
    route: str = Field(min_length=1, max_length=2048)
    command: str = Field(min_length=1, max_length=2048)


class _CheckInCheckpoint(BaseModel):
    """Constant-size slot watermark, one unresolved intent, and crash-safe receipt cleanup pointer."""

    model_config = ConfigDict(extra="forbid", strict=True)
    version: int = Field(ge=1, le=1)
    journal_seq: int = Field(default=0, ge=0)
    last_completed: datetime | None = None
    pending: _CheckInIntent | None = None
    cleanup_key: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def consistent_watermark(self) -> "_CheckInCheckpoint":
        """Reject impossible states rather than risk replaying or regressing a completed slot.

        Raises:
            ValueError: If timestamps or pending/cleanup state violate the checkpoint ordering.
        """
        if self.last_completed is not None and self.last_completed.utcoffset() is None:
            msg = "check-in watermark must include a timezone"
            raise ValueError(msg)
        if self.pending is not None and self.pending.slot.utcoffset() is None:
            msg = "pending check-in slot must include a timezone"
            raise ValueError(msg)
        if (
            self.pending is not None
            and self.last_completed is not None
            and self.pending.slot.astimezone(UTC) <= self.last_completed.astimezone(UTC)
        ):
            msg = "pending check-in cannot precede its completion watermark"
            raise ValueError(msg)
        if self.cleanup_key is not None and (self.last_completed is None or self.pending is not None):
            msg = "receipt cleanup requires a completed slot and no pending intent"
            raise ValueError(msg)
        return self


def _legacy_text(value: JsonValue) -> str:
    """Read a required string from a legacy event without coercing malformed payloads.

    Raises:
        HeinzelError: If the field is not a string.
    """
    if not isinstance(value, str):
        msg = "Legacy check-in delivery history has invalid field types; inspect it before continuing"
        raise HeinzelError(msg)
    return value


def _parse_legacy_check_in(event: SessionEvent, revision: int) -> _CheckInIntent:
    """Parse one prior durable intent; fail closed on an older ambiguous intent format.

    Raises:
        HeinzelError: If legacy delivery records are unsupported or malformed.
    """
    data = event.data
    if event.key is None or type(data.get("dedup_version")) is not int or data.get("dedup_version") != 1:
        msg = "Legacy check-in delivery history is ambiguous; inspect it before continuing"
        raise HeinzelError(msg)
    slot = data.get("slot")
    contract_version = data.get("contract_version")
    fields = {name: _legacy_text(data.get(name)) for name in ("notice_key", "title", "body", "route", "command")}
    if not isinstance(slot, str) or type(contract_version) is not int:
        msg = "Legacy check-in delivery history has invalid field types; inspect it before continuing"
        raise HeinzelError(msg)
    try:
        return _CheckInIntent(
            slot=datetime.fromisoformat(slot),
            schedule_revision=revision,
            contract_version=contract_version,
            **fields,
        )
    except (TypeError, ValueError, ValidationError):
        msg = "Legacy check-in delivery history is ambiguous; inspect it before continuing"
        raise HeinzelError(msg) from None


def _reconcile_legacy_check_in_events(
    checkpoint: _CheckInCheckpoint,
    events: list[SessionEvent],
    journal_seq: int,
    schedule_revision: int,
) -> tuple[_CheckInCheckpoint, list[str]]:
    """Fold old append-only delivery records into the bounded checkpoint before dispatch resumes.

    This migration relies on the caller holding the same cooperative per-session control lock used by legacy
    SessionStore writers. It cannot fence a process that appends without taking that lock.

    Raises:
        HeinzelError: If legacy delivery records are ambiguous or malformed.
    """
    intent_revisions = _legacy_intent_revisions(events, schedule_revision)
    intents = {
        event.key: _parse_legacy_check_in(event, intent_revisions[event.seq])
        for event in events
        if event.kind == "check-in.intent"
    }
    completed = {
        event.key: event.data.get("notice_key")
        for event in events
        if event.kind == "check-in.recorded" and event.key is not None
    }
    if any(event.kind == "check-in.recorded" and event.key is None for event in events):
        msg = "Legacy check-in completion has no identity; inspect it before continuing"
        raise HeinzelError(msg)
    if set(completed).difference(intents):
        msg = "Legacy check-in completion has no matching intent; inspect it before continuing"
        raise HeinzelError(msg)
    if any(completed[key] != intents[key].notice_key for key in completed):
        msg = "Legacy check-in completion does not match its intent; inspect it before continuing"
        raise HeinzelError(msg)
    unresolved = [(key, intent) for key, intent in intents.items() if key not in completed]
    if len(unresolved) > 1:
        msg = "Several legacy check-in intents need reconciliation; inspect them before continuing"
        raise HeinzelError(msg)

    result = checkpoint.model_copy(update={"journal_seq": journal_seq})
    completed_notice_keys: list[str] = []
    for key in completed:
        intent = intents[key]
        if result.pending is not None and result.pending.notice_key != intent.notice_key:
            msg = "A legacy completion overlaps an unresolved check-in; inspect it before continuing"
            raise HeinzelError(msg)
        latest = max(filter(None, (result.last_completed, intent.slot)), default=intent.slot)
        result = result.model_copy(update={"last_completed": latest, "pending": None, "cleanup_key": None})
        completed_notice_keys.append(intent.notice_key)
    if unresolved:
        _key, intent = unresolved[0]
        if result.pending is not None and result.pending.notice_key != intent.notice_key:
            msg = "A legacy check-in intent overlaps the bounded outbox; inspect it before continuing"
            raise HeinzelError(msg)
        if result.last_completed is not None and intent.slot <= result.last_completed:
            msg = "A legacy check-in intent predates its completion watermark; inspect it before continuing"
            raise HeinzelError(msg)
        result = result.model_copy(update={"pending": intent, "cleanup_key": None})
    return _CheckInCheckpoint.model_validate(result.model_dump(mode="python")), completed_notice_keys


def _legacy_intent_revisions(events: list[SessionEvent], current_revision: int) -> dict[int, int]:
    """Recover each append-only intent's schedule revision from surrounding journal transitions."""
    revisions: dict[int, int] = {}
    revision = current_revision
    for event in reversed(events):
        if event.kind == "check-in.intent":
            revisions[event.seq] = revision
        if event.kind == "contract.proposed" or event.kind.startswith("schedule."):
            revision -= 1
    return revisions


class _ScheduleState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    contract_version: int = Field(ge=1)
    cron: str
    timezone: str
    status: Literal["proposed", "active", "paused"]
    preview_hash: str


class _CheckInRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    contract: ContractVersion
    schedule: _ScheduleState


def _validate_schedule_state(state: _ScheduleState, contracts: list[ContractVersion]) -> _ScheduleState:
    if state.cron != CHECK_IN_CRON:
        raise ValueError
    ZoneInfo(state.timezone)
    if not any(item.version == state.contract_version for item in contracts):
        raise ValueError
    return state


def _session_state(schedule: WakeScheduleView | None) -> Literal["proposed", "awake", "paused"]:
    """Project an unapproved schedule as proposed, not as a schedule the person paused."""
    if schedule is None or schedule.status == "proposed":
        return "proposed"
    return "awake" if schedule.status == "active" else "paused"


class SessionCrud(ApprovalIssuer):
    """Persist acceptance before acknowledgement; no provider calls or approval grants happen here."""

    def __init__(
        self,
        repository: SessionRepository,
        costs: Callable[[], SessionCosts],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Use trusted per-call ownership and current policy; never cache another request's principal."""
        self.repository = repository
        self.costs = costs
        self.clock = clock

    def new_chat(
        self, request_id: str, name: str = "New Heinzel", appearance: HeinzelAppearance | None = None
    ) -> SessionSnapshot:
        """Reserve identity, then initialize private profile; a retry finishes any interrupted initialization."""
        _REQUEST.validate_python(request_id)
        profile = PersonalProfile.model_validate_json(
            PersonalProfile(name=name, appearance=appearance or HeinzelAppearance()).model_dump_json()
        )
        session_id = self.repository.reserve(request_id, _digest(profile.model_dump(mode="json")))
        try:
            existing = self.repository.control_read(session_id).events
        except FileNotFoundError:
            existing = []
        if any(event.kind == "profile.updated" for event in existing):
            return self.snapshot(session_id)
        # Creation alone takes main then control, matching active turns. The reservation lock is already released.
        with self.repository.main_writer(session_id, wait=True) as main:
            if not self.repository.main_read(session_id).events:
                main.append("session.created", key="created", data={"owner_id": self.repository.principal_id})
            with self.repository.control_writer(session_id) as control:
                if not self.repository.control_read(session_id).events:
                    body = immutable_body(control, "profile", profile.model_dump(mode="json"), control=True)
                    control.append(
                        "profile.updated", key="created", data={"revision": 1, **body.model_dump(mode="json")}
                    )
        return self.snapshot(session_id)

    def _profile(self, session_id: str, events: list[SessionEvent]) -> tuple[PersonalProfile, int]:
        event = next((item for item in reversed(events) if item.kind == "profile.updated"), None)
        if event is None:
            msg = "This Heinzel's creation is incomplete; retry its original creation request"
            raise HeinzelError(msg)
        value = self._body(session_id, event)
        try:
            profile = PersonalProfile.model_validate_json(_json(value))
            revision = _REVISION.validate_python(event.data["revision"], strict=True)
        except (ValidationError, ValueError, KeyError):
            msg = "This Heinzel's saved profile is invalid; restore its private snapshot"
            raise HeinzelError(msg) from None
        if revision < 1:
            msg = "This Heinzel's saved profile revision is invalid"
            raise HeinzelError(msg)
        return profile, revision

    def _body(self, session_id: str, event: SessionEvent) -> JsonValue:
        try:
            ref = BodyRef.model_validate({key: event.data.get(key) for key in ("source", "body_id", "body_hash")})
        except ValidationError:
            msg = "This session checkpoint has an invalid private body reference"
            raise HeinzelError(msg) from None
        body = self.repository.body(
            session_id, ref.body_id, expected_hash=ref.body_hash, control=ref.source == "control"
        )
        if len(body) != 1:
            msg = "This session checkpoint is not a complete immutable private snapshot"
            raise HeinzelError(msg)
        return body[0]

    def profile(
        self,
        session_id: str,
        request_id: str,
        expected_revision: int,
        *,
        name: str | None = None,
        appearance: HeinzelAppearance | None = None,
    ) -> SessionSnapshot:
        """Replace explicit personal fields against the exact version the user saw, including default inheritance.

        Raises:
            HeinzelError: If a replay changes its payload or the displayed revision is stale.
        """
        _REQUEST.validate_python(request_id)
        appearance = (
            HeinzelAppearance.model_validate_json(appearance.model_dump_json()) if appearance is not None else None
        )
        request: JsonValue = {
            "expected_revision": expected_revision,
            "name": name,
            "appearance": appearance.model_dump(mode="json") if appearance is not None else None,
        }
        digest = _digest(request)
        with self.repository.control_writer(session_id) as writer:
            events = self.repository.control_read(session_id).events
            prior = next((item for item in events if item.key == f"profile-{request_id}"), None)
            if prior is not None:
                if prior.data.get("request_hash") != digest:
                    msg = "This request already identifies a different profile update"
                    raise HeinzelError(msg)
            else:
                current, revision = self._profile(session_id, events)
                if type(expected_revision) is not int or expected_revision != revision:
                    msg = "This Heinzel's profile changed; reload before saving your appearance"
                    raise HeinzelError(msg)
                profile = PersonalProfile(
                    name=current.name if name is None else name,
                    appearance=current.appearance if appearance is None else appearance,
                )
                body = immutable_body(writer, "profile", profile.model_dump(mode="json"), control=True)
                writer.append(
                    "profile.updated",
                    key=f"profile-{request_id}",
                    data={"revision": revision + 1, "request_hash": digest, **body.model_dump(mode="json")},
                )
        return self.snapshot(session_id)

    def accept(self, session_id: str, request: MessageRequest) -> MessageAccepted:
        """Record the exact inbox request before model work, preserving request identity across lost replies.

        Raises:
            HeinzelError: If uploads are not retained or a replay changes accepted content.
        """
        request = MessageRequest.model_validate_json(request.model_dump_json())
        if request.attachment_ids:
            msg = "Attachments need a retained upload before they can be sent in this conversation"
            raise HeinzelError(msg)
        digest = _digest(request.model_dump(mode="json"))
        with self.repository.control_writer(session_id) as writer:
            events = self.repository.control_read(session_id).events
            prior = next((event for event in events if event.key == f"message-{request.request_id}"), None)
            if prior is not None:
                if prior.kind != "message.accepted" or prior.data.get("request_hash") != digest:
                    msg = "This request already identifies a different accepted message"
                    raise HeinzelError(msg)
                event = prior
            else:
                body = immutable_body(writer, "inbox", request.model_dump(mode="json"), control=True)
                event = writer.append(
                    "message.accepted",
                    key=f"message-{request.request_id}",
                    data={"message_id": request.request_id, "request_hash": digest, **body.model_dump(mode="json")},
                )
        return MessageAccepted(
            request_id=request.request_id, session_id=session_id, disposition="queued", inbox_position=event.seq
        )

    def _contracts(self, session_id: str, events: list[SessionEvent]) -> list[ContractVersion]:
        versions: list[ContractVersion] = []
        for event in events:
            if event.kind not in {"contract.proposed", "schedule.activated"}:
                continue
            try:
                contract = _CheckInRecord.model_validate(self._body(session_id, event)).contract
            except (TypeError, ValueError, ValidationError):
                msg = "This Heinzel's saved contract version is invalid; restore its private snapshot"
                raise HeinzelError(msg) from None
            if event.kind == "contract.proposed":
                versions.append(contract)
            else:
                versions = [contract if item.version == contract.version else item for item in versions]
        return versions

    def _schedule_state(self, session_id: str, events: list[SessionEvent]) -> _ScheduleState | None:
        event = next(
            (
                item
                for item in reversed(events)
                if item.kind == "contract.proposed" or item.kind.startswith("schedule.")
            ),
            None,
        )
        if event is None:
            return None
        try:
            if event.kind in {"contract.proposed", "schedule.activated"}:
                state = _CheckInRecord.model_validate(self._body(session_id, event)).schedule
            else:
                state = _ScheduleState.model_validate(self._body(session_id, event))
            return _validate_schedule_state(state, self._contracts(session_id, events))
        except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError, ValidationError):
            msg = "This Heinzel's saved schedule is invalid; restore its private snapshot"
            raise HeinzelError(msg) from None

    def _schedule_view(self, session_id: str, events: list[SessionEvent]) -> WakeScheduleView | None:
        state = self._schedule_state(session_id, events)
        if state is None:
            return None
        contract = next(item for item in self._contracts(session_id, events) if item.version == state.contract_version)
        revision = self._schedule_revision(events)
        try:
            ZoneInfo(state.timezone)
        except (ValueError, ZoneInfoNotFoundError):
            msg = "This Heinzel's saved schedule has an unknown time zone; propose it again"
            raise HeinzelError(msg) from None
        return WakeScheduleView(
            summary=contract.title,
            cron=state.cron,
            timezone=state.timezone,
            status=state.status,
            contract_version=state.contract_version,
            revision=revision,
            preview_hash=state.preview_hash,
            # A cron boundary is not an actual wake: the daemon may be stopped or ticking too slowly.
            next_wake=None,
            blocked_reason="Wake timing is not verified; check daemon status and cadence"
            if state.status == "active"
            else None,
        )

    @staticmethod
    def _schedule_revision(events: list[SessionEvent]) -> int:
        return sum(event.kind == "contract.proposed" or event.kind.startswith("schedule.") for event in events)

    def propose_check_in(self, session_id: str, request_id: str, timezone: str = "UTC") -> "SessionSnapshot":
        """Store a proposal for one local five-minute check-in; it remains inert until activated by the person.

        Raises:
            HeinzelError: The time zone is invalid or this session already has a check-in contract.
        """
        _REQUEST.validate_python(request_id)
        if len(timezone) > MAX_TIMEZONE_LENGTH or not timezone.strip():
            msg = "Choose an IANA time zone for this schedule"
            raise HeinzelError(msg)
        try:
            ZoneInfo(timezone)
        except (ValueError, ZoneInfoNotFoundError):
            msg = f"Unknown IANA time zone {timezone!r}"
            raise HeinzelError(msg) from None
        payload: JsonValue = {
            "title": CHECK_IN_TITLE,
            "text": CHECK_IN_CONTRACT,
            "cron": CHECK_IN_CRON,
            "timezone": timezone,
        }
        request_hash = _digest(payload)
        with self.repository.control_writer(session_id) as writer:
            events = self.repository.control_read(session_id).events
            prior = next(
                (event for event in events if event.kind == "contract.proposed" and event.key == request_id), None
            )
            if prior is not None:
                if prior.data.get("request_hash") != request_hash:
                    msg = "This request already identifies a different contract proposal"
                    raise HeinzelError(msg)
            else:
                if any(event.kind == "contract.proposed" for event in events):
                    msg = "This first slice supports one immutable check-in contract per Heinzel"
                    raise HeinzelError(msg)
                contract = ContractVersion(
                    id="check-in-v1",
                    version=1,
                    sha256=request_hash,
                    status="proposed",
                    title=CHECK_IN_TITLE,
                    text=CHECK_IN_CONTRACT,
                    created_at=self.clock(),
                )
                state = _ScheduleState(
                    contract_version=1,
                    cron=CHECK_IN_CRON,
                    timezone=timezone,
                    status="proposed",
                    preview_hash=request_hash,
                )
                body = immutable_body(
                    writer,
                    "check-in",
                    _CheckInRecord(contract=contract, schedule=state).model_dump(mode="json"),
                    control=True,
                )
                writer.append(
                    "contract.proposed",
                    key=request_id,
                    data={"request_hash": request_hash, **body.model_dump(mode="json")},
                )
        return self.snapshot(session_id)

    def schedule_control(  # noqa: PLR0913, PLR0917, PLR0915, C901 - revisioned state and proof gate share one boundary
        self,
        session_id: str,
        request_id: str,
        expected_revision: int,
        action: Literal["activate", "pause", "resume"],
        approval_proof: str | None = None,
        expected_contract_version: int = 1,
        expected_preview_hash: str | None = None,
    ) -> "SessionSnapshot":
        """Activate the exact proposal or pause/resume it against the schedule revision the person inspected.

        Raises:
            HeinzelError: The action is invalid, stale, or not allowed in the current state.
        """
        _REQUEST.validate_python(request_id)
        if action not in {"activate", "pause", "resume"}:
            msg = "Choose activate, pause or resume"
            raise HeinzelError(msg)
        if type(expected_contract_version) is not int or expected_contract_version < 1:
            msg = "Reload the schedule before changing it"
            raise HeinzelError(msg)
        if type(expected_revision) is not int or expected_revision < 1:
            msg = "Reload the schedule before changing it"
            raise HeinzelError(msg)
        request_hash = _digest(
            {"expected_revision": expected_revision, "action": action}
            if action == "pause"
            else {
                "expected_revision": expected_revision,
                "action": action,
                "expected_contract_version": expected_contract_version,
                "expected_preview_hash": expected_preview_hash,
            }
        )
        key = f"schedule-control-{request_id}"
        if action in {"activate", "resume"}:
            event_kind = "schedule.activated" if action == "activate" else "schedule.resume"
            if self._has_recorded_schedule_control(session_id, request_id, request_hash, event_kind):
                return self.snapshot(session_id)
            if approval_proof is None or expected_preview_hash is None:
                msg = "Approval is invalid or unavailable"
                raise HeinzelError(msg)
            requested = ApprovalIntent(
                owner=self.repository.principal_id,
                audience="personal-check-in",
                session=session_id,
                action=action,
                resource="five-minute-check-in",
                arguments_hash=arguments_hash(
                    {
                        "request_id": request_id,
                        "expected_revision": expected_revision,
                        "contract_version": expected_contract_version,
                        "preview_hash": expected_preview_hash,
                    }
                ),
                contract_revision=expected_contract_version,
                grant_revision=0,
                policy_revision=0,
            )

            def current_intent(events: list[SessionEvent]) -> ApprovalIntent:
                state = self._schedule_state(session_id, events)
                revision = self._schedule_revision(events)
                if (
                    state is None
                    or revision != expected_revision
                    or state.contract_version != expected_contract_version
                    or state.preview_hash != expected_preview_hash
                ):
                    msg = "This schedule changed; reload before controlling it"
                    raise HeinzelError(msg)
                expected_status = "proposed" if action == "activate" else "paused"
                if state.status != expected_status:
                    msg = f"Cannot {action} a schedule that is {state.status}"
                    raise HeinzelError(msg)
                return requested

            def effect_data(
                writer: SessionWriter, events: list[SessionEvent], _evidence: ApprovalEvidence
            ) -> dict[str, JsonValue]:
                state = self._schedule_state(session_id, events)
                if state is None:
                    msg = "Propose a check-in before activating its schedule"
                    raise HeinzelError(msg)
                target = state.model_copy(update={"status": "active"})
                if action == "activate":
                    contracts = self._contracts(session_id, events)
                    current = next(item for item in contracts if item.version == state.contract_version)
                    approved = current.model_copy(update={"status": "approved", "approved_at": self.clock()})
                    body = immutable_body(
                        writer,
                        "check-in",
                        _CheckInRecord(contract=approved, schedule=target).model_dump(mode="json"),
                        control=True,
                    )
                else:
                    body = immutable_body(writer, "schedule", target.model_dump(mode="json"), control=True)
                return {"request_id": request_id, "request_hash": request_hash, **body.model_dump(mode="json")}

            consume_approval(
                self.repository,
                session_id,
                approval_proof,
                requested,
                current_intent,
                self.clock,
                event_kind=event_kind,
                effect_data=effect_data,
                event_key=key,
                idempotency_hash=request_hash,
            )
            return self.snapshot(session_id)

        with self.repository.control_writer(session_id) as writer:
            events = self.repository.control_read(session_id).events
            prior = next((item for item in events if item.key == key), None)
            if prior is not None:
                if prior.data.get("request_hash") != request_hash:
                    msg = "This request already identifies a different schedule control"
                    raise HeinzelError(msg)
            else:
                state = self._schedule_state(session_id, events)
                if state is None:
                    msg = "Propose a check-in before activating its schedule"
                    raise HeinzelError(msg)
                revision = self._schedule_revision(events)
                if expected_revision != revision:
                    msg = "This schedule changed; reload before controlling it"
                    raise HeinzelError(msg)
                transitions = {
                    "activate": ("proposed", "active"),
                    "pause": ("active", "paused"),
                    "resume": ("paused", "active"),
                }
                expected, target = transitions[action]
                if state.status != expected:
                    msg = f"Cannot {action} a schedule that is {state.status}"
                    raise HeinzelError(msg)
                state = state.model_copy(update={"status": target})
                body = immutable_body(writer, "schedule", state.model_dump(mode="json"), control=True)
                writer.append(
                    f"schedule.{action}",
                    key=key,
                    data={"request_hash": request_hash, **body.model_dump(mode="json")},
                )
        return self.snapshot(session_id)

    def _has_recorded_schedule_control(
        self, session_id: str, request_id: str, request_hash: str, event_kind: str
    ) -> bool:
        """Find the exact owner-journal disposition for one request id, denying mismatched reuse.

        Raises:
            HeinzelError: If the request id identifies another action or the journal is ambiguous.
        """
        key = f"schedule-control-{request_id}"
        with self.repository.control_writer(session_id):
            prior = [event for event in self.repository.control_read(session_id).events if event.key == key]
        if not prior:
            return False
        if (
            len(prior) != 1
            or prior[0].kind != event_kind
            or prior[0].data.get("request_hash") != request_hash
            or prior[0].data.get("request_id") != request_id
        ):
            msg = "This request already identifies a different schedule control"
            raise HeinzelError(msg)
        return True

    def review(self, module: str, operation: str, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Describe the exact current schedule to its owner without creating approval state.

        Raises:
            HeinzelError: If the action is unsupported, foreign or stale.
        """
        invalid = "Approval is invalid or unavailable"
        if module != "heinzel" or operation != "schedule-control" or "approval_proof" in arguments:
            raise HeinzelError(invalid)
        try:
            session_id = _SESSION_ID.validate_python(arguments["session_id"])
            request_id = str(_REQUEST.validate_python(arguments["request_id"]))
            action = arguments["action"]
            revision = arguments["expected_revision"]
            contract_version = arguments["expected_contract_version"]
            preview_hash = arguments["expected_preview_hash"]
        except (KeyError, TypeError, ValueError, ValidationError):
            raise HeinzelError(invalid) from None
        if (
            not isinstance(action, str)
            or action not in {"activate", "resume"}
            or type(revision) is not int
            or revision < 1
            or type(contract_version) is not int
            or contract_version < 1
            or not isinstance(preview_hash, str)
            or len(preview_hash) != HASH_LENGTH
            or any(char not in "0123456789abcdef" for char in preview_hash)
        ):
            raise HeinzelError(invalid)
        request_hash = _digest(
            {
                "expected_revision": revision,
                "action": action,
                "expected_contract_version": contract_version,
                "expected_preview_hash": preview_hash,
            }
        )
        event_kind = "schedule.activated" if action == "activate" else "schedule.resume"
        if self._has_recorded_schedule_control(session_id, request_id, request_hash, event_kind):
            msg = "This exact schedule action is already recorded"
            raise HeinzelError(msg)
        snapshot = self.snapshot(session_id)
        schedule = snapshot.schedule
        expected_status = "proposed" if action == "activate" else "paused"
        if (
            schedule is None
            or schedule.revision != revision
            or schedule.contract_version != contract_version
            or schedule.preview_hash != preview_hash
            or schedule.status != expected_status
        ):
            msg = "This schedule changed; reload before reviewing it"
            raise HeinzelError(msg)
        return {
            "name": snapshot.name,
            "action": action,
            "summary": schedule.summary,
            "cron": schedule.cron,
            "timezone": schedule.timezone,
            "status": schedule.status,
            "revision": schedule.revision,
            "contract_version": schedule.contract_version,
            "preview_hash": schedule.preview_hash,
            "request_id": request_id,
        }

    def issue(self, module: str, operation: str, arguments: dict[str, JsonValue], provenance: str) -> str:
        """Issue a short-lived check-in proof after ChatService confirms its stored action.

        The only supported caller is the kernel's non-operation `ApprovalIssuer` service, reached by trusted CLI/web
        confirmation, a CLI/web MCP-authorization boundary, or the stdio MCP head after terminal confirmation.
        Recheck the authoritative schedule under its writer lock.

        Raises:
            HeinzelError: If the proposal is not a supported check-in action or its preview has changed.
        """
        invalid = "Approval is invalid or unavailable"
        if module != "heinzel" or operation != "schedule-control" or "approval_proof" in arguments:
            raise HeinzelError(invalid)
        try:
            session_id = _SESSION_ID.validate_python(arguments["session_id"])
            request_id = str(_REQUEST.validate_python(arguments["request_id"]))
            action = arguments["action"]
            revision = arguments["expected_revision"]
            contract_version = arguments["expected_contract_version"]
            preview_hash = arguments["expected_preview_hash"]
        except (KeyError, TypeError, ValueError, ValidationError):
            raise HeinzelError(invalid) from None
        if (
            not isinstance(action, str)
            or action not in {"activate", "resume"}
            or type(revision) is not int
            or revision < 1
            or type(contract_version) is not int
            or contract_version < 1
            or not isinstance(preview_hash, str)
            or len(preview_hash) != HASH_LENGTH
            or any(char not in "0123456789abcdef" for char in preview_hash)
        ):
            raise HeinzelError(invalid)

        intent = ApprovalIntent(
            owner=self.repository.principal_id,
            audience="personal-check-in",
            session=session_id,
            action=action,
            resource="five-minute-check-in",
            arguments_hash=arguments_hash(
                {
                    "request_id": request_id,
                    "expected_revision": revision,
                    "contract_version": contract_version,
                    "preview_hash": preview_hash,
                }
            ),
            contract_revision=contract_version,
            grant_revision=0,
            policy_revision=0,
        )
        proof = secrets.token_urlsafe(32)
        digest = approval_proof_hash(proof)
        now = self.clock()
        record = ApprovalRecord(
            intent=intent,
            proof_hash=digest,
            nonce=secrets.token_hex(32),
            provenance=provenance[:128],
            decision="approve",
            issued_at=now,
            expires_at=now + timedelta(minutes=5),
        )
        with self.repository.control_writer(session_id) as writer:
            events = self.repository.control_read(session_id).events
            state = self._schedule_state(session_id, events)
            current_status = "proposed" if action == "activate" else "paused"
            if (
                state is None
                or self._schedule_revision(events) != revision
                or state.contract_version != contract_version
                or state.preview_hash != preview_hash
                or state.status != current_status
            ):
                msg = "This schedule changed; reload before approving it"
                raise HeinzelError(msg)
            writer.append("approval.issued", key=digest, data=record.model_dump(mode="json"))
        return proof

    def applied(self, module: str, operation: str, arguments: dict[str, JsonValue]) -> bool:
        """Check whether an interrupted approval's exact schedule effect was durably recorded.

        Raises:
            HeinzelError: If this is not a supported schedule action or its journal cannot be read safely.
        """
        invalid = "This interrupted action cannot be reconciled safely"
        if module != "heinzel" or operation != "schedule-control" or "approval_proof" in arguments:
            raise HeinzelError(invalid)
        try:
            session_id = _SESSION_ID.validate_python(arguments["session_id"])
            request_id = _REQUEST.validate_python(arguments["request_id"])
            action = arguments["action"]
            revision = arguments["expected_revision"]
            contract_version = arguments["expected_contract_version"]
            preview_hash = arguments["expected_preview_hash"]
        except (KeyError, TypeError, ValueError, ValidationError):
            raise HeinzelError(invalid) from None
        if (
            not isinstance(action, str)
            or action not in {"activate", "resume"}
            or type(revision) is not int
            or type(contract_version) is not int
            or not isinstance(preview_hash, str)
        ):
            raise HeinzelError(invalid)
        request_hash = _digest(
            {
                "expected_revision": revision,
                "action": action,
                "expected_contract_version": contract_version,
                "expected_preview_hash": preview_hash,
            }
        )
        event_kind = "schedule.activated" if action == "activate" else "schedule.resume"
        with self.repository.control_writer(session_id):
            events = self.repository.control_read(session_id).events
            matching = [event for event in events if event.key == f"schedule-control-{request_id}"]
            if not matching:
                return False
            if len(matching) != 1:
                raise HeinzelError(invalid)
            event = matching[0]
            return event.kind == event_kind and event.data.get("request_hash") == request_hash

    def emit_check_in(
        self,
        session_id: str,
        contract_version: int,
        schedule_revision: int,
        slot: datetime,
        notifications: Notifications,
    ) -> JsonValue | JobInactive:
        """Recheck active state under its control lock, then emit a deterministic owner-only notice.

        Raises:
            HeinzelError: If the notification cannot be recorded.
        """
        with self.repository.control_writer(session_id) as writer:
            events = self.repository.control_read(session_id).events
            state = self._schedule_state(session_id, events)
            revision = self._schedule_revision(events)
            if state is None or state.status != "active" or state.contract_version != contract_version:
                return JobInactive(reason="the check-in schedule is no longer active")
            if revision != schedule_revision:
                return JobInactive(reason="the check-in schedule changed before this slot ran")
            active_since = next(
                (event.ts for event in reversed(events) if event.kind in {"schedule.activated", "schedule.resume"}),
                None,
            )
            if slot.tzinfo is None or slot.utcoffset() is None or active_since is None:
                return JobInactive(reason="the check-in slot or activation time is unavailable")
            if slot.astimezone(UTC) < active_since.astimezone(UTC):
                return JobInactive(reason="the check-in slot predates this active schedule")
            profile, _profile_revision = self._profile(session_id, events)
            slot = slot.astimezone(UTC).replace(second=0, microsecond=0)
            return _dispatch_check_in_slot(
                writer,
                notifications,
                _CheckInSlot(
                    owner=self.repository.principal_id,
                    session_id=session_id,
                    contract_version=contract_version,
                    schedule_revision=revision,
                    profile_name=profile.name,
                    slot=slot,
                    journal_seq=events[-1].seq if events else 0,
                    active_since=active_since,
                ),
                events,
            )

    @staticmethod
    def _after(session_id: str, cursor: str | None) -> int:
        if cursor is None:
            return 0
        msg = "This message cursor belongs to a different conversation or is invalid; reopen the session"
        if len(cursor) > MAX_CURSOR:
            raise HeinzelError(msg)
        try:
            raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
            parsed = _Cursor.model_validate_json(raw)
        except (ValueError, ValidationError):
            raise HeinzelError(msg) from None
        if parsed.session_id != session_id:
            raise HeinzelError(msg)
        return parsed.after

    def _message(self, session_id: str, event: SessionEvent) -> SessionMessage:
        try:
            message_id = _REQUEST.validate_python(event.data.get("message_id"), strict=True)
        except ValidationError:
            msg = "This message checkpoint has an invalid message identity"
            raise HeinzelError(msg) from None
        try:
            value = self._body(session_id, event)
        except FileNotFoundError:
            return SessionMessage(
                id=message_id,
                seq=event.seq,
                role="user" if event.kind == "message.accepted" else "assistant",
                at=event.ts,
                status="expired",
                text="This private message body is no longer retained.",
            )
        if event.kind == "message.accepted":
            request = MessageRequest.model_validate_json(_json(value))
            if request.request_id != message_id:
                msg = "This accepted message's identity no longer matches its checkpoint"
                raise HeinzelError(msg)
            return SessionMessage(
                id=request.request_id,
                seq=event.seq,
                role="user",
                text=request.text,
                attachment_ids=request.attachment_ids,
                at=event.ts,
                status="pending",
            )
        message = SessionMessage.model_validate_json(_json(value))
        if message.id != message_id:
            msg = "This reply's immutable message identity no longer matches its checkpoint"
            raise HeinzelError(msg)
        return SessionMessage.model_validate({**message.model_dump(), "seq": event.seq})

    def snapshot(self, session_id: str, *, cursor: str | None = None, limit: int = 50) -> SessionSnapshot:
        """Read profile and an ordered inbox page while a long main turn owns its separate writer lock.

        Raises:
            HeinzelError: If the page size, cursor or authoritative profile is invalid.
        """
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
            msg = "Choose between 1 and 200 messages per page"
            raise HeinzelError(msg)
        after = self._after(session_id, cursor)
        events = self.repository.control_read(session_id).events
        if after > (events[-1].seq if events else 0):
            msg = "This message cursor is ahead of the committed conversation; reopen the session"
            raise HeinzelError(msg)
        profile, revision = self._profile(session_id, events)
        contracts = self._contracts(session_id, events)
        schedule = self._schedule_view(session_id, events)
        approved = next((item.version for item in reversed(contracts) if item.status == "approved"), None)
        messages = [
            event for event in events if event.seq > after and event.kind in {"message.accepted", "message.replied"}
        ]
        page = messages[:limit]
        more = len(messages) > limit
        next_cursor = None
        if more:
            encoded = _Cursor(v=1, session_id=session_id, after=page[-1].seq).model_dump_json().encode()
            next_cursor = base64.urlsafe_b64encode(encoded).decode().rstrip("=")
        return SessionSnapshot(
            session_id=session_id,
            heinzel_id=session_id,
            name=profile.name,
            appearance=profile.appearance,
            profile_revision=revision,
            contract_version=approved,
            state=_session_state(schedule),
            contracts=contracts,
            schedule=schedule,
            last_seq=events[-1].seq if events else 0,
            messages=[self._message(session_id, event) for event in page],
            costs=self.costs(),
            has_more=more,
            next_cursor=next_cursor,
            next_after_seq=page[-1].seq if more else None,
        )

    def recent_history(self, session_id: str, limit: int = 20) -> list[SessionMessage]:
        """Return a bounded tail of durable conversation messages for a user-opened runner chat.

        Raises:
            HeinzelError: If the requested limit is outside the supported range or the session is unavailable.
        """
        if type(limit) is not int or not 1 <= limit <= MAX_RECENT_HISTORY:
            msg = f"Choose between 1 and {MAX_RECENT_HISTORY} recent messages"
            raise HeinzelError(msg)
        events = self.repository.control_read(session_id).events
        messages = [
            self._message(session_id, event)
            for event in events
            if event.kind in {"message.accepted", "message.replied"}
        ]
        return messages[-limit:]

    def listing(self) -> SessionList:
        """Include new uncontracted chats; catalog/control journals never become phantom agents.

        Raises:
            HeinzelError: If an authoritative journal is damaged.
        """
        summaries: list[SessionSummary] = []
        for head in self.repository.heads():
            if head.problem:
                msg = "A personal Heinzel journal needs repair before listing can be completed"
                raise HeinzelError(msg)
            events = self.repository.control_read(head.session).events
            profile, _revision = self._profile(head.session, events)
            contracts = self._contracts(head.session, events)
            schedule = self._schedule_view(head.session, events)
            approved = next((item.version for item in reversed(contracts) if item.status == "approved"), None)
            summaries.append(
                SessionSummary(
                    session_id=head.session,
                    name=profile.name,
                    appearance=profile.appearance,
                    state=_session_state(schedule),
                    last_seq=events[-1].seq,
                    updated_at=events[-1].ts,
                    contract_version=approved,
                )
            )
        return SessionList(
            sessions=sorted(summaries, key=lambda item: (item.updated_at, item.session_id), reverse=True)
        )
