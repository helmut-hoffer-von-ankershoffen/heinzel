"""Bounded local notification capability for separately approved scheduled agent turns."""

import contextlib
import hashlib
import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from heinzel import (
    ChannelNotice,
    CurrentUser,
    HeinzelError,
    NoticeHelper,
    NoticeKind,
    NotificationChannel,
    Notifications,
    SessionEvent,
    SessionWriter,
)
from heinzel.kernel._approval import arguments_hash
from heinzel.modules.heinzel._connected_grants import ConnectedReadGrants
from heinzel.modules.heinzel._input_receipts import existing_input_receipt_key
from heinzel.modules.heinzel._item_evidence import ItemLookup, dedupe_key, resolve_current_item
from heinzel.modules.heinzel._notification_proposals import NotificationProposal
from heinzel.modules.heinzel._session_bodies import immutable_body
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionId

_MAX_PAGE = 100
_INCONSISTENT_OUTCOME = "The scheduled notification outcome is inconsistent"


class ItemNotificationPolicy(BaseModel):
    """Separately reviewed source-item deduplication; never a provider access grant."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    deduplicate_by: Literal["item", "revision"] = Field(
        description="At most one attempt per observed identity or revision."
    )
    disclosure: Literal[
        "Only current-turn items count; deduplication is scoped to the approved source and contract."
    ] = Field(
        default="Only current-turn items count; deduplication is scoped to the approved source and contract.",
        description="Reviewed item-evidence and deduplication scope.",
    )


class ScheduledNotificationPolicy(BaseModel):
    """Explicit bounded notification scope; it grants no external messaging or data access."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    max_per_24h: int = Field(ge=1, le=24, description="Maximum local queue attempts per rolling 24 hours.")
    topic_cooldown_seconds: int = Field(
        ge=60, le=2678400, description="Minimum seconds between attempts for the same model-chosen topic."
    )
    item_evidence: ItemNotificationPolicy | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Separately reviewed observed-item policy; absent retains model-chosen topics.",
    )
    channels: tuple[str, ...] = Field(
        default=(),
        max_length=8,
        strict=False,
        exclude_if=lambda value: not value,
        description="Other channels, by name, that also carry each notice; the owner reviews this list.",
    )
    per_turn: Literal[1] = Field(default=1, description="At most one local queue attempt per scheduled turn.")
    delivery: Literal["local-notification"] = Field(
        default="local-notification", description="Only the owner's local in-app or native notification policy."
    )
    disclosure: Literal[
        "Content may appear on this device. Topics are model-chosen, not verified source identities.",
        "Content may appear on this device. Item identities are provider-reported and not completeness guarantees.",
    ] = Field(
        default="Content may appear on this device. Topics are model-chosen, not verified source identities.",
        description="The visibility and deduplication limitation the person reviews.",
    )

    @model_validator(mode="after")
    def matching_disclosure(self) -> "ScheduledNotificationPolicy":
        """Require the reviewed disclosure to match the selected identity policy.

        Raises:
            ValueError: If the disclosure describes a different policy.
        """
        item_text = (
            "Content may appear on this device. Item identities are provider-reported and not completeness guarantees."
        )
        if (self.item_evidence is not None) != (self.disclosure == item_text):
            msg = "The notification disclosure does not match its identity policy"
            raise ValueError(msg)
        return self

    @field_validator("per_turn", mode="before")
    @classmethod
    def exact_one(cls, value: object) -> object:
        """Reject JSON booleans or floats that compare equal to the single allowed integer.

        Raises:
            ValueError: If the value is not exactly integer one.
        """
        if type(value) is not int or value != 1:
            msg = "The per-turn limit must be the integer one"
            raise ValueError(msg)
        return value


class ScheduledNotificationRequest(BaseModel):
    """The model chooses content and topic, not notification route, authority or limits."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    topic: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(max_length=500)


class ItemNotificationRequest(BaseModel):
    """One notification based on a current-turn host-issued item reference."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    item_ref: str = Field(pattern=r"^[0-9a-f]{64}$")
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(max_length=500)


