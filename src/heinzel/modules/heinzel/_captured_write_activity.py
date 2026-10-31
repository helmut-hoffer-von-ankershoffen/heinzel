"""Read-only owner projection of verified captured-write activity."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from heinzel import CurrentUser, HeinzelError, SessionEvent
from heinzel.kernel import CapturedWriteService
from heinzel.modules.heinzel._captured_write_host import CapturedWriteHost
from heinzel.modules.heinzel._session_crud import SessionCrud

_PAGE_LIMIT = 20


class CapturedWriteActivityItem(BaseModel):
    """One saved write draft or verified provider acknowledgement, without replacement text."""

    request_id: str = Field(description="Owner-scoped draft identity.")
    seq: int = Field(description="Latest recorded activity position.", gt=0)
    at: datetime = Field(description="Time of the latest recorded activity.")
    provider: str = Field(description="Host-selected provider recorded with the draft.")
    account: str = Field(description="Account recorded in the reviewed draft.")
    source: str = Field(description="Source identity recorded in the reviewed draft.")
    reference: str = Field(description="Exact artifact reference recorded in the reviewed draft.")
    status: Literal["draft", "uncertain", "acknowledged"] = Field(description="Verified local attempt disposition.")
    revision: str | None = Field(description="Provider-reported acknowledgement revision, if any.")


class CapturedWriteActivityPage(BaseModel):
    """A bounded owner-private page of verified captured-write activity."""

    session_id: str = Field(description="The owner-scoped conversation.")
    items: list[CapturedWriteActivityItem] = Field(description="Latest verified activity, newest first.")
    next_before: int | None = Field(description="Exclusive activity sequence for the next page, if available.")


def _latest(events: list[SessionEvent]) -> dict[str, SessionEvent]:
    drafts = {event.key: event for event in events if event.kind == "write.drafted" and event.key is not None}
    if len(drafts) != sum(event.kind == "write.drafted" for event in events):
        msg = "The captured write activity is inconsistent"
        raise HeinzelError(msg)
    latest = dict(drafts)
    for event in events:
        if event.kind == "write.attempted":
            if not isinstance(event.key, str) or not event.key.startswith("write-attempt-"):
                msg = "The captured write activity is inconsistent"
                raise HeinzelError(msg)
            key = event.key.removeprefix("write-attempt-")
        elif event.kind == "write.acknowledged":
            if not isinstance(event.key, str):
                msg = "The captured write activity is inconsistent"
                raise HeinzelError(msg)
            key = event.key
        else:
            continue
        if key not in drafts:
            msg = "The captured write activity is inconsistent"
            raise HeinzelError(msg)
        latest[key] = event
    return latest


def captured_write_activity(
    crud: SessionCrud, current: CurrentUser, session: str, *, before: int | None = None, limit: int = 20
) -> CapturedWriteActivityPage:
    """Project recorded attempts without provider calls or write authority.

    Raises:
        HeinzelError: If owner, records, paging, or a stable journal cannot be verified.
    """
    context = current.context()
    if (
        context.principal != current.owner()
        or context.principal.id != crud.repository.principal_id
        or context.authentication not in {"cli", "api-bearer", "web-session", "web-device"}
        or "private:write" not in context.permissions
        or type(limit) is not int
        or not 1 <= limit <= _PAGE_LIMIT
        or (before is not None and (type(before) is not int or before < 1))
    ):
        msg = "The captured write activity is unavailable"
        raise HeinzelError(msg)
    journal = crud.repository.control_read(session)
    latest = _latest(journal.events)
    selected = sorted(
        (event for event in latest.values() if before is None or event.seq < before),
        key=lambda event: event.seq,
        reverse=True,
    )
    host = CapturedWriteHost(crud, current, CapturedWriteService())
    items: list[CapturedWriteActivityItem] = []
    for event in selected[:limit]:
        if event.key is None:
            msg = "The captured write activity is inconsistent"
            raise HeinzelError(msg)
        request = event.key.removeprefix("write-attempt-") if event.kind == "write.attempted" else event.key
        draft = host.current(session, request)
        status = host.projection(session, request)
        receipt = status.receipt
        items.append(
            CapturedWriteActivityItem(
                request_id=request,
                seq=event.seq,
                at=event.ts,
                provider=draft.provider,
                account=draft.account,
                source=draft.source,
                reference=draft.reference,
                status=status.status,
                revision=receipt.revision if receipt is not None else None,
            )
        )
    after = crud.repository.control_read(session)
    if after.hashes != journal.hashes:
        msg = "The captured write activity changed; refresh it"
        raise HeinzelError(msg)
    return CapturedWriteActivityPage(
        session_id=session, items=items, next_before=items[-1].seq if len(selected) > limit else None
    )
