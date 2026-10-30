"""Owner-bound notification drafts; proposing never sends or authorizes future delivery."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from heinzel import CurrentUser, HeinzelError, SessionWriter
from heinzel.kernel._approval import arguments_hash
from heinzel.modules.heinzel._session_bodies import immutable_body
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import RequestId, SessionId

_MAX_PAGE = 100


class NotificationProposal(BaseModel):
    """Exact private local-notification draft; no delivery or approval is implied."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    session_id: SessionId = Field(description="The owner-bound personal session.")
    request_id: RequestId = Field(description="The stable host-generated proposal identity.")
    topic: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
        description="Model-chosen topic, not a verified source identity or a deduplication guarantee.",
    )
    title: str = Field(min_length=1, max_length=120, description="Exact proposed notification title.")
    body: str = Field(max_length=500, description="Exact proposed notification body.")
    delivery: Literal["local-notification"] = Field(
        default="local-notification", description="Local in-app or native notification under current user policy."
    )
    disclosure: Literal["The title and body may appear on this device; queued does not mean shown."] = Field(
        default="The title and body may appear on this device; queued does not mean shown.",
        description="The exact local delivery and visibility limitation.",
    )
    status: Literal["proposed"] = Field(default="proposed", description="A draft, not permission to notify.")


class NotificationProposalRef(BaseModel):
    """One bounded owner-private notification draft reference."""

    request_id: RequestId = Field(description="Stable proposal request identity.")
    seq: int = Field(gt=0, description="Exclusive journal cursor for this reference.")


class NotificationProposalPage(BaseModel):
    """A bounded page of references; bodies are read separately by exact identity."""

    session_id: SessionId = Field(description="The owner-bound personal session.")
    items: list[NotificationProposalRef] = Field(description="Owner-private draft references.")
    next_cursor: int | None = Field(description="Exclusive cursor for the next page, if any.")


class NotificationProposals:
    """Persist local-only proposals behind an owner-bound main writer."""

    def __init__(self, crud: SessionCrud) -> None:
        self.crud = crud

    def propose(self, proposal: NotificationProposal, owner: str, writer: SessionWriter) -> NotificationProposal:
        """Stage or replay exact draft content without contacting the notification service.

        Raises:
            HeinzelError: If the request is unowned, unlocked, invalid or conflicts with its earlier content.
        """
        repo = self.crud.repository
        session, request = proposal.session_id, proposal.request_id
        if repo.principal_id != owner or not repo.is_main_writer(session, writer):
            msg = "The notification draft does not belong to this owner and turn"
            raise HeinzelError(msg)
        with repo.control_writer(session) as control:
            events = repo.control_read(session).events
            matches = [event for event in events if event.kind == "notification.proposed" and event.key == request]
            if matches:
                if len(matches) != 1:
                    msg = "The notification draft record is inconsistent"
                    raise HeinzelError(msg)
                previous = NotificationProposal.model_validate(self.crud.read_body(session, matches[0]))
                if arguments_hash(previous.model_dump(mode="json")) != arguments_hash(proposal.model_dump(mode="json")):
                    msg = "This notification request already names another draft"
                    raise HeinzelError(msg)
                return previous
            saved = immutable_body(control, "notification-proposal", proposal.model_dump(mode="json"), control=True)
            control.append("notification.proposed", key=request, data=saved.model_dump(mode="json"))
        return proposal

    def get(self, session: str, request: str, current: CurrentUser) -> NotificationProposal:
        """Read one exact owner-private draft without exposing a delivery capability.

        Raises:
            HeinzelError: If the draft or authenticated owner is unavailable.
        """
        repo = self.crud.repository
        self._reader(current)
        matches = [
            event
            for event in repo.control_read(session).events
            if event.kind == "notification.proposed" and event.key == request
        ]
        if len(matches) != 1:
            msg = "The notification draft is unavailable"
            raise HeinzelError(msg)
        proposal = NotificationProposal.model_validate(self.crud.read_body(session, matches[0]))
        if proposal.session_id != session or proposal.request_id != request:
            msg = "The notification draft identity is inconsistent"
            raise HeinzelError(msg)
        return proposal

    def list(self, session: str, current: CurrentUser, *, cursor: int = 0, limit: int = 20) -> NotificationProposalPage:
        """List bounded owner-private draft references without returning their contents.

        Raises:
            HeinzelError: If the owner or page bounds cannot be verified.
        """
        self._reader(current)
        if type(cursor) is not int or cursor < 0 or type(limit) is not int or not 1 <= limit <= _MAX_PAGE:
            msg = "The notification page bounds are invalid"
            raise HeinzelError(msg)
        events = [
            event
            for event in self.crud.repository.control_read(session).events
            if event.kind == "notification.proposed" and event.seq > cursor
        ]
        selected = events[:limit]
        if any(event.key is None for event in selected):
            msg = "The notification draft reference is invalid"
            raise HeinzelError(msg)
        items = [
            NotificationProposalRef(request_id=event.key, seq=event.seq) for event in selected if event.key is not None
        ]
        return NotificationProposalPage(
            session_id=session, items=items, next_cursor=selected[-1].seq if len(events) > limit else None
        )

    def _reader(self, current: CurrentUser) -> None:
        context = current.context()
        if (
            context.principal != current.owner()
            or context.principal.id != self.crud.repository.principal_id
            or context.authentication not in {"cli", "api-bearer", "web-session", "web-device"}
            or "private:read" not in context.permissions
        ):
            msg = "The notification draft requires its authenticated owner"
            raise HeinzelError(msg)
