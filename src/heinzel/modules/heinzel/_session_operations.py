"""Private durable-session operations: one declared surface for CLI, stdio MCP, paired web and private API."""

import asyncio
import getpass
import sys
from collections.abc import Generator
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, JsonValue

from heinzel import Config, ConfiguredApiAccess, CurrentUser, HeinzelError, Module, Notifications
from heinzel.kernel import (
    CheckInChatContext,
    CheckInChatContextSource,
    CheckInHistoryMessage,
    DaemonAttestation,
    DaemonInstallationLifecycle,
    Kernel,
    RunnerSnapshotProvider,
)
from heinzel.kernel._approval import ApprovalConfirmation, ApprovalIssuer, arguments_hash
from heinzel.modules.heinzel._autonomous_schedule import (
    AutonomousSchedules,
    ScheduleBinding,
    ScheduleReviewPage,
    ScheduleReviewView,
)
from heinzel.modules.heinzel._connected_grants import (
    ConnectedGrant,
    ConnectedGrantProposal,
    ConnectedReadGrants,
    ConnectedReadOption,
    ConnectedReadState,
    RollingConnectedGrantProposal,
    RollingWindowPolicy,
)
from heinzel.modules.heinzel._connected_read_set_approvals import ConnectedReadSetApprovals
from heinzel.modules.heinzel._connected_read_set_reviews import ConnectedReadSetReviews, ReadSetReview
from heinzel.modules.heinzel._connected_read_sets import ConnectedReadSets, ReadSetProposal, ReadSetSelection
from heinzel.modules.heinzel._contract_review import ContractReview, ContractReviewView
from heinzel.modules.heinzel._input_receipts import InputSnapshotPage, snapshot_history
from heinzel.modules.heinzel._model_grants import ModelGrants
from heinzel.modules.heinzel._model_policy_operations import ModelPolicyService, register_model_policy_operations
from heinzel.modules.heinzel._models import HeinzelSettings
from heinzel.modules.heinzel._notification_delivery import NotificationDelivery
from heinzel.modules.heinzel._notification_proposals import (
    NotificationProposal,
    NotificationProposalPage,
    NotificationProposals,
)
from heinzel.modules.heinzel._quiet_hours import QuietHours
from heinzel.modules.heinzel._schedule_suggestions import ScheduleSuggestion, latest_schedule_suggestion
from heinzel.modules.heinzel._scheduled_notifications import (
    ScheduledNotificationPage,
    ScheduledNotificationPolicy,
    ScheduledNotificationRecovery,
    ScheduledNotificationView,
)
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import (
    HeinzelAppearance,
    MessageAccepted,
    MessageRequest,
    PersonalArtifact,
    PersonalArtifacts,
    ProfileProposal,
    RequestId,
    ScheduledAgentProposal,
    SessionCosts,
    SessionId,
    SessionList,
    SessionSnapshot,
    SkillDraft,
    SkillDraftPage,
    SkillSharingPage,
    SkillSharingProposal,
)
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository
from heinzel.modules.heinzel._source_set_suggestions import SourceSetSuggestion, latest_source_set_suggestion
from heinzel.modules.heinzel._source_suggestions import SourceSuggestion, latest_source_suggestion
from heinzel.modules.heinzel._user_chat import (
    UserChatAnswer,
    UserChatService,
    UserToolActivity,
    UserTurnResolution,
    UserTurnState,
)

Request = Annotated[RequestId, Field(description="A stable client request id; reuse it only for an exact retry.")]
Identity = Annotated[SessionId, Field(description="The stable personal session id returned by new-chat or sessions.")]
Appearance = Annotated[
    HeinzelAppearance | None,
    Field(description="The complete personal appearance overrides; null leaves the current look unchanged."),
]


def _crud(current: CurrentUser) -> SessionCrud:
    def costs() -> SessionCosts:
        policy = Config.load(Path.home()).settings(HeinzelSettings, "heinzel")
        return SessionCosts(
            session_max_usd=policy.session_max_usd,
            message_max_usd=min(policy.session_max_usd, policy.message_max_usd),
        )

    return SessionCrud(PersonalSessionRepository(current), costs)