class ScheduledNotifications:
    """Execute a reviewed bounded local notice only under an owner-bound scheduled writer."""

    def __init__(  # noqa: PLR0913 - independent trusted host bindings
        self,
        crud: SessionCrud,
        current: CurrentUser,
        notifications: Notifications,
        *,
        session: str,
        turn: str,
        contract_sha256: str,
        schedule_request: str,
        schedule_sha256: str,
        policy: ScheduledNotificationPolicy,
        verify: Callable[[], None],
        connected: ConnectedReadGrants | None = None,
        channels: Sequence[NotificationChannel] = (),
    ) -> None:
        self.crud, self.current, self.notifications = crud, current, notifications
        self.session, self.turn, self.contract_sha256, self.policy, self.verify = (
            session,
            turn,
            contract_sha256,
            policy,
            verify,
        )
        self.schedule_request, self.schedule_sha256 = schedule_request, schedule_sha256
        self.owner = crud.repository.principal_id
        self.connected = connected
        self.channels = channels

    def _fan_out(self, request: ScheduledNotificationRequest, request_id: str) -> None:
        """Offer the notice to each reviewed channel; a receipt says what it saw, and no failure undoes the notice."""
        profile = self.crud.snapshot(self.session)
        notice = ChannelNotice(
            key=request_id[:128],
            helper=profile.name,
            title=request.title,
            body=request.body,
            route=f"/heinzel?chat={self.session}",
            hat=profile.appearance.hat,
            reason="reviewed scheduled notice",
        )
        for channel in self.channels:
            if channel.name not in self.policy.channels:
                continue
            try:
                receipt = channel.deliver(notice)
                status, reference = receipt.status, receipt.reference
            except Exception:  # noqa: BLE001 - provider diagnostics stay out of the journal
                status, reference = "unknown", None
            with contextlib.suppress(Exception), self.crud.repository.control_writer(self.session) as control:
                control.append(
                    "scheduled-notification.delivered",
                    turn=self.turn,
                    key=f"{request_id}-{channel.name}",
                    data={"channel": channel.name, "status": status, "reference": reference},
                )

    def schema(self) -> dict[str, JsonValue]:
        """Expose only the user's bounded local notification scope, not provider credentials or authority."""
        return {
            "name": "notification:send",
            "description": "Queue a local notice under reviewed limits, using current observed item evidence."
            if self.policy.item_evidence
            else "Queue a local notice under reviewed limits; the topic is model-chosen.",
            "schema": (
                ItemNotificationRequest if self.policy.item_evidence else ScheduledNotificationRequest
            ).model_json_schema(),
        }

    def send(
        self, arguments: dict[str, JsonValue], writer: SessionWriter
    ) -> tuple[Literal["recorded", "uncertain"], str]:
        """Attempt one local queue operation; unknown outcomes are never replayed.

        Raises:
            HeinzelError: If the owner, approval, bounds or earlier topic outcome are invalid.
        """
        context = self.current.context()
        repo = self.crud.repository
        if (
            context.authentication != "daemon"
            or context.principal != self.current.owner()
            or context.principal.id != self.owner
            or repo.principal_id != self.owner
            or not repo.is_main_writer(self.session, writer)
        ):
            msg = "This scheduled notification is not authorized"
            raise HeinzelError(msg)
        self.verify()
        if self.policy.item_evidence is not None:
            item_request = ItemNotificationRequest.model_validate(arguments)
            if self.connected is None:
                msg = "The observed source item is unavailable"
                raise HeinzelError(msg)
            grant = self.connected.approved(self.session)
            terms = grant.model_dump(mode="json")
            item = resolve_current_item(
                self.crud,
                self.current,
                ItemLookup(self.session, self.turn, item_request.item_ref, terms),
                existing_input_receipt_key(self.current),
            )
            item_identity = dedupe_key(item, self.policy.item_evidence.deduplicate_by)
            scoped_identity = hashlib.sha256(f"{self.contract_sha256}\0{item_identity}".encode()).hexdigest()
            request = ScheduledNotificationRequest(
                topic="observed-" + scoped_identity, title=item_request.title, body=item_request.body
            )
        else:
            request = ScheduledNotificationRequest.model_validate(arguments)
        topic = hashlib.sha256(f"{self.owner}\0{self.session}\0{request.topic}".encode()).hexdigest()
        self._check_rate(topic, once=self.policy.item_evidence is not None)
        identity = hashlib.sha256(f"{self.owner}\0{self.session}\0{self.turn}\0{topic}".encode()).hexdigest()
        request_id = "notice-" + identity
        proposal = NotificationProposal(
            session_id=self.session,
            request_id=request_id,
            topic=request.topic,
            title=request.title,
            body=request.body,
        )
        self.verify()
        with repo.control_writer(self.session) as control:
            saved = immutable_body(control, "scheduled-notification", proposal.model_dump(mode="json"), control=True)
            control.append(
                "scheduled-notification.attempted",
                turn=self.turn,
                key=request_id,
                data={
                    "topic_sha256": topic,
                    "policy_sha256": arguments_hash(self.policy.model_dump(mode="json")),
                    "contract_sha256": self.contract_sha256,
                    "schedule_request_id": self.schedule_request,
                    "schedule_sha256": self.schedule_sha256,
                    **saved.model_dump(mode="json"),
                },
            )
        key = "scheduled-notification:" + identity
        try:
            recorded = self.notifications.emit_once_now(
                NoticeKind.HEINZEL,
                request.title,
                request.body,
                route=f"/heinzel?chat={self.session}",
                command="hz heinzel sessions",
                key=key,
                helper=self._helper("reviewed scheduled notice"),
            )
        except Exception:  # noqa: BLE001 - never disclose provider diagnostics
            recorded = False
        if recorded is True:
            try:
                with repo.control_writer(self.session) as control:
                    control.append("scheduled-notification.recorded", turn=self.turn, key=request_id, data={})
            except Exception:  # noqa: BLE001 - the queue may already contain this notice
                return "uncertain", "The notification outcome is uncertain and will not be retried automatically."
            with contextlib.suppress(Exception):
                self.notifications.forget_completed({key})
            self._fan_out(request, request_id)
            return "recorded", "The local notification was queued; it may be held and is not confirmed shown."
        return "uncertain", "The notification outcome is uncertain and will not be retried automatically."

    def _helper(self, reason: str) -> NoticeHelper | None:
        """Whose notice this is, for the pop-up's name and face; None if the profile cannot be read."""
        try:
            snapshot = self.crud.snapshot(self.session, limit=1)
        except Exception:  # noqa: BLE001 - a pop-up without a face is better than a lost notice
            return None
        return NoticeHelper(name=snapshot.name[:100], hat=snapshot.appearance.hat, reason=reason)

    def _check_rate(self, topic: str, *, once: bool = False) -> None:
        now = self.crud.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            msg = "The scheduled notification clock is unavailable"
            raise HeinzelError(msg)
        now = now.astimezone(UTC)
        events = self.crud.repository.control_read(self.session).events
        attempts = [event for event in events if event.kind == "scheduled-notification.attempted"]
        if any(
            event.key is None or event.turn is None or not isinstance(event.data.get("topic_sha256"), str)
            for event in attempts
        ):
            msg = "The scheduled notification history is invalid"
            raise HeinzelError(msg)
        if any(event.ts > now for event in attempts):
            msg = "The notification history is ahead of the host clock"
            raise HeinzelError(msg)
        if any(event.turn == self.turn for event in attempts):
            msg = "This scheduled turn already attempted a notification"
            raise HeinzelError(msg)
        if sum(event.ts > now - timedelta(hours=24) for event in attempts) >= self.policy.max_per_24h:
            msg = "The reviewed notification rate limit has been reached"
            raise HeinzelError(msg)
        for event in attempts:
            if event.data.get("topic_sha256") != topic:
                continue
            self._check_topic_attempt(event, events, now, once)

    def _check_topic_attempt(self, event: SessionEvent, events: list[SessionEvent], now: datetime, once: bool) -> None:
        if once:
            msg = "This source item already has a notification attempt"
            raise HeinzelError(msg)
        view = ScheduledNotificationRecovery(self.crud, self.current, self.notifications).validated_view(
            self.session, event, events
        )
        if view.status == "unresolved":
            msg = "An earlier notification for this topic has an uncertain outcome"
            raise HeinzelError(msg)
        if event.ts > now - timedelta(seconds=self.policy.topic_cooldown_seconds):
            msg = "The reviewed notification topic is still within its cooldown"
            raise HeinzelError(msg)


