"""Internal autonomous schedule foundation; not registered with any daemon or user-facing head.

An occurrence reservation alone is never authority to start a process. A future dispatcher must hold and
recheck the owner, schedule and access fences through launch; existing fixed-notice schedules are unrelated.
"""

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from heinzel import HeinzelError, SessionEvent, SessionWriter
from heinzel.kernel import PRIVATE_WRITE
from heinzel.kernel._approval import (
    ApprovalConfirmation,
    ApprovalEvidence,
    ApprovalIntent,
    ApprovalRecord,
    approval_proof_hash,
    arguments_hash,
    consume_approval,
)
from heinzel.modules.heinzel._connected_grants import ConnectedGrant
from heinzel.modules.heinzel._contract_review import ContractReview
from heinzel.modules.heinzel._interactive_turn import interactive_turn_id
from heinzel.modules.heinzel._scheduled_notifications import ScheduledNotificationPolicy
from heinzel.modules.heinzel._session_bodies import immutable_body
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import MessageRequest, RequestId, SessionId
from heinzel.modules.heinzel._user_chat import MAX_TURN_SECONDS, UserChatService

_MAX_PAGE = 100


class ScheduleBinding(BaseModel):
    """Exact reviewed private schedule; no external tools or account permissions are granted."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    session_id: SessionId = Field(description="Owner-bound personal session.")
    request_id: RequestId = Field(description="Stable schedule proposal request.")
    owner: str = Field(pattern=r"^u-[0-9a-f]{32}$", description="Authenticated owner identity.")
    contract_request_id: RequestId = Field(description="Accepted contract request.")
    contract_revision: int = Field(ge=1, description="Accepted contract revision.")
    contract_sha256: str = Field(pattern=r"^[a-f0-9]{64}$", description="Exact accepted contract digest.")
    cadence_seconds: int = Field(
        ge=60, le=86400, strict=True, description="Whole-minute recurrence interval in seconds."
    )
    max_requests: int = Field(ge=1, le=4, strict=True, description="Maximum model requests per turn.")
    timeout_seconds: int = Field(
        ge=1, le=MAX_TURN_SECONDS, strict=True, description="Maximum turn duration in seconds."
    )
    access: Literal["claude-code-subscription"] = Field(
        default="claude-code-subscription", description="Selected subscription access mode."
    )
    access_fingerprint: str = Field(
        pattern=r"^[a-f0-9]{64}$", description="Opaque selected-access continuity fingerprint."
    )
    first_run: Literal["current-slot-after-approval"] = Field(
        default="current-slot-after-approval", description="Current interval becomes eligible only after approval."
    )
    status: Literal["proposed"] = Field(default="proposed", description="Proposal status; approval is separate.")
    notification_policy: ScheduledNotificationPolicy | None = Field(
        default=None,
        description="Separately reviewed bounded local notification scope, or null for no scheduled notifications.",
    )
    connected_grant: ConnectedGrant | None = Field(
        default=None, description="Exact separately approved connected read, or null for no external read."
    )

    @field_validator("cadence_seconds")
    @classmethod
    def whole_minutes(cls, value: int) -> int:
        """The daemon checks every minute; durations must be whole minutes.

        Raises:
            ValueError: If the cadence is not a whole minute.
        """
        if value % 60:
            msg = "Schedule cadence must be a whole number of minutes"
            raise ValueError(msg)
        return value


def _binding_intent(binding: ScheduleBinding) -> dict[str, JsonValue]:
    # Older schedules predate connected_grant; preserve their exact digest without omitting nested nulls.
    value = binding.model_dump(mode="json")
    if value["notification_policy"] is None:
        del value["notification_policy"]
    if value["connected_grant"] is None:
        del value["connected_grant"]
    return value


def _same_binding(left: ScheduleBinding, right: ScheduleBinding) -> bool:
    return arguments_hash(_binding_intent(left)) == arguments_hash(_binding_intent(right))


class ScheduleReviewView(BaseModel):
    """Owner-private schedule proposal or its recorded decision and current eligibility."""

    binding: ScheduleBinding = Field(description="Exact reviewed schedule binding.")
    status: Literal["proposed", "approved", "paused", "stale"] = Field(
        description="Recorded decision and current eligibility."
    )


class ScheduleReviewPage(BaseModel):
    """Bounded owner-private schedule references, including paused and stale decisions."""

    session_id: SessionId = Field(description="Owner-bound personal session.")
    items: list[ScheduleReviewView] = Field(max_length=100, description="Bounded owner-private schedule reviews.")
    next_cursor: int | None = Field(default=None, ge=0, description="Cursor for the next page, if present.")


def _fingerprint(chat: UserChatService) -> str:
    """Observe selected CLI/settings, not provider account identity or publisher authenticity.

    Raises:
        HeinzelError: If selected access cannot be observed.
    """
    limits, runner = chat.selected()
    path = chat.which(runner.claude_executable)
    if path is None:
        msg = "The selected Claude Code executable is unavailable"
        raise HeinzelError(msg)
    executable, digest = chat.executable(path)
    terms = {
        "executable": str(executable),
        "digest": digest,
        "budget_mode": limits.subscription_budget_mode,
        "model": runner.model,
        "runner_revision": runner.revision,
    }
    return hashlib.sha256(json.dumps(terms, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class AutonomousSchedules:
    """Private draft and trusted confirmation seam; it does not install, activate, or run a daemon."""

    def __init__(self, crud: SessionCrud, chat: UserChatService) -> None:
        self.crud, self.chat = crud, chat

    def _require_owner_write(self) -> None:
        context = self.chat.current.context()
        if (
            context.authentication not in {"cli", "api-bearer", "web-session", "web-device"}
            or context.principal != self.chat.current.owner()
            or context.principal.id != self.crud.repository.principal_id
            or PRIVATE_WRITE not in context.permissions
        ):
            msg = "This schedule action requires the authenticated owner"
            raise HeinzelError(msg)

    def current(self, session: str, request: str) -> ScheduleBinding:
        events = self.crud.repository.control_read(session).events
        found = [e for e in events if e.kind == "agent.schedule-proposed" and e.key == request]
        if len(found) != 1:
            msg = "The schedule proposal is unavailable"
            raise HeinzelError(msg)
        binding = ScheduleBinding.model_validate(self.crud.read_body(session, found[0]))
        latest = self.crud.latest_scheduled_agent_proposal(session)
        if (
            binding.session_id != session
            or binding.owner != self.crud.repository.principal_id
            or latest is None
            or latest.request_id != binding.contract_request_id
            or latest.revision != binding.contract_revision
            or latest.preview_sha256 != binding.contract_sha256
            or not ContractReview(self.crud).applied(session, binding.contract_request_id)
            or binding.access_fingerprint != _fingerprint(self.chat)
            or latest.max_usd is not None
            or binding.timeout_seconds > latest.timeout_seconds
        ):
            msg = "The approved contract or selected access changed; review a new schedule"
            raise HeinzelError(msg)
        if binding.connected_grant is not None:
            self.chat.connected_grants().verify_approved(session, binding.connected_grant)
        return binding

    def propose(  # noqa: PLR0913, PLR0917 - explicit complete reviewed schedule terms
        self,
        session: str,
        request: str,
        contract_request: str,
        cadence_seconds: int,
        max_requests: int,
        timeout_seconds: int,
        connected_request_id: str | None = None,
        notification_policy: ScheduledNotificationPolicy | None = None,
    ) -> ScheduleBinding:
        """Persist an inert proposal; no user decision or wake is created.

        Raises:
            HeinzelError: If contract, selected access, terms, or request identity cannot be verified.
        """
        self._require_owner_write()
        proposal = ContractReview(self.crud).current(session, contract_request)
        if not ContractReview(self.crud).applied(session, contract_request) or proposal.max_usd is not None:
            msg = "Accept the exact subscription contract before proposing a schedule"
            raise HeinzelError(msg)
        connected_grant = None
        if connected_request_id is not None:
            grants = self.chat.connected_grants()
            connected_grant = grants.approved(session)
            if connected_grant.request_id != connected_request_id:
                msg = "The connected read grant changed"
                raise HeinzelError(msg)
            grants.verify_approved(session, connected_grant)
        binding = ScheduleBinding(
            session_id=session,
            request_id=request,
            owner=self.crud.repository.principal_id,
            contract_request_id=contract_request,
            contract_revision=proposal.revision,
            contract_sha256=proposal.preview_sha256,
            cadence_seconds=cadence_seconds,
            max_requests=max_requests,
            timeout_seconds=timeout_seconds,
            access_fingerprint=_fingerprint(self.chat),
            connected_grant=connected_grant,
            notification_policy=notification_policy,
        )
        if timeout_seconds > proposal.timeout_seconds:
            msg = "Schedule timeout exceeds the reviewed contract"
            raise HeinzelError(msg)
        with self.crud.repository.control_writer(session) as writer:
            events = self.crud.repository.control_read(session).events
            matches = [e for e in events if e.kind == "agent.schedule-proposed" and e.key == request]
            if matches:
                if len(matches) != 1 or not _same_binding(
                    ScheduleBinding.model_validate(self.crud.read_body(session, matches[0])), binding
                ):
                    msg = "This schedule request already identifies different terms"
                    raise HeinzelError(msg)
                return binding
            body = immutable_body(writer, "autonomous-schedule", binding.model_dump(mode="json"), control=True)
            writer.append("agent.schedule-proposed", key=request, data=body.model_dump(mode="json"))
        return binding

    def _intent(self, binding: ScheduleBinding) -> ApprovalIntent:
        return ApprovalIntent(
            owner=binding.owner,
            audience="personal-agent-schedule",
            session=binding.session_id,
            action="approve-agent-schedule",
            resource=binding.request_id,
            arguments_hash=arguments_hash(_binding_intent(binding)),
            contract_revision=binding.contract_revision,
            grant_revision=0,
            policy_revision=0,
        )

    def issue(self, session: str, request: str, provenance: str) -> str:
        """Internal issuer for a trusted confirmation boundary, never an exposed operation.

        Raises:
            HeinzelError: If the schedule is stale or already approved.
        """
        self._require_owner_write()
        proof = secrets.token_urlsafe(32)
        with self.crud.repository.control_writer(session) as writer:
            binding = self.current(session, request)
            if self._has_active(session, self.crud.repository.control_read(session).events):
                msg = "This schedule has already been approved"
                raise HeinzelError(msg)
            now = self.crud.clock()
            record = ApprovalRecord(
                intent=self._intent(binding),
                proof_hash=approval_proof_hash(proof),
                nonce=secrets.token_hex(32),
                provenance=provenance[:128],
                decision="approve",
                issued_at=now,
                expires_at=now + timedelta(minutes=5),
            )
            writer.append("approval.issued", key=record.proof_hash, data=record.model_dump(mode="json"))
        return proof

    def _paused(self, session: str, request: str, events: list[SessionEvent]) -> bool:
        pauses = [e for e in events if e.kind == "agent.schedule-paused" and e.key == request]
        if not pauses:
            return False
        approved = [
            e
            for e in events
            if e.kind == "agent.schedule-approved"
            and ScheduleBinding.model_validate(self.crud.read_body(session, e)).request_id == request
        ]
        if (
            len(pauses) != 1
            or len(approved) != 1
            or pauses[0].seq <= approved[0].seq
            or pauses[0].data.get("request_id") != request
            or pauses[0].data.get("owner") != self.crud.repository.principal_id
            or pauses[0].data.get("origin") not in {"cli", "api-bearer", "web-session", "web-device"}
        ):
            msg = "The schedule pause record is invalid"
            raise HeinzelError(msg)
        return True

    def _has_active(self, session: str, events: list[SessionEvent]) -> bool:
        for event in events:
            if event.kind == "agent.schedule-approved":
                binding = ScheduleBinding.model_validate(self.crud.read_body(session, event))
                if not self._paused(session, binding.request_id, events):
                    return True
        return False

    def approve(
        self, session: str, request: str, review: dict[str, JsonValue], proof: str, confirmation: ApprovalConfirmation
    ) -> ScheduleBinding:
        """Record a separately confirmed schedule binding; no autonomous dispatcher is installed.

        Raises:
            HeinzelError: If confirmation, current binding, or exact intent is invalid.
        """
        self._require_owner_write()
        args: dict[str, JsonValue] = {
            "session_id": session,
            "request_id": request,
            "review": review,
            "approval_proof": proof,
        }
        if not confirmation.matches("heinzel", "approve-agent-schedule", args):
            msg = "Approval is invalid or unavailable"
            raise HeinzelError(msg)
        binding = self.current(session, request)
        if arguments_hash(review) != arguments_hash(binding.model_dump(mode="json")):
            msg = "The reviewed schedule changed"
            raise HeinzelError(msg)

        def current(events: list[SessionEvent]) -> ApprovalIntent:
            if self._has_active(session, events):
                msg = "This schedule has already been approved"
                raise HeinzelError(msg)
            return self._intent(self.current(session, request))

        def effect(
            writer: SessionWriter, _events: list[SessionEvent], _evidence: ApprovalEvidence
        ) -> dict[str, JsonValue]:
            return immutable_body(
                writer, "approved-agent-schedule", binding.model_dump(mode="json"), control=True
            ).model_dump(mode="json")

        with self.crud.repository.main_writer(session):
            consume_approval(
                self.crud.repository,
                session,
                proof,
                self._intent(binding),
                current,
                self.crud.clock,
                event_kind="agent.schedule-approved",
                effect_data=effect,
            )
        return binding

    def _saved(self, session: str, request: str, events: list[SessionEvent]) -> ScheduleBinding:
        proposals = [e for e in events if e.kind == "agent.schedule-proposed" and e.key == request]
        if len(proposals) != 1:
            msg = "The schedule proposal is unavailable"
            raise HeinzelError(msg)
        binding = ScheduleBinding.model_validate(self.crud.read_body(session, proposals[0]))
        if binding.session_id != session or binding.owner != self.crud.repository.principal_id:
            msg = "The schedule owner or session is invalid"
            raise HeinzelError(msg)
        return binding

    def approved(
        self, session: str, request: str, *, validate_current: bool = True, allow_paused: bool = False
    ) -> ScheduleBinding:
        events = self.crud.repository.control_read(session).events
        binding = self._saved(session, request, events)
        if validate_current and not _same_binding(self.current(session, request), binding):
            msg = "The schedule binding changed"
            raise HeinzelError(msg)
        matches: list[SessionEvent] = []
        for event in events:
            if event.kind != "agent.schedule-approved":
                continue
            saved = ScheduleBinding.model_validate(self.crud.read_body(session, event))
            if _same_binding(saved, binding):
                matches.append(event)
        if len(matches) != 1:
            msg = "This exact schedule has not been separately approved"
            raise HeinzelError(msg)
        event = matches[0]
        issued = [
            e
            for e in events
            if e.kind == "approval.issued" and e.key == event.data.get("proof_hash") and e.seq < event.seq
        ]
        if len(issued) != 1:
            msg = "This schedule approval has invalid evidence"
            raise HeinzelError(msg)
        try:
            record = ApprovalRecord.model_validate_json(json.dumps(issued[0].data))
        except ValueError:
            msg = "This schedule approval has invalid evidence"
            raise HeinzelError(msg) from None
        if (
            record.model_dump(mode="json").get("decision") != "approve"
            or record.intent != self._intent(binding)
            or record.proof_hash != issued[0].key
            or record.nonce != event.data.get("consumed_nonce")
            or record.intent.model_dump(mode="json") != event.data.get("intent")
            or record.provenance != event.data.get("provenance")
            or not record.issued_at <= event.ts < record.expires_at
            or issued[0].ts > event.ts
            or sum(e.data.get("consumed_nonce") == record.nonce for e in events) != 1
            or any(e.kind == "approval.revoked" and e.key == record.nonce for e in events)
            or (not allow_paused and self._paused(session, request, events))
        ):
            msg = "This schedule is paused or its approval is invalid"
            raise HeinzelError(msg)
        return binding

    def applied(self, session: str, request: str) -> bool:
        """Reconcile the exact recorded approval, including a subsequently paused schedule."""
        events = self.crud.repository.control_read(session).events
        binding = self._saved(session, request, events)
        if not any(
            e.kind == "agent.schedule-approved"
            and _same_binding(ScheduleBinding.model_validate(self.crud.read_body(session, e)), binding)
            for e in events
        ):
            return False
        self.approved(session, request, validate_current=False, allow_paused=True)
        return True

    def review(self, session: str, request: str) -> ScheduleReviewView:
        """Read exact owner-private terms and eligibility; never issue a decision."""
        events = self.crud.repository.control_read(session).events
        binding = self._saved(session, request, events)
        status: Literal["proposed", "approved", "paused", "stale"] = "proposed"
        if self.applied(session, request):
            if self._paused(session, request, events):
                status = "paused"
            else:
                try:
                    self.current(session, request)
                except HeinzelError:
                    status = "stale"
                else:
                    status = "approved"
        else:
            try:
                self.current(session, request)
            except HeinzelError:
                status = "stale"
        return ScheduleReviewView(binding=binding, status=status)

    def list_reviews(self, session: str, cursor: int = 0, limit: int = 20) -> ScheduleReviewPage:
        """List owner-private schedule records after an exclusive journal position.

        Raises:
            HeinzelError: If the requested bounds or journal references are invalid.
        """
        if type(cursor) is not int or cursor < 0 or type(limit) is not int or not 1 <= limit <= _MAX_PAGE:
            msg = "The schedule page bounds are invalid"
            raise HeinzelError(msg)
        events = self.crud.repository.control_read(session).events
        proposals = [e for e in events if e.kind == "agent.schedule-proposed" and e.seq > cursor]
        chosen = proposals[:limit]
        items: list[ScheduleReviewView] = []
        for event in chosen:
            if event.key is None:
                msg = "The schedule proposal has an invalid identity"
                raise HeinzelError(msg)
            items.append(self.review(session, event.key))
        return ScheduleReviewPage(
            session_id=session, items=items, next_cursor=chosen[-1].seq if len(proposals) > limit else None
        )

    def pause(self, session: str, request: str) -> None:
        """Stop future launches; resumption requires a new exact schedule approval.

        Raises:
            HeinzelError: If the caller is not the owner through a human-facing head.
        """
        self._require_owner_write()
        with self.crud.repository.main_writer(session):
            self.approved(session, request, validate_current=False, allow_paused=True)
            events = self.crud.repository.control_read(session).events
            if self._paused(session, request, events):
                return
            with self.crud.repository.control_writer(session) as writer:
                writer.append(
                    "agent.schedule-paused",
                    key=request,
                    data={
                        "request_id": request,
                        "owner": self.crud.repository.principal_id,
                        "origin": self.chat.current.context().authentication,
                    },
                )

    def _ensure_no_prior_pending(self, session: str, occurrence: str) -> None:
        events = self.crud.repository.control_read(session).events
        for event in events:
            if event.kind != "message.accepted" or event.data.get("message_id") == occurrence:
                continue
            previous = event.data.get("message_id")
            if not isinstance(previous, str):
                msg = "An earlier message has an invalid identity"
                raise HeinzelError(msg)
            turn = interactive_turn_id(previous)
            if not any(e.key == turn and e.kind in {"message.replied", "interactive.abandoned"} for e in events):
                msg = "An earlier message must finish or be resolved before a scheduled turn"
                raise HeinzelError(msg)

    async def tick(self, session: str, request: str, when: datetime) -> str:
        """Run one due generic turn under the trusted local daemon identity, never a model-supplied identity.

        Raises:
            HeinzelError: If schedule, owner, access, or occurrence authority is unavailable.
        """
        context = self.chat.current.context()
        if context.authentication != "daemon" or context.principal != self.chat.current.owner():
            msg = "Only the owner-bound daemon may dispatch this schedule"
            raise HeinzelError(msg)
        if when.tzinfo is None or when.utcoffset() is None or when > datetime.now(UTC) + timedelta(seconds=1):
            msg = "The schedule occurrence time is invalid"
            raise HeinzelError(msg)
        binding = self.approved(session, request)
        slot = int(when.timestamp()) // binding.cadence_seconds
        if slot != int(datetime.now(UTC).timestamp()) // binding.cadence_seconds:
            msg = "Only the current schedule occurrence can run"
            raise HeinzelError(msg)
        occurrence = "scheduled-" + hashlib.sha256(f"{session}\0{request}\0{slot}".encode()).hexdigest()
        contract = ContractReview(self.crud).current(session, binding.contract_request_id)

        def verify() -> None:
            current = self.chat.current.context()
            if current.authentication != "daemon" or current.principal.id != binding.owner:
                msg = "The scheduled owner identity changed"
                raise HeinzelError(msg)
            if int(datetime.now(UTC).timestamp()) // binding.cadence_seconds != slot:
                msg = "The schedule occurrence expired before launch"
                raise HeinzelError(msg)
            if not _same_binding(self.approved(session, request), binding):
                msg = "The approved schedule changed"
                raise HeinzelError(msg)

        def prepare() -> None:
            verify()
            self._ensure_no_prior_pending(session, occurrence)
            self.crud.accept(session, MessageRequest(request_id=occurrence, text=contract.contract))
            events = self.crud.repository.control_read(session).events
            accepted = [e for e in events if e.kind == "message.accepted" and e.key == f"message-{occurrence}"]
            if len(accepted) != 1 or accepted[0].data.get("origin") != "daemon":
                msg = "The scheduled occurrence has no trusted daemon origin"
                raise HeinzelError(msg)

        answer = await self.chat.dispatch(
            session,
            occurrence,
            verify,
            binding.max_requests,
            binding.timeout_seconds,
            user_proposals=False,
            prepare=prepare,
            connected_allowed=binding.connected_grant is not None,
            notification_policy=binding.notification_policy,
            notification_contract=binding.contract_sha256,
            notification_schedule=binding.request_id,
            notification_binding=arguments_hash(_binding_intent(binding)),
        )
        return answer.message.text
