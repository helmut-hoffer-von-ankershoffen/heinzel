"""Disposable process-private text index for owner-scoped conversation search.

The authoritative journal and immutable bodies stay in SessionStore. This in-memory
index is rebuilt after restart and never proves completeness against same-user tampering.
"""

import base64
import hashlib
import sqlite3
from collections import OrderedDict
from datetime import datetime
from threading import RLock

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from heinzel import HeinzelError
from heinzel.modules.heinzel._history_search import (
    HistorySearchRequest,
    matches_history,
    search_history,
    validate_history_terms,
)
from heinzel.modules.heinzel._session_crud import SessionCrud, history_cursor_before
from heinzel.modules.heinzel._session_models import SessionMessage

_LIMIT = 50
_MIN_TRIGRAM = 3
_MAX_BYTES = 32 * 1024 * 1024
_MAX_SESSIONS = 4
_LOCK = RLock()
_CACHES: OrderedDict[tuple[str, str, str], "_Cache"] = OrderedDict()


class _Cursor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    owner: str
    storage: str
    session: str
    query: str
    since: str | None
    until: str | None
    high: int = Field(strict=True, ge=0)
    high_hash: str | None
    after: int = Field(strict=True, ge=0)


class IndexedHistoryPage(BaseModel):
    """Private matching messages; coverage is a checked cache, not a completeness proof."""

    session_id: str = Field(description="The owner-scoped conversation searched.")
    items: list[SessionMessage] = Field(description="Matches in this bounded results page.")
    next_cursor: str | None = Field(description="Continuation for the same query and journal prefix, if available.")
    jump_cursors: list[str] = Field(
        default_factory=list, description="Owner and journal-bound message navigation hints."
    )
    mode: str = Field(description="Search method used for this page.")
    completeness: str = Field(default="unknown", description="Whether the retained search can establish completeness.")


class _Cache:
    def __init__(self) -> None:
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        try:
            self.db.execute("PRAGMA secure_delete = ON")
            if self.db.execute("PRAGMA secure_delete").fetchone()[0] != 1:
                msg = "Secure deletion is unavailable"
                raise sqlite3.OperationalError(msg)
            self.db.execute(
                "CREATE TABLE docs(seq INTEGER PRIMARY KEY, prev TEXT, text TEXT NOT NULL, "
                "source TEXT NOT NULL, body_id TEXT NOT NULL, size INTEGER NOT NULL)"
            )
            self.db.execute(
                "CREATE VIRTUAL TABLE terms USING fts5(text, content='docs', content_rowid='seq', tokenize='trigram')"
            )
            self.db.execute(
                "CREATE TRIGGER docs_add AFTER INSERT ON docs BEGIN "
                "INSERT INTO terms(rowid,text) VALUES(new.seq,new.text); END"
            )
            self.db.execute(
                "CREATE TRIGGER docs_del AFTER DELETE ON docs BEGIN "
                "INSERT INTO terms(terms,rowid,text) VALUES('delete',old.seq,old.text); END"
            )
            self.db.execute("INSERT INTO terms(terms,rank) VALUES('secure-delete',1)")
        except sqlite3.DatabaseError:
            self.db.close()
            raise
        self.high = 0
        self.high_hash: str | None = None
        self.count = 0
        self.bytes = 0
        self.verified_changes = -1
        self.body_states: dict[str, tuple[int, int, int, int] | None] = {"main": None, "control": None}

    def close(self) -> None:
        self.db.close()


def _get(storage: str, owner: str, session: str) -> _Cache:
    key = (storage, owner, session)
    cached = _CACHES.pop(key, None)
    if cached is None:
        try:
            cached = _Cache()
        except sqlite3.DatabaseError:
            msg = "Indexed conversation search is unavailable on this installation"
            raise HeinzelError(msg) from None
    _CACHES[key] = cached
    while len(_CACHES) > _MAX_SESSIONS:
        _CACHES.popitem(last=False)[1].close()
    return cached


