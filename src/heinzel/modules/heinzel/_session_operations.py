"""Private durable-session operations: one declared surface for CLI, stdio MCP, paired web and private API."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from heinzel import Config, CurrentUser, Module
from heinzel.modules.heinzel._models import HeinzelSettings
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import (
    HeinzelAppearance,
    MessageAccepted,
    MessageRequest,
    RequestId,
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


def register_session_operations(module: Module) -> None:
    """Register once on the owning module; constructing the service performs no private I/O."""
    module.service(SessionCrud, _crud)

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