class ScheduledNotificationView(BaseModel):
    """Owner-private observed attempt; recorded means queued, never confirmed shown."""

    proposal: NotificationProposal = Field(description="Exact private attempted notification content.")
    seq: int = Field(gt=0, description="Exclusive journal cursor.")
    status: Literal["recorded", "unresolved", "abandoned"] = Field(description="Recorded outcome; never OS display.")


class ScheduledNotificationPage(BaseModel):
    """Bounded owner-private attempted notification history."""

    session_id: SessionId = Field(description="The authenticated owner's personal session.")
    items: list[ScheduledNotificationView] = Field(description="Bounded owner-private notification attempts.")
    next_cursor: int | None = Field(description="Exclusive cursor for the next page, if present.")


class ScheduledNotificationInventory:
    """Validate owner-scoped notification attempts without invoking a notification provider."""

    def __init__(self, crud: SessionCrud) -> None:
        self.crud = crud

    def validated_view(
        self, session: str, event: SessionEvent, events: list[SessionEvent]
    ) -> ScheduledNotificationView:
        """Validate an owner-scoped attempted notice against its immutable body and outcome.

        Raises:
            HeinzelError: If the saved identity or outcome cannot be verified.
        """
        outcomes = [
            later
            for later in events
            if later.kind in {"scheduled-notification.recorded", "scheduled-notification.abandoned"}
            and later.key == event.key
        ]
        return self._validated_view(session, event, outcomes)

    def _validated_view(
        self, session: str, event: SessionEvent, outcomes: list[SessionEvent]
    ) -> ScheduledNotificationView:
        try:
            proposal = NotificationProposal.model_validate(self.crud.read_body(session, event))
        except FileNotFoundError:
            return self._view_without_body(session, event, outcomes)
        expected = hashlib.sha256(
            f"{self.crud.repository.principal_id}\0{session}\0{proposal.topic}".encode()
        ).hexdigest()
        expected_id = (
            "notice-"
            + hashlib.sha256(
                f"{self.crud.repository.principal_id}\0{session}\0{event.turn}\0{expected}".encode()
            ).hexdigest()
        )
        if (
            proposal.session_id != session
            or proposal.request_id != event.key
            or proposal.request_id != expected_id
            or event.turn is None
            or not re.fullmatch(r"notice-[0-9a-f]{64}", proposal.request_id)
            or any(
                not isinstance(event.data.get(name), str) or not re.fullmatch(r"[0-9a-f]{64}", str(event.data[name]))
                for name in ("contract_sha256", "schedule_sha256", "policy_sha256")
            )
            or not isinstance(event.data.get("schedule_request_id"), str)
            or event.data.get("topic_sha256") != expected
        ):
            msg = "The scheduled notification record is inconsistent"
            raise HeinzelError(msg)
        return ScheduledNotificationView(proposal=proposal, seq=event.seq, status=self._status(event, outcomes))

    def _view_without_body(
        self, session: str, event: SessionEvent, outcomes: list[SessionEvent]
    ) -> ScheduledNotificationView:
        """Keep an attempt whose private body expired visible, so its owner can still resolve it.

        Raises:
            HeinzelError: If the attempt's own identity is invalid.
        """
        if event.turn is None or not re.fullmatch(r"notice-[0-9a-f]{64}", event.key or ""):
            msg = "The scheduled notification record is inconsistent"
            raise HeinzelError(msg)
        proposal = NotificationProposal(
            session_id=session,
            request_id=str(event.key),
            topic="unavailable",
            title="This notice's private content is no longer retained",
            body="",
        )
        return ScheduledNotificationView(proposal=proposal, seq=event.seq, status=self._status(event, outcomes))

    def _status(
        self, event: SessionEvent, outcomes: list[SessionEvent]
    ) -> Literal["recorded", "unresolved", "abandoned"]:
        """Derive the recorded state of one attempt.

        Raises:
            HeinzelError: If its outcomes are duplicate, out of order or not the owner's.
        """
        if len(outcomes) > 1 or (outcomes and outcomes[0].seq <= event.seq):
            msg = _INCONSISTENT_OUTCOME
            raise HeinzelError(msg)
        if not outcomes:
            return "unresolved"
        outcome = outcomes[0]
        if outcome.kind != "scheduled-notification.abandoned":
            return "recorded"
        if (
            outcome.data.get("owner") != self.crud.repository.principal_id
            or outcome.data.get("origin") not in {"cli", "api-bearer", "web-session", "web-device"}
            or outcome.data.get("acknowledged") is not True
            or outcome.data.get("attempt_seq") != event.seq
        ):
            msg = "The scheduled notification acknowledgment is invalid"
            raise HeinzelError(msg)
        return "abandoned"

    def unresolved_attempts(self, events: list[SessionEvent]) -> list[SessionEvent]:
        """Find attempts without an outcome in one journal scan, reading no private body.

        Raises:
            HeinzelError: If attempts or outcomes are duplicate, orphaned, or invalid.
        """
        attempts: dict[str, SessionEvent] = {}
        outcomes: dict[str, list[SessionEvent]] = {}
        for event in events:
            if event.kind == "scheduled-notification.attempted":
                if event.key is None or event.key in attempts:
                    msg = "The scheduled notification attempts are inconsistent"
                    raise HeinzelError(msg)
                attempts[event.key] = event
            elif event.kind in {"scheduled-notification.recorded", "scheduled-notification.abandoned"}:
                if event.key is None:
                    msg = _INCONSISTENT_OUTCOME
                    raise HeinzelError(msg)
                outcomes.setdefault(event.key, []).append(event)
        if any(key not in attempts for key in outcomes):
            msg = _INCONSISTENT_OUTCOME
            raise HeinzelError(msg)
        return [event for key, event in attempts.items() if self._status(event, outcomes.get(key, [])) == "unresolved"]