def _invalidate(storage: str, owner: str, session: str) -> None:
    cache = _CACHES.pop((storage, owner, session), None)
    if cache is not None:
        cache.close()


def _cursor(request: HistorySearchRequest, owner: str, storage: str) -> _Cursor | None:
    if request.cursor is None:
        return None
    try:
        raw = base64.b64decode(request.cursor + "=" * (-len(request.cursor) % 4), altchars=b"-_", validate=True)
        state = _Cursor.model_validate_json(raw)
    except (ValueError, ValidationError):
        msg = "This search cursor is invalid or belongs to a different search"
        raise HeinzelError(msg) from None
    expected = (owner, storage, request.session_id, request.query, _time(request.since), _time(request.until))
    if (
        state.owner,
        state.storage,
        state.session,
        state.query,
        state.since,
        state.until,
    ) != expected or state.after > state.high:
        msg = "This search cursor is invalid or belongs to a different search"
        raise HeinzelError(msg)
    return state


def _time(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _remove_doc(cache: _Cache, seq: int) -> None:
    """Drop a document the host itself expired, from a verified index.

    Older SQLite can then fail an integrity check on a sound index, so this trusted change alone counts as verified.
    Committing flushes the terms and moves the change count, so only count after it.
    """
    _verified(cache)
    cache.db.execute("DELETE FROM docs WHERE seq = ?", (seq,))
    cache.db.commit()
    cache.verified_changes = cache.db.total_changes


def _verified(cache: _Cache) -> None:
    if cache.db.total_changes == cache.verified_changes:
        return
    count = cache.db.execute("SELECT count(*) FROM docs").fetchone()[0]
    if count != cache.count:
        msg = "Conversation search index needs rebuilding"
        raise HeinzelError(msg)
    try:
        cache.db.execute("INSERT INTO terms(terms, rank) VALUES('integrity-check', 1)")
        cache.verified_changes = cache.db.total_changes
    except sqlite3.DatabaseError:
        msg = "Conversation search index needs rebuilding"
        raise HeinzelError(msg) from None


def _advance(
    cache: _Cache, service: SessionCrud, session: str, anchor: tuple[int, str | None], max_pages: int | None
) -> bool:
    if cache.high == anchor[0] and cache.high_hash != anchor[1]:
        msg = "Conversation search index no longer matches its source; retry to rebuild"
        raise HeinzelError(msg)
    if cache.high > anchor[0]:
        return False
    position = (cache.high, cache.high_hash)
    pages = 0
    while position[0] < anchor[0] and (max_pages is None or pages < max_pages):
        page, _ = service.repository.control_indexed_page(session, position=position, anchor=anchor, limit=_LIMIT)
        if not page.events:
            msg = "Conversation search index is incomplete"
            raise HeinzelError(msg)
        for event in page.events:
            if event.kind not in {"message.accepted", "message.replied"}:
                continue
            message = service.read_message(session, event)
            if message.status == "expired":
                continue
            text = message.text.casefold()
            size = len(text.encode("utf-8"))
            if cache.bytes + size > _MAX_BYTES:
                msg = "Conversation search index is too large to build in memory"
                raise HeinzelError(msg)
            source, body_id = event.data.get("source"), event.data.get("body_id")
            if source not in {"main", "control"} or not isinstance(body_id, str):
                msg = "Conversation search encountered an invalid body reference"
                raise HeinzelError(msg)
            cache.db.execute(
                "INSERT INTO docs(seq,prev,text,source,body_id,size) VALUES(?,?,?,?,?,?)",
                (event.seq, event.prev, text, source, body_id, size),
            )
            cache.count += 1
            cache.bytes += size
        position = (page.events[-1].seq, page.hashes[-1])
        pages += 1
    cache.db.commit()
    cache.high, cache.high_hash = position
    return pages > 0


def _sync(cache: _Cache, service: SessionCrud, session: str) -> tuple[int, str | None]:
    _first, anchor = service.repository.control_indexed_page(session, position=(0, None), anchor=None, limit=1)
    if cache.high > anchor[0]:
        msg = "Conversation search index needs rebuilding"
        raise HeinzelError(msg)
    _advance(cache, service, session, anchor, None)
    return anchor


def _reconcile(cache: _Cache, service: SessionCrud, session: str) -> set[int]:
    removed: set[int] = set()
    for source in ("main", "control"):
        signature, available = service.repository.search_body_inventory(
            session, source, known=cache.body_states[source]
        )
        if available is not None:
            for seq, body_id, size in cache.db.execute(
                "SELECT seq, body_id, size FROM docs WHERE source = ?", (source,)
            ).fetchall():
                if body_id not in available:
                    _remove_doc(cache, seq)
                    cache.count -= 1
                    cache.bytes -= size
                    removed.add(seq)
        cache.body_states[source] = signature
    cache.db.commit()
    return removed


def _query(
    cache: _Cache, service: SessionCrud, request: HistorySearchRequest, state: _Cursor
) -> tuple[list[SessionMessage], int | None]:
    phrase = '"' + request.query.casefold().replace('"', '""') + '"'
    rows = cache.db.execute(
        "SELECT docs.seq,docs.prev,docs.text FROM terms JOIN docs ON docs.seq=terms.rowid "
        "WHERE terms MATCH ? AND docs.seq > ? AND docs.seq <= ? ORDER BY docs.seq LIMIT ?",
        (phrase, state.after, state.high, _LIMIT + 1),
    ).fetchall()
    results: list[SessionMessage] = []
    for seq, prev, indexed_text in rows[:_LIMIT]:
        page, _ = service.repository.control_indexed_page(
            request.session_id, position=(seq - 1, prev), anchor=(state.high, state.high_hash), limit=1
        )
        if len(page.events) != 1 or page.events[0].seq != seq:
            msg = "Conversation search index needs rebuilding"
            raise HeinzelError(msg)
        message = service.read_message(request.session_id, page.events[0])
        if message.status == "expired":
            _remove_doc(cache, seq)
            cache.count -= 1
            cache.bytes -= len(indexed_text.encode("utf-8"))
        elif matches_history(message, request):
            results.append(message)
    cache.db.commit()
    return results, rows[_LIMIT - 1][0] if len(rows) > _LIMIT else None


def indexed_search(service: SessionCrud, request: HistorySearchRequest) -> IndexedHistoryPage:
    """Search private retained messages; short queries use explicitly incremental fallback.

    Raises:
        HeinzelError: If the index cannot be checked or the owner, cursor or journal is invalid.
    """
    validate_history_terms(request)
    if len(request.query.casefold()) < _MIN_TRIGRAM or "\x00" in request.query:
        fallback = search_history(service, request)
        return IndexedHistoryPage(
            session_id=request.session_id,
            items=fallback.items,
            next_cursor=fallback.next_cursor,
            jump_cursors=fallback.jump_cursors,
            mode="bounded-scan",
        )
    owner = service.repository.principal_id
    storage = service.repository.search_storage_id
    storage_binding = hashlib.sha256(storage.encode()).hexdigest()
    state = _cursor(request, owner, storage_binding)
    with _LOCK:
        cache = _get(storage, owner, request.session_id)
        try:
            _verified(cache)
            high, high_hash = _sync(cache, service, request.session_id)
            if state is None:
                state = _Cursor(
                    owner=owner,
                    storage=storage_binding,
                    session=request.session_id,
                    query=request.query,
                    since=_time(request.since),
                    until=_time(request.until),
                    high=high,
                    high_hash=high_hash,
                    after=0,
                )
            service.repository.control_indexed_page(
                request.session_id, position=(0, None), anchor=(state.high, state.high_hash), limit=1
            )
            _reconcile(cache, service, request.session_id)
            _verified(cache)
            results, after = _query(cache, service, request, state)
            removed = _reconcile(cache, service, request.session_id)
            results = [message for message in results if message.seq not in removed]
        except sqlite3.DatabaseError:
            _invalidate(storage, owner, request.session_id)
            msg = "Conversation search index needs rebuilding; retry the search"
            raise HeinzelError(msg) from None
        except HeinzelError:
            _invalidate(storage, owner, request.session_id)
            raise
        cursor = None
        if after is not None:
            cursor = (
                base64.urlsafe_b64encode(state.model_copy(update={"after": after}).model_dump_json().encode())
                .decode()
                .rstrip("=")
            )
        return IndexedHistoryPage(
            session_id=request.session_id,
            items=results,
            next_cursor=cursor,
            jump_cursors=[
                history_cursor_before(service, request.session_id, item.seq, state.high, state.high_hash)
                for item in results
            ],
            mode="trigram",
        )


class IndexedHistoryStep(BaseModel):
    """One bounded indexing or results step within a single conversation."""

    page: IndexedHistoryPage
    indexing: bool


def indexed_search_step(service: SessionCrud, request: HistorySearchRequest) -> IndexedHistoryStep:
    """Advance no more than one event page before returning progress.

    Raises:
        HeinzelError: If the owner, cursor, index or source is invalid.
    """
    validate_history_terms(request)
    if len(request.query.casefold()) < _MIN_TRIGRAM or "\x00" in request.query:
        return IndexedHistoryStep(page=indexed_search(service, request), indexing=False)
    owner = service.repository.principal_id
    storage = service.repository.search_storage_id
    binding = hashlib.sha256(storage.encode()).hexdigest()
    state = _cursor(request, owner, binding)
    with _LOCK:
        cache = _get(storage, owner, request.session_id)
        try:
            _verified(cache)
            if state is None:
                _page, (high, high_hash) = service.repository.control_indexed_page(
                    request.session_id, position=(0, None), anchor=None, limit=1
                )
                state = _Cursor(
                    owner=owner,
                    storage=binding,
                    session=request.session_id,
                    query=request.query,
                    since=_time(request.since),
                    until=_time(request.until),
                    high=high,
                    high_hash=high_hash,
                    after=0,
                )
            anchor = (state.high, state.high_hash)
            service.repository.control_indexed_page(request.session_id, position=(0, None), anchor=anchor, limit=1)
            if cache.high > state.high:
                service.repository.control_indexed_page(
                    request.session_id, position=(0, None), anchor=(cache.high, cache.high_hash), limit=1
                )
            _reconcile(cache, service, request.session_id)
            progressed = _advance(cache, service, request.session_id, anchor, 1)
            _reconcile(cache, service, request.session_id)
            _verified(cache)
            if progressed:
                progress_cursor = base64.urlsafe_b64encode(state.model_dump_json().encode()).decode().rstrip("=")
                return IndexedHistoryStep(
                    page=IndexedHistoryPage(
                        session_id=request.session_id, items=[], next_cursor=progress_cursor, mode="trigram"
                    ),
                    indexing=True,
                )
            results, after = _query(cache, service, request, state)
            removed = _reconcile(cache, service, request.session_id)
            results = [message for message in results if message.seq not in removed]
        except sqlite3.DatabaseError:
            _invalidate(storage, owner, request.session_id)
            msg = "Conversation search index needs rebuilding; retry the search"
            raise HeinzelError(msg) from None
        except HeinzelError:
            _invalidate(storage, owner, request.session_id)
            raise
        cursor = None
        if after is not None:
            cursor = (
                base64.urlsafe_b64encode(state.model_copy(update={"after": after}).model_dump_json().encode())
                .decode()
                .rstrip("=")
            )
        return IndexedHistoryStep(
            page=IndexedHistoryPage(
                session_id=request.session_id,
                items=results,
                next_cursor=cursor,
                mode="trigram",
                jump_cursors=[
                    history_cursor_before(service, request.session_id, item.seq, state.high, state.high_hash)
                    for item in results
                ],
            ),
            indexing=False,
        )
