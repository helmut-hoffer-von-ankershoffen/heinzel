"""Bounded owner-catalog traversal of separately anchored conversation searches."""

import base64
import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from heinzel import HeinzelError
from heinzel.modules.heinzel._history_fts import indexed_search_step
from heinzel.modules.heinzel._history_search import HistorySearchRequest, validate_history_terms
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionId, SessionMessage

_SESSION = TypeAdapter(SessionId)


class CrossHistoryRequest(BaseModel):
    """One owner's query; continuation is a navigation hint, not authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    query: str = Field(default="", max_length=200)
    since: str | None = None
    until: str | None = None
    cursor: str | None = Field(default=None, max_length=8192)


class CrossHistoryPage(BaseModel):
    """At most one agent's results or explicit bounded progress."""

    status: Literal["indexing", "results", "next-agent", "done", "blocked"] = Field(
        description="Bounded search progress for this step."
    )
    session_id: str | None = Field(description="Owner-scoped conversation for this step, if any.")
    items: list[SessionMessage] = Field(description="Matches from at most one conversation in this step.")
    jump_cursors: list[str] = Field(
        default_factory=list, description="Owner and journal-bound message navigation hints."
    )
    next_cursor: str | None = Field(
        description="Continuation for the same owner, query and catalog prefix, if available."
    )
    completeness: Literal["unknown"] = Field(
        default="unknown", description="Search completeness cannot be established."
    )


class _Cursor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    owner: str
    storage: str
    query: str
    since: str | None
    until: str | None
    after: int = Field(ge=0, strict=True)
    previous: str | None
    high: int = Field(ge=0, strict=True)
    high_hash: str | None
    session_cursor: str | None


def _encode(cursor: _Cursor) -> str:
    return base64.urlsafe_b64encode(cursor.model_dump_json().encode()).decode().rstrip("=")


def _decode(request: CrossHistoryRequest, owner: str, storage: str) -> _Cursor | None:
    if request.cursor is None:
        return None
    try:
        raw = base64.b64decode(request.cursor + "=" * (-len(request.cursor) % 4), altchars=b"-_", validate=True)
        value = _Cursor.model_validate_json(raw)
    except (ValueError, ValidationError):
        msg = "This cross-agent search cursor is invalid"
        raise HeinzelError(msg) from None
    if (value.owner, value.storage, value.query, value.since, value.until) != (
        owner,
        storage,
        request.query,
        request.since,
        request.until,
    ) or value.after > value.high:
        msg = "This cross-agent search cursor belongs to a different search"
        raise HeinzelError(msg)
    return value


def search_owner_history(service: SessionCrud, request: CrossHistoryRequest) -> CrossHistoryPage:
    """Visit at most one catalog event and one bounded session step.

    Raises:
        HeinzelError: If the owner, terms or catalog cursor is invalid.
    """
    terms = HistorySearchRequest.model_validate(
        {"session_id": "unused", "query": request.query, "since": request.since, "until": request.until}
    )
    validate_history_terms(terms)
    owner = service.repository.principal_id
    storage = hashlib.sha256(service.repository.search_storage_id.encode()).hexdigest()
    cursor = _decode(request, owner, storage)
    position = (cursor.after, cursor.previous) if cursor else (0, None)
    anchor = (cursor.high, cursor.high_hash) if cursor else None
    page, (high, high_hash) = service.repository.search_catalog_page(position=position, anchor=anchor, limit=1)
    state = cursor or _Cursor(
        owner=owner,
        storage=storage,
        query=request.query,
        since=request.since,
        until=request.until,
        after=0,
        previous=None,
        high=high,
        high_hash=high_hash,
        session_cursor=None,
    )
    if not page.events:
        return CrossHistoryPage(status="done", session_id=None, items=[], next_cursor=None)
    event = page.events[0]
    if event.kind != "creation.reserved":
        msg = "The owner session catalog contains an unexpected record"
        raise HeinzelError(msg)
    try:
        session = _SESSION.validate_python(event.data.get("session_id"), strict=True)
    except ValidationError:
        msg = "The owner session catalog contains an invalid reservation"
        raise HeinzelError(msg) from None
    per_session = terms.model_copy(update={"session_id": session, "cursor": state.session_cursor})
    try:
        result = indexed_search_step(service, per_session)
    except HeinzelError:
        return CrossHistoryPage(status="blocked", session_id=session, items=[], next_cursor=_encode(state))
    if result.page.next_cursor is not None:
        next_state = state.model_copy(update={"session_cursor": result.page.next_cursor})
        return CrossHistoryPage(
            status="indexing" if result.indexing else "results",
            session_id=session,
            items=result.page.items,
            jump_cursors=result.page.jump_cursors,
            next_cursor=_encode(next_state),
        )
    next_state = state.model_copy(update={"after": event.seq, "previous": page.hashes[0], "session_cursor": None})
    done = event.seq == high
    return CrossHistoryPage(
        status="done" if done else "results" if result.page.items else "next-agent",
        session_id=session,
        items=result.page.items,
        jump_cursors=result.page.jump_cursors,
        next_cursor=None if done else _encode(next_state),
    )
