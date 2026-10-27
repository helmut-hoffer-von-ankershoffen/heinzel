"""Durable personal identity, appearance and inbox acceptance shared by every protected head."""

import base64
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError, field_validator

from heinzel import HeinzelError, JobInactive, NoticeKind, Notifications, SessionEvent
from heinzel.modules.heinzel._session_bodies import BodyRef, immutable_body
from heinzel.modules.heinzel._session_models import (
    ContractVersion,
    HeinzelAppearance,
    MessageAccepted,
    MessageRequest,
    RequestId,
    SessionCosts,
    SessionList,
    SessionMessage,
    SessionSnapshot,
    SessionSummary,
    WakeScheduleView,
)
from heinzel.modules.heinzel._session_protocol import SessionRepository
from heinzel.modules.system.daemon._schedule import next_slot
from heinzel.modules.system.daemon._schedule import problem as schedule_problem

_REQUEST = TypeAdapter(RequestId)
_REVISION = TypeAdapter(int)
MAX_CURSOR = 1024
MAX_PAGE = 200
CHECK_IN_CRON = "*/5 * * * *"
CHECK_IN_TITLE = "Five-minute check-in"
CHECK_IN_CONTRACT = (
    "At each explicitly approved five-minute schedule slot, create one local check-in notification for the owner. "
    "Do not call a model, use external services, or perform other effects."
)
MAX_TIMEZONE_LENGTH = 128


def _json(value: JsonValue) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def _digest(value: JsonValue) -> str:
    return hashlib.sha256(_json(value)).hexdigest()


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
    if state.cron != CHECK_IN_CRON or schedule_problem(state.cron):
        raise ValueError
    ZoneInfo(state.timezone)
    if not any(item.version == state.contract_version for item in contracts):
        raise ValueError
    return state


class SessionCrud:
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
            zone = ZoneInfo(state.timezone)
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
            next_wake=next_slot(state.cron, self.clock(), zone) if state.status == "active" else None,
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

    def schedule_control(
        self, session_id: str, request_id: str, expected_revision: int, action: Literal["activate", "pause", "resume"]
    ) -> "SessionSnapshot":
        """Activate the exact proposal or pause/resume it against the schedule revision the person inspected.

        Raises:
            HeinzelError: The action is invalid, stale, or not allowed in the current state.
        """
        _REQUEST.validate_python(request_id)
        if action not in {"activate", "pause", "resume"}:
            msg = "Choose activate, pause or resume"
            raise HeinzelError(msg)
        if type(expected_revision) is not int or expected_revision < 1:
            msg = "Reload the schedule before changing it"
            raise HeinzelError(msg)
        request_hash = _digest({"expected_revision": expected_revision, "action": action})
        key = f"schedule-control-{request_id}"
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
                if action == "activate":
                    contracts = self._contracts(session_id, events)
                    current = next(item for item in contracts if item.version == state.contract_version)
                    approved = current.model_copy(update={"status": "approved", "approved_at": self.clock()})
                    state = state.model_copy(update={"status": target})
                    body_value = _CheckInRecord(contract=approved, schedule=state).model_dump(mode="json")
                    body = immutable_body(writer, "check-in", body_value, control=True)
                    writer.append(
                        "schedule.activated",
                        key=key,
                        data={"request_hash": request_hash, **body.model_dump(mode="json")},
                    )
                else:
                    state = state.model_copy(update={"status": target})
                    body = immutable_body(writer, "schedule", state.model_dump(mode="json"), control=True)
                    writer.append(
                        f"schedule.{action}",
                        key=key,
                        data={"request_hash": request_hash, **body.model_dump(mode="json")},
                    )
        return self.snapshot(session_id)

    def emit_check_in(
        self,
        session_id: str,
        contract_version: int,
        schedule_revision: int,
        slot: datetime,
        notifications: Notifications,
    ) -> JsonValue | JobInactive:
        """Recheck active state under its control lock, then emit a deterministic owner-only notice."""
        with self.repository.control_writer(session_id):
            events = self.repository.control_read(session_id).events
            state = self._schedule_state(session_id, events)
            revision = self._schedule_revision(events)
            if state is None or state.status != "active" or state.contract_version != contract_version:
                return JobInactive(reason="the check-in schedule is no longer active")
            if revision != schedule_revision:
                return JobInactive(reason="the check-in schedule changed before this slot ran")
            profile, _profile_revision = self._profile(session_id, events)
            slot = slot.astimezone(UTC).replace(second=0, microsecond=0)
            notice = notifications.emit(
                NoticeKind.HEINZEL,
                f"{profile.name} checked in",
                "Your scheduled five-minute check-in is due.",
                route="/heinzel",
                command="hz heinzel sessions",
                key=f"heinzel:{session_id}:check-in:{slot.isoformat()}",
            )
            return {"session_id": session_id, "slot": slot.isoformat(), "notice": notice is not None}

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
            state="awake" if schedule and schedule.status == "active" else "paused" if schedule else "proposed",
            contracts=contracts,
            schedule=schedule,
            last_seq=events[-1].seq if events else 0,
            messages=[self._message(session_id, event) for event in page],
            costs=self.costs(),
            has_more=more,
            next_cursor=next_cursor,
            next_after_seq=page[-1].seq if more else None,
        )

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
                    state="awake" if schedule and schedule.status == "active" else "paused" if schedule else "proposed",
                    last_seq=events[-1].seq,
                    updated_at=events[-1].ts,
                    contract_version=approved,
                )
            )
        return SessionList(
            sessions=sorted(summaries, key=lambda item: (item.updated_at, item.session_id), reverse=True)
        )
