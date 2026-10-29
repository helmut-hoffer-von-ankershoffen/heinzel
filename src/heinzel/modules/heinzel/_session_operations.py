"""Private durable-session operations: one declared surface for CLI, stdio MCP, paired web and private API."""

import getpass
import sys
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, JsonValue

from heinzel import Config, CurrentUser, HeinzelError, Module
from heinzel.kernel import (
    CheckInChatContext,
    CheckInChatContextSource,
    CheckInHistoryMessage,
    DaemonAttestation,
    Kernel,
)
from heinzel.kernel._approval import ApprovalConfirmation, ApprovalIssuer
from heinzel.modules.heinzel._models import HeinzelSettings
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import (
    HeinzelAppearance,
    MessageAccepted,
    MessageRequest,
    PersonalArtifact,
    PersonalArtifacts,
    RequestId,
    ScheduledAgentProposal,
    SessionCosts,
    SessionId,
    SessionList,
    SessionSnapshot,
)
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

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

    def __init__(self, service: SessionCrud, daemon: DaemonAttestation | None) -> None:
        self.service = service
        self.daemon = daemon

    def _check_daemon(self, module: str, operation: str, arguments: dict[str, JsonValue]) -> None:
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

    def review(self, module: str, operation: str, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]:
        self._check_daemon(module, operation, arguments)
        return self.service.review(module, operation, arguments)

    def issue(self, module: str, operation: str, arguments: dict[str, JsonValue], provenance: str) -> str:
        self._check_daemon(module, operation, arguments)
        return self.service.issue(module, operation, arguments, provenance)

    def applied(self, module: str, operation: str, arguments: dict[str, JsonValue]) -> bool:
        return self.service.applied(module, operation, arguments)


def _approval_issuer(service: SessionCrud, kernel: Kernel) -> ApprovalIssuer:
    """Expose the internal issuer only as a kernel service, never as a callable operation."""
    daemon = kernel.resolve(DaemonAttestation) if kernel.provides(DaemonAttestation) else None
    return _SessionApprovalIssuer(service, daemon)


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


def register_session_operations(module: Module) -> None:  # noqa: C901 - operation declarations define one shared head surface
    """Register once on the owning module; constructing the service performs no private I/O."""
    module.service(SessionCrud, _crud)
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