class ScheduledNotificationRecovery(ScheduledNotificationInventory):
    """Owner inspection and explicit acknowledgment without ever replaying a notice."""

    def __init__(self, crud: SessionCrud, current: CurrentUser, notifications: Notifications) -> None:
        super().__init__(crud)
        self.current, self.notifications = current, notifications

    def _owner(self, *, write: bool) -> None:
        context = self.current.context()
        required = "private:write" if write else "private:read"
        if (
            context.principal != self.current.owner()
            or context.principal.id != self.crud.repository.principal_id
            or context.authentication not in {"cli", "api-bearer", "web-session", "web-device"}
            or required not in context.permissions
        ):
            msg = "This notification history requires its authenticated owner"
            raise HeinzelError(msg)

    def list(self, session: str, *, cursor: int = 0, limit: int = 20) -> ScheduledNotificationPage:
        """Read owner-scoped attempts, never inferring whether a native banner appeared.

        Raises:
            HeinzelError: If the caller, records or pagination bounds cannot be verified.
        """
        self._owner(write=False)
        if type(cursor) is not int or cursor < 0 or type(limit) is not int or not 1 <= limit <= _MAX_PAGE:
            msg = "The scheduled notification page bounds are invalid"
            raise HeinzelError(msg)
        events = self.crud.repository.control_read(session).events
        attempts = [e for e in events if e.kind == "scheduled-notification.attempted" and e.seq > cursor]
        chosen = attempts[:limit]
        return ScheduledNotificationPage(
            session_id=session,
            items=[self.validated_view(session, event, events) for event in chosen],
            next_cursor=chosen[-1].seq if len(attempts) > limit else None,
        )

    def resolve(self, session: str, request: str, *, acknowledge_uncertain: bool) -> ScheduledNotificationView:
        """Inspect an existing receipt or explicitly set aside uncertainty; never retry or undo it.

        Raises:
            HeinzelError: If the owner, acknowledgment or attempt cannot be verified.
        """
        self._owner(write=True)
        if acknowledge_uncertain is not True:
            msg = "Acknowledge that this notification may already have been queued"
            raise HeinzelError(msg)
        repo = self.crud.repository
        with repo.main_writer(session):
            events = repo.control_read(session).events
            matches = [e for e in events if e.kind == "scheduled-notification.attempted" and e.key == request]
            if len(matches) != 1:
                msg = "The scheduled notification attempt is unavailable"
                raise HeinzelError(msg)
            attempt = matches[0]
            view = self.validated_view(session, attempt, events)
            if view.status != "unresolved":
                return view
            key = "scheduled-notification:" + request.removeprefix("notice-")
            try:
                known = self.notifications.has_once(key)
            except Exception:  # noqa: BLE001 - provider diagnostics may contain private data
                known = None
            with repo.control_writer(session) as writer:
                if known is True:
                    writer.append("scheduled-notification.recorded", turn=attempt.turn, key=request, data={})
                else:
                    writer.append(
                        "scheduled-notification.abandoned",
                        turn=attempt.turn,
                        key=request,
                        data={
                            "owner": repo.principal_id,
                            "origin": self.current.context().authentication,
                            "acknowledged": True,
                            "attempt_seq": attempt.seq,
                        },
                    )
            if known is True:
                with contextlib.suppress(Exception):
                    self.notifications.forget_completed({key})
            return self.validated_view(session, attempt, repo.control_read(session).events)