class _SessionApprovalIssuer(ApprovalIssuer):
    """Require a current version observed in a daemon tick before issuing exact schedule approval."""

    def __init__(self, service: SessionCrud, daemon: DaemonAttestation | None, kernel: Kernel | None = None) -> None:
        self.service = service
        self.daemon = daemon
        self.kernel = kernel

    def check_daemon(self, module: str, operation: str, arguments: dict[str, JsonValue]) -> None:
        if (
            module == "heinzel"
            and operation == "schedule-control"
            and arguments.get("action") in {"activate", "resume"}
        ):
            if self.daemon is None:
                msg = "Check-ins are blocked because the daemon attestation service is unavailable"
                raise HeinzelError(msg)
            status = self.daemon.status()
            if status.installed and (
                status.version is None or status.installed_version is None or status.version_mismatch
            ):
                observed = status.version or "unknown"
                installed = status.installed_version or "unknown"
                msg = (
                    f"Check-ins are blocked because the daemon unit version is {installed} and the last daemon "
                    f"service tick version is {observed}; run `hz system daemon restart`, wait for a service-managed "
                    "tick from the current Heinzel, then review this action again"
                )
                raise HeinzelError(msg)

    @staticmethod
    def _model_args(arguments: dict[str, JsonValue]) -> tuple[str, str]:
        session, request = arguments.get("session_id"), arguments.get("request_id")
        if (
            set(arguments) not in ({"session_id", "request_id"}, {"session_id", "request_id", "review"})
            or not isinstance(session, str)
            or not isinstance(request, str)
        ):
            msg = "Approval is invalid or unavailable"
            raise HeinzelError(msg)
        return session, request

    def _set_approvals(self) -> ConnectedReadSetApprovals:
        if self.kernel is None:
            msg = "Connected read-set review is unavailable"
            raise HeinzelError(msg)
        current = self.kernel.resolve(CurrentUser)
        sets = ConnectedReadSets(self.service, current)
        grants = ConnectedReadGrants(self.service, current, self.kernel)
        return ConnectedReadSetApprovals(ConnectedReadSetReviews(sets, grants))

    def review(self, module: str, operation: str, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if module == "heinzel" and operation == "queue-agent-notification":
            if self.kernel is None:
                msg = "Notification review is unavailable"
                raise HeinzelError(msg)
            session, request = self._model_args(arguments)
            current = self.kernel.resolve(CurrentUser)
            return NotificationProposals(self.service).get(session, request, current).model_dump(mode="json")
        if module == "heinzel" and operation in {"approve-connected-read", "approve-connected-read-set"}:
            return self._review_connected_action(operation, arguments)
        if module == "heinzel" and operation == "approve-agent-schedule":
            if self.kernel is None:
                msg = "Schedule review is unavailable"
                raise HeinzelError(msg)
            return (
                self.kernel.resolve(AutonomousSchedules).current(*self._model_args(arguments)).model_dump(mode="json")
            )
        if module == "heinzel" and operation == "accept-agent-contract":
            return ContractReview(self.service).current(*self._model_args(arguments)).model_dump(mode="json")
        if module == "heinzel" and operation == "approve-model-grant":
            if self.kernel is None:
                msg = "Configured model policy review is unavailable"
                raise HeinzelError(msg)
            return (
                ModelPolicyService(self.service, self.kernel.resolve_builtin_service(ConfiguredApiAccess, "ai.access"))
                .review(*self._model_args(arguments))
                .proposal
            )
        self.check_daemon(module, operation, arguments)
        return self.service.review(module, operation, arguments)

    def _review_connected_action(self, operation: str, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]:
        session, request = self._model_args(arguments)
        if operation == "approve-connected-read-set":
            return self._set_approvals().reviews.current(session, request).model_dump(mode="json")
        if self.kernel is None:
            msg = "Connected read review is unavailable"
            raise HeinzelError(msg)
        return (
            ConnectedReadGrants(self.service, self.kernel.resolve(CurrentUser), self.kernel)
            .review(session, request)
            .model_dump(mode="json")
        )

    def _issue_set(self, arguments: dict[str, JsonValue], provenance: str) -> str:
        session, request = self._model_args(arguments)
        review = arguments.get("review")
        if not isinstance(review, dict) or arguments_hash(review) != arguments_hash(
            self.review("heinzel", "approve-connected-read-set", arguments)
        ):
            msg = "The reviewed read set changed"
            raise HeinzelError(msg)
        exact = ReadSetReview.model_validate(review)
        return self._set_approvals().issue(session, request, exact.revision, provenance)

    def _issue_connected(self, arguments: dict[str, JsonValue], provenance: str) -> str:
        if self.kernel is None:
            msg = "Connected read review is unavailable"
            raise HeinzelError(msg)
        session, request = self._model_args(arguments)
        review = arguments.get("review")
        if not isinstance(review, dict) or arguments_hash(review) != arguments_hash(
            self.review("heinzel", "approve-connected-read", arguments)
        ):
            msg = "The reviewed connected read changed"
            raise HeinzelError(msg)
        return ConnectedReadGrants(self.service, self.kernel.resolve(CurrentUser), self.kernel).issue(
            session, request, provenance
        )

    def _issue_notification(self, arguments: dict[str, JsonValue], provenance: str) -> str:
        if self.kernel is None:
            msg = "Notification review is unavailable"
            raise HeinzelError(msg)
        session, request = self._model_args(arguments)
        if arguments_hash(arguments.get("review")) != arguments_hash(
            self.review("heinzel", "queue-agent-notification", arguments)
        ):
            msg = "The reviewed notification changed"
            raise HeinzelError(msg)
        return self.kernel.resolve(NotificationDelivery).issue(session, request, provenance)

    def _applied_notification(self, arguments: dict[str, JsonValue]) -> bool:
        if self.kernel is None:
            return False
        session, request = self._model_args(arguments)
        if arguments_hash(arguments.get("review")) != arguments_hash(
            self.review("heinzel", "queue-agent-notification", arguments)
        ):
            return False
        return self.kernel.resolve(NotificationDelivery).recover(session, request, write_receipt=False) == "recorded"

    def issue(self, module: str, operation: str, arguments: dict[str, JsonValue], provenance: str) -> str:
        if module == "heinzel" and operation == "queue-agent-notification":
            return self._issue_notification(arguments, provenance)
        if module == "heinzel" and operation in {"approve-connected-read", "approve-connected-read-set"}:
            return (
                self._issue_set(arguments, provenance)
                if operation.endswith("-set")
                else self._issue_connected(arguments, provenance)
            )
        if module == "heinzel" and operation == "approve-agent-schedule":
            if self.kernel is None:
                msg = "Schedule review is unavailable"
                raise HeinzelError(msg)
            session, request = self._model_args(arguments)
            if arguments_hash(arguments.get("review")) != arguments_hash(self.review(module, operation, arguments)):
                msg = "The reviewed schedule changed"
                raise HeinzelError(msg)
            return self.kernel.resolve(AutonomousSchedules).issue(session, request, provenance)
        if module == "heinzel" and operation == "accept-agent-contract":
            session, request = self._model_args(arguments)
            if arguments.get("review") != self.review(module, operation, arguments):
                msg = "The reviewed contract changed"
                raise HeinzelError(msg)
            return ContractReview(self.service).issue(session, request, provenance)
        if module == "heinzel" and operation == "approve-model-grant":
            session, request = self._model_args(arguments)
            if arguments.get("review") != self.review(module, operation, arguments):
                msg = "The reviewed model policy changed"
                raise HeinzelError(msg)
            return ModelGrants(self.service).issue(session, request, provenance)
        guarded = (
            module == "heinzel"
            and operation == "schedule-control"
            and arguments.get("action") in {"activate", "resume"}
        )
        with _schedule_admission(self.service, self.kernel) if guarded else nullcontext():
            self.check_daemon(module, operation, arguments)
            return self.service.issue(module, operation, arguments, provenance)

    def _applied_connected(self, arguments: dict[str, JsonValue]) -> bool:
        if self.kernel is None:
            return False
        session, request = self._model_args(arguments)
        expected = arguments.get("review")
        if not isinstance(expected, dict):
            return False
        grant = ConnectedReadGrants(self.service, self.kernel.resolve(CurrentUser), self.kernel).approved(session)
        return grant.request_id == request and arguments_hash(grant.model_dump(mode="json")) == arguments_hash(expected)

    def _applied_set(self, arguments: dict[str, JsonValue]) -> bool:
        session, request = self._model_args(arguments)
        expected = arguments.get("review")
        if not isinstance(expected, dict):
            return False
        approved = self._set_approvals().approved(session)
        return approved.request_id == request and arguments_hash(approved.model_dump(mode="json")) == arguments_hash(
            expected
        )

    def applied(self, module: str, operation: str, arguments: dict[str, JsonValue]) -> bool:
        if module == "heinzel" and operation == "queue-agent-notification":
            return self._applied_notification(arguments)
        if module == "heinzel" and operation in {"approve-connected-read", "approve-connected-read-set"}:
            return self._applied_set(arguments) if operation.endswith("-set") else self._applied_connected(arguments)
        if module == "heinzel" and operation == "approve-agent-schedule":
            if self.kernel is None:
                return False
            return self.kernel.resolve(AutonomousSchedules).applied(*self._model_args(arguments))
        if module == "heinzel" and operation in {"accept-agent-contract", "approve-model-grant"}:
            service = (
                ContractReview(self.service) if operation == "accept-agent-contract" else ModelGrants(self.service)
            )
            return service.applied(*self._model_args(arguments))
        return self.service.applied(module, operation, arguments)


@contextmanager
def _schedule_admission(service: SessionCrud, kernel: Kernel | None) -> Generator[None, None, None]:
    """Hold lifecycle before session writer until the caller's schedule write is complete.

    Yields:
        Control while the current daemon state remains protected from cooperating lifecycle writers.

    Raises:
        HeinzelError: If the lifecycle service or current daemon evidence is unavailable.
    """
    if kernel is None or not kernel.provides(DaemonInstallationLifecycle):
        msg = "Check-ins are blocked because daemon lifecycle admission is unavailable"
        raise HeinzelError(msg)
    daemon = kernel.resolve(DaemonAttestation) if kernel.provides(DaemonAttestation) else None
    with kernel.resolve(DaemonInstallationLifecycle).admission():
        _SessionApprovalIssuer(service, daemon, kernel).check_daemon(
            "heinzel", "schedule-control", {"action": "activate"}
        )
        yield


def _approval_issuer(service: SessionCrud, kernel: Kernel) -> ApprovalIssuer:
    """Expose the internal issuer only as a kernel service, never as a callable operation."""
    daemon = kernel.resolve(DaemonAttestation) if kernel.provides(DaemonAttestation) else None
    return _SessionApprovalIssuer(service, daemon, kernel)


def _approval_confirmation() -> ApprovalConfirmation:
    """Use a context-local proof marker for the one internal CLI/web confirmation call."""
    return ApprovalConfirmation()


class _CheckInContextSource(CheckInChatContextSource):
    """Project only the bounded fields runner chat needs from the current owner's durable session."""

    def __init__(self, service: SessionCrud) -> None:
        self.service = service

    def get(self, session_id: str) -> CheckInChatContext:
        snapshot = self.service.snapshot(session_id, limit=200)
        history = self.service.recent_history(session_id)
        schedule = snapshot.schedule
        return CheckInChatContext(
            name=snapshot.name,
            state=snapshot.state,
            schedule_status=schedule.status if schedule is not None else None,
            cron=schedule.cron if schedule is not None else None,
            timezone=schedule.timezone if schedule is not None else None,
            history=tuple(CheckInHistoryMessage(role=item.role, text=item.text) for item in history),
        )


def _check_in_context_source(service: SessionCrud) -> CheckInChatContextSource:
    return _CheckInContextSource(service)


def _register_skill_operations(module: Module) -> None:
    """Declare private draft and sharing-review operations; no sharing authority is issued."""

    @module.operation(private=True, read_only=False, heads={"web", "api-private", "mcp-stdio"}, audience="everyday")
    def propose_skill_draft(  # noqa: PLR0913, PLR0917 - complete private draft and intended scope
        service: SessionCrud,
        session_id: Identity,
        request_id: Request,
        name: Annotated[str, Field(description="Proposed skill name.", min_length=1, max_length=64)],
        requested_scope: Annotated[
            Literal["personal", "team", "org", "site", "company"],
            Field(description="Requested scope; no grant is created."),
        ],
        files: Annotated[
            dict[str, str], Field(description="Exact SKILL.md, supporting text and optional Python script drafts.")
        ],
        scope_ref: Annotated[
            str | None,
            Field(
                description="Opaque target for a requested shared scope; not verified.",
                max_length=128,
                pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$",
            ),
        ] = None,
    ) -> SkillDraft:
        """Store a private skill draft; no file is published, executed or shared."""
        return service.propose_skill_draft(session_id, request_id, name, requested_scope, scope_ref, files)

    @module.operation(private=True, audience="everyday")
    def skill_draft(service: SessionCrud, session_id: Identity, request_id: Request) -> SkillDraft:
        """Read exact private skill draft files and requested sharing scope."""
        return service.skill_draft(session_id, request_id)

    @module.operation(private=True, read_only=False, heads={"web", "api-private"}, audience="everyday")
    def propose_skill_sharing(
        service: SessionCrud,
        session_id: Identity,
        request_id: Request,
        draft_request_id: Request,
        draft_sha256: Annotated[
            str, Field(description="The exact immutable draft fingerprint.", pattern=r"^[0-9a-f]{64}$")
        ],
    ) -> SkillSharingProposal:
        """Request review of exact private draft and target; membership and authority are unverified."""
        return service.propose_skill_sharing(session_id, request_id, draft_request_id, draft_sha256)

    @module.operation(private=True, heads={"web", "api-private"}, audience="everyday")
    def skill_sharing(service: SessionCrud, session_id: Identity, request_id: Request) -> SkillSharingProposal:
        """Read a private sharing request; it is neither approved nor published."""
        return service.skill_sharing(session_id, request_id)

    @module.operation(private=True, heads={"web", "api-private"}, audience="everyday")
    def skill_sharing_proposals(
        service: SessionCrud,
        session_id: Identity,
        cursor: Annotated[int, Field(description="Resume after this session journal position.", ge=0)] = 0,
        limit: Annotated[
            int, Field(description="The most private sharing references returned in this page.", ge=1, le=100)
        ] = 20,
    ) -> SkillSharingPage:
        """Discover owner-private pending sharing request references."""
        return service.skill_sharing_proposals(session_id, cursor, limit)

    @module.operation(private=True, audience="everyday")
    def skill_drafts(
        service: SessionCrud,
        session_id: Identity,
        cursor: Annotated[int, Field(description="Resume after this session journal position.", ge=0)] = 0,
        limit: Annotated[
            int, Field(description="The most private draft references returned in this page.", ge=1, le=100)
        ] = 20,
    ) -> SkillDraftPage:
        """List private draft references without file contents."""
        return service.skill_drafts(session_id, cursor, limit)


def _register_contract_review_operations(module: Module) -> None:
    """Register exact inert contract review and trusted confirmation."""

    @module.operation(private=True, heads={"web", "api-private"}, audience="everyday")
    def agent_contract_review(service: SessionCrud, session_id: Identity, request_id: Request) -> ContractReviewView:
        """Read an exact contract and its acceptance; this grants no execution or access."""
        review = ContractReview(service)
        proposal = service.scheduled_agent_proposal(session_id, request_id)
        return ContractReviewView(proposal=proposal, accepted=review.applied(session_id, request_id))

    @module.operation(private=True, read_only=False, heads={"web", "api-private"}, audience="everyday")
    def accept_agent_contract(  # noqa: PLR0913, PLR0917 - exact trusted confirmation
        service: SessionCrud,
        confirmation: ApprovalConfirmation,
        session_id: Identity,
        request_id: Request,
        review: dict[str, JsonValue],
        approval_proof: str,
    ) -> ScheduledAgentProposal:
        """Record only exact contract acceptance through trusted confirmation.

        Raises:
            HeinzelError: If confirmation or exact review is invalid.
        """
        args: dict[str, JsonValue] = {
            "session_id": session_id,
            "request_id": request_id,
            "review": review,
            "approval_proof": approval_proof,
        }
        if not confirmation.matches("heinzel", "accept-agent-contract", args):
            msg = "Approval is invalid or unavailable"
            raise HeinzelError(msg)
        contracts = ContractReview(service)
        if contracts.current(session_id, request_id).model_dump(mode="json") != review:
            msg = "The reviewed contract changed"
            raise HeinzelError(msg)
        return contracts.accept(session_id, request_id, approval_proof)


def _register_connected_read_operations(module: Module) -> None:
    """Register private review surfaces; human confirmation alone can consume an issued proof."""

    @module.operation(private=True, heads={"web", "api-private"}, audience="everyday")
    def connected_read_options(service: ConnectedReadGrants, session_id: Identity) -> list[ConnectedReadOption]:
        """List selected read methods without opening a provider."""
        return service.options(session_id)

    @module.operation(private=True, heads={"web", "api-private"}, audience="everyday")
    def connected_read_state(service: ConnectedReadGrants, session_id: Identity) -> ConnectedReadState:
        """Inspect the latest recorded connected-read state for this session."""
        return service.state(session_id)

    @module.operation(private=True, read_only=False, heads={"web", "api-private"}, audience="everyday")
    def propose_connected_read(  # noqa: PLR0913, PLR0917 - exact review parameters
        service: ConnectedReadGrants,
        session_id: Identity,
        request_id: Request,
        capability: Annotated[str, Field(description="The selected read capability.")],
        method: Annotated[str, Field(description="The exact read method to review.")],
        fixed_arguments: Annotated[dict[str, JsonValue], Field(description="Exact fixed read arguments for review.")],
    ) -> ConnectedGrantProposal:
        """Observe and propose exact connected-read terms; this grants no access."""
        return service.propose(session_id, request_id, capability, method, fixed_arguments)

    @module.operation(private=True, read_only=False, heads={"web", "api-private"}, audience="everyday")
    def prepare_suggested_source(
        service: ConnectedReadGrants,
        session_id: Identity,
        request_id: Request,
        suggestion_request: Annotated[str, Field(description="Exact host-derived suggestion request.")],
        suggestion_revision: Annotated[int, Field(ge=1, description="Exact current suggestion revision.")],
    ) -> ConnectedGrantProposal:
        """Prepare the current exact suggestion for separate human approval without executing the read."""
        return service.propose_suggestion(session_id, request_id, suggestion_request, suggestion_revision)

    @module.operation(private=True, read_only=False, heads={"web", "api-private"}, audience="everyday")
    def propose_rolling_connected_read(  # noqa: PLR0913, PLR0917 - exact explicit reviewed terms
        service: ConnectedReadGrants,
        session_id: Identity,
        request_id: Request,
        capability: Annotated[str, Field(description="Selected read capability.")],
        method: Annotated[str, Field(description="Selected read method.")],
        fixed_arguments: Annotated[
            dict[str, JsonValue], Field(description="Fixed arguments excluding the rolling window.")
        ],
        window_policy: RollingWindowPolicy,
    ) -> RollingConnectedGrantProposal:
        """Propose explicit bounded rolling read terms; this grants no access."""
        return service.propose_rolling(session_id, request_id, capability, method, fixed_arguments, window_policy)

    @module.operation(private=True, heads={"web", "api-private"}, audience="everyday")
    def connected_read_review(
        service: ConnectedReadGrants, session_id: Identity, request_id: Request
    ) -> ConnectedGrant:
        """Inspect current exact connected-read proposal; this does not approve it."""
        return service.review(session_id, request_id)

    @module.operation(private=True, read_only=False, heads={"web", "api-private"}, audience="everyday")
    def revoke_connected_read(service: ConnectedReadGrants, session_id: Identity, request_id: Request) -> bool:
        """Revoke this exact connected-read grant before future tool calls."""
        service.revoke(session_id, request_id)
        return True

    @module.operation(private=True, read_only=False, heads={"web", "api-private"}, audience="everyday")
    def approve_connected_read(  # noqa: PLR0913, PLR0917 - exact human confirmation
        service: ConnectedReadGrants,
        confirmation: ApprovalConfirmation,
        session_id: Identity,
        request_id: Request,
        review: Annotated[
            dict[str, JsonValue], Field(description="The exact reviewed provider, source, account and read terms.")
        ],
        approval_proof: Annotated[str, Field(description="In-process proof from the human confirmation boundary.")],
    ) -> ConnectedGrant:
        """Record only a human-confirmed exact connected-read grant; does not execute it."""
        return service.approve(session_id, request_id, review, approval_proof, confirmation)


def _register_connected_read_set_operations(module: Module) -> None:
    """Register owner-private set preparation and exact human confirmation, not execution."""

    @module.operation(private=True, heads={"web", "api-private"}, audience="everyday")
    def connected_read_set_context(service: ConnectedReadSets, session_id: Identity) -> dict[str, str]:
        """Bind local review recovery state to the authenticated owner and session."""
        service.latest(session_id)
        return {"session_id": session_id, "owner": service.owner}

    @module.operation(private=True, heads={"web", "api-private"}, audience="everyday")
    def latest_source_set_suggestion_view(service: SessionCrud, session_id: Identity) -> SourceSetSuggestion | None:
        """Inspect the latest inert suggested sources; no provider was contacted or approved."""
        return latest_source_set_suggestion(service, session_id)

    @module.operation(private=True, heads={"web", "api-private"}, audience="everyday")
    def connected_read_set_proposal(service: ConnectedReadSets, session_id: Identity) -> ReadSetProposal | None:
        """Inspect the latest private read-set proposal; this is not an approval."""
        return service.latest(session_id)

    @module.operation(private=True, read_only=False, heads={"web", "api-private"}, audience="everyday")
    def propose_connected_read_set(
        service: ConnectedReadSets,
        grants: ConnectedReadGrants,
        session_id: Identity,
        request_id: Request,
        selections: Annotated[tuple[ReadSetSelection, ...], Field(min_length=1, max_length=4)],
    ) -> ReadSetProposal:
        """Observe the selected fixed reads and propose them together without granting access."""
        return service.propose_selected(grants, session_id, request_id, selections)

    @module.operation(private=True, read_only=False, heads={"web", "api-private"}, audience="everyday")
    def prepare_connected_read_set_review(
        service: ConnectedReadSetReviews,
        session_id: Identity,
        request_id: Request,
        proposal_request: Request,
        proposal_revision: Annotated[int, Field(ge=1, strict=True)],
    ) -> ReadSetReview:
        """Re-observe the exact current set; this does not issue or consume approval."""
        return service.prepare(session_id, request_id, proposal_request, proposal_revision)

    @module.operation(private=True, heads={"web", "api-private"}, audience="everyday")
    def connected_read_set_review(
        service: ConnectedReadSetReviews, session_id: Identity, request_id: Request
    ) -> ReadSetReview:
        """Inspect the latest exact set review after fresh source and account checks."""
        return service.current(session_id, request_id)

    @module.operation(private=True, heads={"web", "api-private"}, audience="everyday")
    def connected_read_set_approval(service: ConnectedReadSetApprovals, session_id: Identity) -> ReadSetReview:
        """Inspect the latest valid, unrevoked whole-set approval without executing a read."""
        return service.approved(session_id)

    @module.operation(private=True, read_only=False, heads={"web", "api-private"}, audience="everyday")
    def approve_connected_read_set(  # noqa: PLR0913, PLR0917 - exact human confirmation
        service: ConnectedReadSetApprovals,
        confirmation: ApprovalConfirmation,
        session_id: Identity,
        request_id: Request,
        review: dict[str, JsonValue],
        approval_proof: str,
    ) -> ReadSetReview:
        """Record an exact trusted human-confirmed read-set approval; do not execute a read."""
        return service.approve(session_id, request_id, review, approval_proof, confirmation)

    @module.operation(private=True, read_only=False, heads={"web", "api-private"}, audience="everyday")
    def revoke_connected_read_set(
        service: ConnectedReadSetApprovals, session_id: Identity, request_id: Request
    ) -> bool:
        """Revoke the complete exact set before any later reads."""
        service.revoke(session_id, request_id)
        return True


def _register_autonomous_schedule_operations(module: Module) -> None:
    """Register owner-private schedule review, independent of legacy notices."""

    @module.operation(private=True, read_only=False, heads={"web", "api-private"}, audience="everyday")
    def propose_agent_schedule(  # noqa: PLR0913, PLR0917 - exact reviewed terms
        service: AutonomousSchedules,
        session_id: Identity,
        request_id: Request,
        contract_request_id: Request,
        cadence_seconds: Annotated[int, Field(strict=True, ge=60, le=86400, multiple_of=60)],
        max_requests: Annotated[int, Field(strict=True, ge=1, le=4)],
        timeout_seconds: Annotated[int, Field(strict=True, ge=1, le=120)],
        connected_request_id: Annotated[
            str | None,
            Field(
                pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$",
                description="An already approved connected-read request, or null for no connected reads.",
            ),
        ] = None,
        notification_policy: ScheduledNotificationPolicy | None = None,
        quiet_hours: QuietHours | None = None,
    ) -> ScheduleBinding:
        """Propose autonomous turns; separate human confirmation is required to authorize them."""
        return service.propose(
            session_id,
            request_id,
            contract_request_id,
            cadence_seconds,
            max_requests,
            timeout_seconds,
            connected_request_id,
            notification_policy,
            quiet_hours,
        )

    @module.operation(private=True, heads={"web", "api-private"}, audience="everyday")
    def agent_schedule(service: AutonomousSchedules, session_id: Identity, request_id: Request) -> ScheduleReviewView:
        """Inspect the exact proposed schedule and its recorded state."""
        return service.review(session_id, request_id)

    @module.operation(private=True, heads={"web", "api-private"}, audience="everyday")
    def agent_schedules(
        service: AutonomousSchedules,
        session_id: Identity,
        cursor: Annotated[int, Field(strict=True, ge=0)] = 0,
        limit: Annotated[int, Field(strict=True, ge=1, le=100)] = 20,
    ) -> ScheduleReviewPage:
        """List bounded owner-private schedule proposals and recorded decisions."""
        return service.list_reviews(session_id, cursor, limit)

    @module.operation(private=True, read_only=False, heads={"web", "api-private"}, audience="everyday")
    def pause_agent_schedule(
        service: AutonomousSchedules, session_id: Identity, request_id: Request
    ) -> ScheduleReviewView:
        """Stop future launches; a new schedule needs a separate human approval."""
        service.pause(session_id, request_id)
        return service.review(session_id, request_id)

    @module.operation(private=True, read_only=False, heads={"web", "api-private"}, audience="everyday")
    def approve_agent_schedule(  # noqa: PLR0913, PLR0917 - exact trusted action confirmation
        service: AutonomousSchedules,
        confirmation: ApprovalConfirmation,
        session_id: Identity,
        request_id: Request,
        review: dict[str, JsonValue],
        approval_proof: str,
    ) -> ScheduleBinding:
        """Record exact human-confirmed autonomous schedule authority."""
        return service.approve(session_id, request_id, review, approval_proof, confirmation)


def _register_user_chat_operations(module: Module) -> None:
    """Register private chat, tool activity and profile proposal projections."""

    @module.operation(private=True, heads={"cli", "web", "api-private"}, audience="everyday")
    def latest_profile_proposal(service: SessionCrud, session_id: Identity) -> ProfileProposal | None:
        """Inspect the latest suggested name and look; it has not been applied."""
        return service.latest_profile_proposal(session_id)

    @module.operation(private=True, heads={"web", "api-private"}, audience="everyday")
    def latest_schedule_suggestion_view(service: SessionCrud, session_id: Identity) -> ScheduleSuggestion | None:
        """Inspect the latest inert suggested schedule; it has not been approved or activated."""
        return latest_schedule_suggestion(service, session_id)

    @module.operation(private=True, heads={"web", "api-private"}, audience="everyday")
    def latest_source_suggestion_view(service: SessionCrud, session_id: Identity) -> SourceSuggestion | None:
        """Inspect the latest inert suggested source; no provider was contacted or approved."""
        return latest_source_suggestion(service, session_id)

    @module.operation(private=True, read_only=False, heads={"cli", "web", "api-private"}, audience="everyday")
    def answer_chat(service: UserChatService, session_id: Identity, request_id: Request) -> UserChatAnswer:
        """Answer one authenticated user's accepted message; an interrupted request is never silently replayed."""
        return asyncio.run(service.answer(session_id, request_id))

    @module.operation(private=True, heads={"cli", "web", "api-private"}, audience="everyday")
    def chat_turn_state(service: UserChatService, session_id: Identity, request_id: Request) -> UserTurnState:
        """Read the recorded turn state; unresolved may still be running."""
        return service.turn_state(session_id, request_id)

    @module.operation(private=True, read_only=False, heads={"cli", "web", "api-private"}, audience="everyday")
    def abandon_chat_turn(
        service: UserChatService,
        session_id: Identity,
        request_id: Request,
        acknowledge_uncertain: Annotated[
            bool, Field(strict=True, description="Confirm the outcome may be unknown and no retry or undo occurs.")
        ],
    ) -> UserTurnResolution:
        """Explicitly set aside a stopped uncertain turn; never retry or undo its possible effects.

        Raises:
            HeinzelError: If the uncertain outcome was not acknowledged.
        """
        if not acknowledge_uncertain:
            msg = "Acknowledge the uncertain outcome before setting this turn aside"
            raise HeinzelError(msg)
        return service.abandon(session_id, request_id)

    @module.operation(private=True, heads={"cli", "web", "api-private"}, audience="everyday")
    def input_snapshot_history(
        service: SessionCrud,
        current: CurrentUser,
        session_id: Identity,
        limit: Annotated[int, Field(ge=1, le=100, description="Maximum recent observations to return.")] = 20,
    ) -> InputSnapshotPage:
        """Show recent private exact-result comparisons from approved reads, without source contents."""
        return snapshot_history(service, current, session_id, limit)

    @module.operation(private=True, heads={"cli", "web", "api-private"}, audience="everyday")
    def tool_activity(
        service: UserChatService,
        session_id: Identity,
        limit: Annotated[int, Field(ge=1, le=100, description="Maximum recent recorded tool events to return.")] = 30,
    ) -> UserToolActivity:
        """Show owner-private host-observed tool decisions, without their arguments."""
        return service.activity(session_id, limit)


def _user_chat_service(crud: SessionCrud, current: CurrentUser, kernel: Kernel) -> UserChatService:
    runner = kernel.resolve_builtin_service(RunnerSnapshotProvider, "system.runner")
    return UserChatService(
        crud,
        current,
        runner,
        connected=lambda: ConnectedReadGrants(crud, current, kernel),
        notifications=lambda: kernel.resolve(Notifications),
        playbook_kernel=kernel,
    )


def _register_notification_operations(module: Module) -> None:
    """Owner-only access to drafts; reading does not send or authorize a notification."""
    module.service(NotificationDelivery)
    module.service(ScheduledNotificationRecovery)

    @module.operation(private=True, heads={"web", "api-private"}, audience="everyday")
    def notification_proposal(
        service: SessionCrud, current: CurrentUser, session_id: Identity, request_id: Request
    ) -> NotificationProposal:
        """Read an exact private notification draft without sending it."""
        return NotificationProposals(service).get(session_id, request_id, current)

    @module.operation(private=True, heads={"web", "api-private"}, audience="everyday")
    def notification_proposals(
        service: SessionCrud,
        current: CurrentUser,
        session_id: Identity,
        cursor: Annotated[int, Field(ge=0, description="Exclusive journal cursor.")] = 0,
        limit: Annotated[int, Field(ge=1, le=100, description="Maximum references per page.")] = 20,
    ) -> NotificationProposalPage:
        """List bounded owner-private notification references without sending anything."""
        return NotificationProposals(service).list(session_id, current, cursor=cursor, limit=limit)

    @module.operation(private=True, read_only=False, heads={"web", "api-private"}, audience="everyday")
    def queue_agent_notification(  # noqa: PLR0913, PLR0917 - exact action confirmation
        service: NotificationDelivery,
        confirmation: ApprovalConfirmation,
        session_id: Identity,
        request_id: Request,
        review: dict[str, JsonValue],
        approval_proof: str,
    ) -> Literal["recorded", "uncertain"]:
        """Queue the exact owner-confirmed notice; recorded means queued, never shown."""
        return service.confirm(session_id, request_id, review, approval_proof, confirmation)

    @module.operation(private=True, heads={"web", "api-private"}, audience="everyday")
    def scheduled_notification_attempts(
        service: ScheduledNotificationRecovery,
        session_id: Identity,
        cursor: Annotated[int, Field(ge=0, description="Exclusive journal cursor.")] = 0,
        limit: Annotated[int, Field(ge=1, le=100, description="Maximum attempts per page.")] = 20,
    ) -> ScheduledNotificationPage:
        """Inspect bounded owner-private scheduled attempts and their recorded outcomes."""
        return service.list(session_id, cursor=cursor, limit=limit)

    @module.operation(private=True, read_only=False, heads={"web", "api-private"}, audience="everyday")
    def resolve_scheduled_notification(
        service: ScheduledNotificationRecovery,
        session_id: Identity,
        request_id: Request,
        acknowledge_uncertain: Annotated[
            bool,
            Field(strict=True, description="Acknowledge the notice may already have been queued; no retry or undo."),
        ],
    ) -> ScheduledNotificationView:
        """Inspect the receipt or set aside uncertainty; never resend or undo a notice."""
        return service.resolve(session_id, request_id, acknowledge_uncertain=acknowledge_uncertain)

    @module.operation(private=True, heads={"web", "api-private"}, audience="everyday")
    def notification_outcome(service: NotificationDelivery, session_id: Identity, request_id: Request) -> str:
        """Inspect a prior queue attempt without sending again."""
        return service.recover(session_id, request_id, write_receipt=False)


def _register_review_services(module: Module) -> None:
    module.service(ConnectedReadGrants)
    module.service(ConnectedReadSets)
    module.service(ConnectedReadSetReviews)
    module.service(ConnectedReadSetApprovals)
    module.service(AutonomousSchedules)
    _register_connected_read_operations(module)
    _register_connected_read_set_operations(module)
    _register_autonomous_schedule_operations(module)


def register_session_operations(module: Module) -> None:  # noqa: C901 - operation declarations define one shared head surface
    """Register once on the owning module; constructing the service performs no private I/O."""
    module.service(SessionCrud, _crud)
    module.service(UserChatService, _user_chat_service)
    _register_review_services(module)
    _register_skill_operations(module)
    _register_contract_review_operations(module)
    _register_notification_operations(module)
    _register_user_chat_operations(module)
    register_model_policy_operations(module)
    module.service(ApprovalIssuer, _approval_issuer)
    module.service(ApprovalConfirmation, _approval_confirmation)
    module.service(CheckInChatContextSource, _check_in_context_source)

    @module.operation(private=True, read_only=False, audience="everyday")
    def new_chat(
        service: SessionCrud,
        request_id: Request,
        name: Annotated[
            str, Field(description="Your personal display name for this Heinzel.", min_length=1, max_length=100)
        ] = "New Heinzel",
        appearance: Appearance = None,
    ) -> SessionSnapshot:
        """Create a durable personal Heinzel with an idempotent request, name and look."""
        return service.new_chat(request_id, name, appearance)

    @module.operation(private=True, audience="everyday")
    def sessions(service: SessionCrud) -> SessionList:
        """List your durable personal Heinzels, including new conversations without an approved contract."""
        return service.listing()

    @module.operation(private=True, audience="everyday")
    def session(
        service: SessionCrud,
        session_id: Identity,
        cursor: Annotated[
            str | None, Field(description="The opaque next_cursor from this session's previous page.", max_length=1024)
        ] = None,
        limit: Annotated[int, Field(description="The most messages returned in this page.", ge=1, le=200)] = 50,
    ) -> SessionSnapshot:
        """Open one durable Heinzel and an ordered page of its accepted messages, even during an active turn."""
        return service.snapshot(session_id, cursor=cursor, limit=limit)

    @module.operation(private=True, read_only=False, audience="everyday")
    def profile(  # noqa: PLR0913 - flat private HTTP body binds request identity and optimistic profile revision
        service: SessionCrud,
        session_id: Identity,
        request_id: Request,
        expected_revision: Annotated[
            int, Field(description="The exact profile_revision you last read.", strict=True, ge=1)
        ],
        *,
        name: Annotated[
            str | None,
            Field(description="The new personal display name; null leaves it unchanged.", min_length=1, max_length=100),
        ] = None,
        appearance: Appearance = None,
    ) -> SessionSnapshot:
        """Save this Heinzel's name or look against the exact profile revision you saw."""
        return service.profile(session_id, request_id, expected_revision, name=name, appearance=appearance)

    @module.operation(
        private=True,
        read_only=False,
        # An inactive proposal is safe to create from an authenticated local MCP client. Activation still requires
        # exact-intent human approval, and mcp-stdio keeps it out of unauthenticated streamable HTTP.
        heads=frozenset({"cli", "web", "api-private", "mcp-stdio", "mcp-check-in"}),
        audience="everyday",
    )
    def propose_check_in(
        service: SessionCrud,
        session_id: Identity,
        request_id: Request,
        timezone: Annotated[
            str, Field(description="The IANA time zone for the schedule preview.", min_length=1, max_length=128)
        ] = "UTC",
    ) -> SessionSnapshot:
        """Propose local five-minute check-ins; the schedule stays inactive until activated."""
        return service.propose_check_in(session_id, request_id, timezone)

    @module.operation(private=True, read_only=False, audience="everyday")
    def associate_artifact(
        service: SessionCrud,
        session_id: Identity,
        request_id: Request,
        kind: Annotated[
            Literal["document", "spreadsheet", "presentation"],
            Field(description="The claimed Google file type; not verified with the provider."),
        ],
        ref: Annotated[
            str,
            Field(description="Canonical Google editor URL.", max_length=256),
        ],
    ) -> PersonalArtifact:
        """Associate a Google file locally; no access, provider ownership or permission is verified."""
        return service.associate_artifact(session_id, request_id, kind, ref)

    @module.operation(private=True, audience="everyday")
    def personal_artifacts(service: SessionCrud, session_id: Identity) -> PersonalArtifacts:
        """List local principal-submitted file associations without accessing the provider."""
        return service.personal_artifacts(session_id)

    @module.operation(private=True, read_only=False, audience="everyday")
    def propose_scheduled_agent(  # noqa: PLR0913, PLR0917 - flat private API
        service: SessionCrud,
        session_id: Identity,
        request_id: Request,
        contract: Annotated[
            str,
            Field(description="Exact instructions to preview for a future agent turn.", min_length=1, max_length=4000),
        ],
        max_usd: Annotated[
            float, Field(description="Proposed per-turn USD limit; not yet enforced.", gt=0, allow_inf_nan=False)
        ],
        timeout_seconds: Annotated[int, Field(description="Proposed maximum seconds per turn.", ge=1, le=300)],
    ) -> ScheduledAgentProposal:
        """Save an inert agent execution proposal; it neither approves nor starts a model turn."""
        return service.propose_scheduled_agent(session_id, request_id, contract, max_usd, timeout_seconds)

    @module.operation(private=True, read_only=False, audience="everyday")
    def amend_scheduled_agent(  # noqa: PLR0913, PLR0917 - exact revision and complete flat private API terms
        service: SessionCrud,
        session_id: Identity,
        request_id: Request,
        expected_revision: Annotated[
            int, Field(description="The exact proposal revision inspected.", strict=True, ge=1)
        ],
        contract: Annotated[
            str, Field(description="Complete replacement instructions.", min_length=1, max_length=4000)
        ],
        max_usd: Annotated[
            float, Field(description="Proposed per-turn USD limit; not yet enforced.", gt=0, allow_inf_nan=False)
        ],
        timeout_seconds: Annotated[int, Field(description="Proposed maximum seconds per turn.", ge=1, le=300)],
    ) -> ScheduledAgentProposal:
        """Propose revised terms; an independent human review and approval is still required."""
        return service.amend_scheduled_agent(
            session_id, request_id, expected_revision, contract, max_usd, timeout_seconds
        )

    @module.operation(private=True, audience="everyday")
    def latest_scheduled_agent_proposal(service: SessionCrud, session_id: Identity) -> ScheduledAgentProposal | None:
        """Read the current inert proposal for the personal session, if one exists."""
        return service.latest_scheduled_agent_proposal(session_id)

    @module.operation(private=True, audience="everyday")
    def scheduled_agent_proposal(
        service: SessionCrud, session_id: Identity, request_id: Request
    ) -> ScheduledAgentProposal:
        """Inspect the exact inert proposal; a separate future human approval is required to run it."""
        return service.scheduled_agent_proposal(session_id, request_id)

    @module.operation(private=True, read_only=False, audience="everyday")
    def schedule_control(  # noqa: PLR0913, PLR0917 - flat private HTTP input binds the approval to its preview
        service: SessionCrud,
        current: CurrentUser,
        confirmation: ApprovalConfirmation,
        kernel: Kernel,
        session_id: Identity,
        request_id: Request,
        expected_revision: Annotated[
            int, Field(description="The schedule revision you last inspected.", strict=True, ge=1)
        ],
        action: Annotated[
            Literal["activate", "pause", "resume"],
            Field(description="Explicitly activate the proposal, pause it, or resume a paused schedule."),
        ],
        approval_proof: Annotated[
            str | None,
            Field(
                description="Human-issued exact-intent proof required to activate or resume; never returned by reads. "
                "On CLI, pass `-` to read it from hidden input or stdin."
            ),
        ] = None,
        expected_contract_version: Annotated[
            int, Field(description="The contract version in the schedule preview.", strict=True, ge=1)
        ] = 1,
        expected_preview_hash: Annotated[
            str | None,
            Field(description="The preview hash shown for this exact schedule.", min_length=1, max_length=64),
        ] = None,
    ) -> SessionSnapshot:
        """Pause a schedule; activation and resume require an exact-intent approval from a trusted issuer.

        Raises:
            HeinzelError: If the schedule is stale or approval is invalid or unavailable.
        """
        authentication = current.context().authentication
        if authentication == "cli":
            issued_in_confirm = confirmation.matches(
                "heinzel",
                "schedule-control",
                {
                    "session_id": session_id,
                    "request_id": request_id,
                    "expected_revision": expected_revision,
                    "action": action,
                    "approval_proof": approval_proof,
                    "expected_contract_version": expected_contract_version,
                    "expected_preview_hash": expected_preview_hash,
                },
            )
            if approval_proof not in {None, "-"} and not issued_in_confirm:
                msg = "Use `--approval-proof -` on the CLI so the proof is not exposed in process arguments"
                raise HeinzelError(msg)
            if approval_proof == "-":
                approval_proof = (
                    getpass.getpass("Approval proof: ") if sys.stdin.isatty() else sys.stdin.readline().rstrip("\r\n")
                )
        elif approval_proof == "-":
            msg = "Approval input `-` is only available on the CLI"
            raise HeinzelError(msg)

        with _schedule_admission(service, kernel) if action in {"activate", "resume"} else nullcontext():
            return service.schedule_control(
                session_id,
                request_id,
                expected_revision,
                action,
                approval_proof,
                expected_contract_version,
                expected_preview_hash,
            )

    @module.operation(private=True, read_only=False, audience="everyday")
    def chat(
        service: SessionCrud,
        session_id: Identity,
        request_id: Request,
        message: Annotated[
            str, Field(description="Your message to this Heinzel, retained privately.", min_length=1, max_length=32000)
        ],
        when_busy: Annotated[
            Literal["follow_up", "steer"],
            Field(description="Queue a follow-up or request steering at the next safe boundary."),
        ] = "follow_up",
    ) -> MessageAccepted:
        """Durably queue a message for this Heinzel; acceptance is distinct from a completed model answer."""
        return service.accept(session_id, MessageRequest(request_id=request_id, text=message, when_busy=when_busy))
