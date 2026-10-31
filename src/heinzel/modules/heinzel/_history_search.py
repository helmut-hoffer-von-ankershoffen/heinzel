"""Bounded incremental owner-private message search over authoritative journal events.

Normal indexed pages avoid reading the conversation spine; recovery may rescan it.
Cursors are navigation hints, not credentials or tamper-proof attestations.
"""

import base64

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from heinzel import HeinzelError
from heinzel.modules.heinzel._session_crud import SessionCrud, history_cursor_before
from heinzel.modules.heinzel._session_models import SessionMessage

SCAN = 50


class _Cursor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: int = Field(strict=True, ge=1, le=1)
    owner: str
    session: str
    query: str
    since: AwareDatetime | None
    until: AwareDatetime | None
    after: int = Field(strict=True, ge=0)
    high: int = Field(strict=True, ge=0)
    previous: str | None
    high_hash: str | None


class HistorySearchPage(BaseModel):
    """One scan window and its continuation; an empty page can still have more."""

    session_id: str = Field(description="The owner-scoped conversation searched.")
    items: list[SessionMessage] = Field(description="Matches in this bounded scan window.")
    next_cursor: str | None = Field(description="Continuation for the same query and journal prefix, if available.")
    scanned: int = Field(ge=0, le=SCAN, description="Number of message bodies examined in this scan window.")
    jump_cursors: list[str] = Field(
        default_factory=list, description="Owner and journal-bound message navigation hints."
    )


class HistorySearchRequest(BaseModel):
    """Exact text and time bounds for one owner-private conversation."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    session_id: str
    query: str = Field(default="", max_length=200)
    since: AwareDatetime | None = None
    until: AwareDatetime | None = None
    cursor: str | None = Field(default=None, max_length=2048)


def validate_history_terms(request: HistorySearchRequest) -> None:
    if not request.query and request.since is None and request.until is None:
        msg = "Choose text or a time range for conversation search"
        raise HeinzelError(msg)
    if request.since is not None and request.until is not None and request.since > request.until:
        msg = "The search start must not be after its end"
        raise HeinzelError(msg)


def _cursor(request: HistorySearchRequest, owner: str) -> _Cursor | None:
    if request.cursor is None:
        return None
    try:
        raw = base64.b64decode(request.cursor + "=" * (-len(request.cursor) % 4), altchars=b"-_", validate=True)
        state = _Cursor.model_validate_json(raw)
    except (ValueError, ValidationError):
        msg = "This search cursor is invalid or belongs to a different search"
        raise HeinzelError(msg) from None
    if (state.owner, state.session, state.query, state.since, state.until) != (
        owner,
        request.session_id,
        request.query,
        request.since,
        request.until,
    ):
        msg = "This search cursor is invalid or belongs to a different search"
        raise HeinzelError(msg)
    return state


def matches_history(message: SessionMessage, request: HistorySearchRequest) -> bool:
    if message.status == "expired":
        return False
    if request.since is not None and message.at < request.since:
        return False
    if request.until is not None and message.at > request.until:
        return False
    return request.query.casefold() in message.text.casefold()


def search_history(service: SessionCrud, request: HistorySearchRequest) -> HistorySearchPage:
    """Search a fixed journal prefix, reading at most SCAN message bodies per call.

    Raises:
        HeinzelError: If the query, time range, cursor, or owner-bound session is invalid.
    """
    validate_history_terms(request)
    owner = service.repository.principal_id
    state = _cursor(request, owner)
    position = (state.after, state.previous) if state else (0, None)
    anchor = (state.high, state.high_hash) if state else None
    page, (high, high_hash) = service.repository.control_indexed_page(
        request.session_id, position=position, anchor=anchor, limit=SCAN
    )
    selected = [event for event in page.events if event.kind in {"message.accepted", "message.replied"}]
    matches = [
        message
        for event in selected
        if matches_history(message := service.read_message(request.session_id, event), request)
    ]
    after = page.events[-1].seq if page.events else position[0]
    next_cursor = None
    if after < high:
        next_state = _Cursor(
            version=1,
            owner=owner,
            session=request.session_id,
            query=request.query,
            since=request.since,
            until=request.until,
            after=after,
            previous=page.hashes[-1],
            high=high,
            high_hash=high_hash,
        )
        next_cursor = base64.urlsafe_b64encode(next_state.model_dump_json().encode()).decode().rstrip("=")
    return HistorySearchPage(
        session_id=request.session_id,
        items=matches,
        next_cursor=next_cursor,
        scanned=len(selected),
        jump_cursors=[
            history_cursor_before(service, request.session_id, item.seq, high, high_hash) for item in matches
        ],
    )
