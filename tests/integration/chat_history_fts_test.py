"""Real owner-journal fixtures for disposable indexed conversation search."""

import pytest

from heinzel import CurrentUser, HeinzelError
from heinzel.modules.heinzel._history_fts import _CACHES, indexed_search
from heinzel.modules.heinzel._history_search import HistorySearchRequest
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import MessageRequest
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository
from tests.integration.chat_test import AUTH, web_client

pytestmark = pytest.mark.integration


def test_rare_unicode_hit_uses_index_and_short_query_falls_back(make_kernel, monkeypatch):
    kernel = make_kernel(builtins=True)
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    with current.local("cli"):
        session = crud.new_chat("indexed").session_id
        for number in range(250):
            text = "Straße ÜberBLICK rare" if number == 173 else f"ordinary record {number}"
            crud.accept(session, MessageRequest(request_id=f"m{number}", text=text))
        request = HistorySearchRequest(session_id=session, query="STRASSE")
        first = indexed_search(crud, request)
        assert [item.id for item in first.items] == ["m173"]
        assert first.mode == "trigram"
        assert first.completeness == "unknown"
        cache = _CACHES[(crud.repository.search_storage_id, crud.repository.principal_id, session)]
        maintenance = []
        cache.db.set_trace_callback(lambda sql: maintenance.append(True) if "integrity-check" in sql else None)
        original = crud.read_message
        reads = []

        def counted(*args):
            reads.append(1)
            return original(*args)

        monkeypatch.setattr(crud, "read_message", counted)
        again = indexed_search(crud, request)
        assert [item.id for item in again.items] == ["m173"]
        assert len(reads) == 1
        unicode_page = indexed_search(crud, HistorySearchRequest(session_id=session, query="über"))
        assert [item.id for item in unicode_page.items] == ["m173"]
        fallback = indexed_search(crud, HistorySearchRequest(session_id=session, query="xy"))
        assert fallback.mode == "bounded-scan"
        assert fallback.completeness == "unknown"
        assert fallback.next_cursor


def test_index_pagination_append_reopen_and_exact_owner_scope(make_kernel, tmp_path):
    kernel = make_kernel(builtins=True)
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    with current.local("cli"):
        session = crud.new_chat("paginate").session_id
        for number in range(55):
            crud.accept(session, MessageRequest(request_id=f"m{number}", text=f"match needle {number}"))
        first = indexed_search(crud, HistorySearchRequest(session_id=session, query="needle"))
        assert len(first.items) == 50
        assert first.next_cursor
        crud.accept(session, MessageRequest(request_id="later", text="match needle later"))
        reopened = SessionCrud(PersonalSessionRepository(current), crud.costs)
        request = HistorySearchRequest(session_id=session, query="needle", cursor=first.next_cursor)
        tail = indexed_search(reopened, request)
        assert [item.id for item in tail.items] == [f"m{number}" for number in range(50, 55)]
        assert tail.next_cursor is None
        assert indexed_search(reopened, request) == tail
        new = indexed_search(reopened, HistorySearchRequest(session_id=session, query="needle"))
        assert len(new.items) == 50
        assert new.next_cursor
        with pytest.raises(HeinzelError):
            indexed_search(reopened, HistorySearchRequest(session_id=session, query="other", cursor=first.next_cursor))
    foreign = CurrentUser(tmp_path / "foreign" / "identity")
    with foreign.local("cli"):
        foreign_crud = SessionCrud(PersonalSessionRepository(foreign), crud.costs)
        with pytest.raises(HeinzelError):
            indexed_search(foreign_crud, HistorySearchRequest(session_id=session, query="needle"))


def test_missing_docs_and_postings_are_refused_then_rebuilt(make_kernel):
    kernel = make_kernel(builtins=True)
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    with current.local("cli"):
        session = crud.new_chat("corruption").session_id
        crud.accept(session, MessageRequest(request_id="entry", text="needle example"))
        request = HistorySearchRequest(session_id=session, query="needle")
        assert len(indexed_search(crud, request).items) == 1
        cache = _CACHES[(crud.repository.search_storage_id, crud.repository.principal_id, session)]
        cache.db.execute("DELETE FROM docs")
        cache.db.commit()
        with pytest.raises(HeinzelError, match="rebuilding"):
            indexed_search(crud, request)
        assert len(indexed_search(crud, request).items) == 1
        cache = _CACHES[(crud.repository.search_storage_id, crud.repository.principal_id, session)]
        row = cache.db.execute("SELECT seq,text FROM docs").fetchone()
        cache.db.execute("INSERT INTO terms(terms,rowid,text) VALUES('delete',?,?)", row)
        cache.db.commit()
        with pytest.raises(HeinzelError, match="rebuilding"):
            indexed_search(crud, request)
        assert len(indexed_search(crud, request).items) == 1


def test_indexed_search_is_private_http(make_kernel, tmp_path):
    kernel = make_kernel(builtins=True)
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    with current.local("cli"):
        session = crud.new_chat("http").session_id
        crud.accept(session, MessageRequest(request_id="entry", text="rare example"))
    client = web_client(kernel, tmp_path)
    body = {"request": {"session_id": session, "query": "rare"}}
    assert client.post("/api/heinzel/indexed-conversation-search", json=body).status_code == 401
    response = client.post("/api/heinzel/indexed-conversation-search", headers=AUTH, json=body)
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["items"]] == ["entry"]


def test_expired_matching_body_is_omitted_and_removed_from_disposable_index(make_kernel, tmp_path):
    kernel = make_kernel(builtins=True)
    kernel.bind(CurrentUser, CurrentUser(tmp_path / "identity"))
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    with current.local("cli"):
        session = crud.new_chat("retention").session_id
        crud.accept(session, MessageRequest(request_id="entry", text="retained needle"))
        request = HistorySearchRequest(session_id=session, query="needle")
        assert len(indexed_search(crud, request).items) == 1
        event = next(e for e in crud.repository.control_read(session).events if e.kind == "message.accepted")
        assert event.data["source"] == "control"
        body = (
            current.directory.parent
            / "heinzel-sessions"
            / crud.repository.principal_id
            / "control"
            / session
            / "turns"
            / f"{event.data['body_id']}.jsonl"
        )
        assert body.resolve().is_relative_to(tmp_path.resolve())
        assert not body.is_symlink()
        body.unlink()
        assert indexed_search(crud, request).items == []
        cache = _CACHES[(crud.repository.search_storage_id, crud.repository.principal_id, session)]
        assert cache.db.execute("SELECT count(*) FROM docs").fetchone()[0] == 0


def test_unicode_and_quoted_trigrams_and_nul_fallback(make_kernel):
    kernel = make_kernel(builtins=True)
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    with current.local("cli"):
        session = crud.new_chat("unicode").session_id
        text = 'Quoted a"bc and e\u0301clair with 😀😁😂 and a\x00b'
        crud.accept(session, MessageRequest(request_id="entry", text=text))
        for query in ('a"bc', "e\u0301cl", "😀😁😂"):
            result = indexed_search(crud, HistorySearchRequest(session_id=session, query=query))
            assert result.mode == "trigram"
            assert [item.id for item in result.items] == ["entry"]
        result = indexed_search(crud, HistorySearchRequest(session_id=session, query="a\x00b"))
        assert result.mode == "bounded-scan"
        assert [item.id for item in result.items] == ["entry"]


def test_append_indexes_only_new_body_and_validates_the_hit(make_kernel, monkeypatch):
    kernel = make_kernel(builtins=True)
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    with current.local("cli"):
        session = crud.new_chat("append").session_id
        for number in range(120):
            crud.accept(session, MessageRequest(request_id=f"m{number}", text=f"ordinary {number}"))
        assert indexed_search(crud, HistorySearchRequest(session_id=session, query="ordinary")).mode == "trigram"
        crud.accept(session, MessageRequest(request_id="new", text="very rare appended phrase"))
        original = crud.read_message
        reads = []

        def counted(*args):
            reads.append(1)
            return original(*args)

        monkeypatch.setattr(crud, "read_message", counted)
        result = indexed_search(crud, HistorySearchRequest(session_id=session, query="appended"))
        assert [item.id for item in result.items] == ["new"]
        assert len(reads) == 2  # Index the single append, then verify the one selected hit.


def test_warmed_cache_is_safe_across_authenticated_worker_threads(make_kernel):
    from concurrent.futures import ThreadPoolExecutor

    kernel = make_kernel(builtins=True)
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    with current.local("cli"):
        session = crud.new_chat("threads").session_id
        crud.accept(session, MessageRequest(request_id="entry", text="thread needle"))
        assert len(indexed_search(crud, HistorySearchRequest(session_id=session, query="needle")).items) == 1

    def search():
        with current.local("cli"):
            return indexed_search(crud, HistorySearchRequest(session_id=session, query="needle")).items[0].id

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(lambda _value: search(), range(4))) == ["entry"] * 4


def test_equal_sequence_changed_hash_invalidates_cached_index(make_kernel, monkeypatch):
    kernel = make_kernel(builtins=True)
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    with current.local("cli"):
        session = crud.new_chat("same-sequence").session_id
        crud.accept(session, MessageRequest(request_id="one", text="needle original"))
        request = HistorySearchRequest(session_id=session, query="needle")
        assert len(indexed_search(crud, request).items) == 1
        original = crud.repository.control_indexed_page

        def changed(session_id, **kwargs):
            page, anchor = original(session_id, **kwargs)
            if kwargs["anchor"] is None:
                return page, (anchor[0], "0" * 64)
            return page, anchor

        monkeypatch.setattr(crud.repository, "control_indexed_page", changed)
        with pytest.raises(HeinzelError, match="no longer matches"):
            indexed_search(crud, request)
        assert (crud.repository.search_storage_id, crud.repository.principal_id, session) not in _CACHES


def test_identical_owner_session_in_distinct_stores_does_not_share_cache(make_kernel, tmp_path):
    import shutil

    kernel = make_kernel(builtins=True)
    kernel.bind(CurrentUser, CurrentUser(tmp_path / "first" / "identity"))
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    with current.local("cli"):
        session = crud.new_chat("same-owner").session_id
        crud.accept(session, MessageRequest(request_id="one", text="needle original"))
        request = HistorySearchRequest(session_id=session, query="needle")
        assert len(indexed_search(crud, request).items) == 1
        owner = crud.repository.principal_id
    second_root = tmp_path / "second"
    assert not second_root.exists()
    shutil.copytree(tmp_path / "first", second_root)
    second = CurrentUser(second_root / "identity")
    with second.local("cli"):
        copy = SessionCrud(PersonalSessionRepository(second), crud.costs)
        assert copy.repository.principal_id == owner
        copy.accept(session, MessageRequest(request_id="two", text="needle copy"))
        assert [item.id for item in indexed_search(copy, request).items] == ["one", "two"]
    with current.local("cli"):
        assert [item.id for item in indexed_search(crud, request).items] == ["one"]


def test_unmatched_expired_body_is_purged_without_reading_its_content(make_kernel, tmp_path, monkeypatch):
    kernel = make_kernel(builtins=True)
    kernel.bind(CurrentUser, CurrentUser(tmp_path / "identity"))
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    with current.local("cli"):
        session = crud.new_chat("unmatched-expiry").session_id
        crud.accept(session, MessageRequest(request_id="retained", text="visible needle"))
        crud.accept(session, MessageRequest(request_id="expired", text="unmatched private content"))
        request = HistorySearchRequest(session_id=session, query="needle")
        assert [item.id for item in indexed_search(crud, request).items] == ["retained"]
        event = next(
            e
            for e in crud.repository.control_read(session).events
            if e.kind == "message.accepted" and e.data.get("message_id") == "expired"
        )
        body = (
            current.directory.parent
            / "heinzel-sessions"
            / crud.repository.principal_id
            / "control"
            / session
            / "turns"
            / f"{event.data['body_id']}.jsonl"
        )
        assert body.resolve().is_relative_to(tmp_path.resolve())
        assert not body.is_symlink()
        body.unlink()
        original = crud.read_message
        reads = []

        def counted(*args):
            reads.append(1)
            return original(*args)

        monkeypatch.setattr(crud, "read_message", counted)
        assert [item.id for item in indexed_search(crud, request).items] == ["retained"]
        assert len(reads) == 1
        cache = _CACHES[(crud.repository.search_storage_id, crud.repository.principal_id, session)]
        assert cache.db.execute("PRAGMA secure_delete").fetchone()[0] == 1
        assert cache.db.execute("SELECT v FROM terms_config WHERE k='secure-delete'").fetchone()[0] == 1
        assert cache.db.execute("SELECT count(*) FROM docs").fetchone()[0] == 1
        assert cache.db.execute("SELECT count(*) FROM terms WHERE terms MATCH ?", ('"unmatched"',)).fetchone()[0] == 0


def test_unsupported_secure_delete_refuses_indexed_search_and_closes_cache(make_kernel, monkeypatch):
    import sqlite3

    from heinzel.modules.heinzel import _history_fts as module

    kernel = make_kernel(builtins=True)
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    with current.local("cli"):
        session = crud.new_chat("unsupported").session_id
        crud.accept(session, MessageRequest(request_id="entry", text="private needle"))
        connect = sqlite3.connect
        closed = []

        class Unsupported(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if "'secure-delete'" in sql:
                    msg = "unsupported fixture"
                    raise sqlite3.OperationalError(msg)
                return super().execute(sql, parameters)

            def close(self):
                closed.append(True)
                return super().close()

        monkeypatch.setattr(
            module.sqlite3, "connect", lambda *args, **kwargs: connect(*args, **kwargs, factory=Unsupported)
        )
        with pytest.raises(HeinzelError, match="unavailable"):
            indexed_search(crud, HistorySearchRequest(session_id=session, query="needle"))
        assert closed == [True]


def test_indexed_query_honors_time_bounds_and_rejects_cursor_rebinding(make_kernel):
    from datetime import UTC, datetime

    kernel = make_kernel(builtins=True)
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    with current.local("cli"):
        session = crud.new_chat("time").session_id
        for number in range(55):
            crud.accept(session, MessageRequest(request_id=f"m{number}", text="needle item"))
        first = indexed_search(crud, HistorySearchRequest(session_id=session, query="needle"))
        assert len(first.items) == 50
        assert first.next_cursor
        future = datetime(2100, 1, 1, tzinfo=UTC)
        assert indexed_search(crud, HistorySearchRequest(session_id=session, query="needle", since=future)).items == []
        with pytest.raises(HeinzelError):
            indexed_search(
                crud, HistorySearchRequest(session_id=session, query="needle", since=future, cursor=first.next_cursor)
            )


def test_cursor_cannot_move_between_distinct_owner_store_copies(make_kernel, tmp_path):
    import base64
    import shutil

    kernel = make_kernel(builtins=True)
    kernel.bind(CurrentUser, CurrentUser(tmp_path / "first" / "identity"))
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    with current.local("cli"):
        session = crud.new_chat("copy-cursor").session_id
        for number in range(51):
            crud.accept(session, MessageRequest(request_id=f"m{number}", text="needle record"))
        first = indexed_search(crud, HistorySearchRequest(session_id=session, query="needle"))
        assert first.next_cursor
        decoded = base64.urlsafe_b64decode(first.next_cursor + "=" * (-len(first.next_cursor) % 4))
        assert str(tmp_path).encode() not in decoded
    second_root = tmp_path / "second"
    assert not second_root.exists()
    shutil.copytree(tmp_path / "first", second_root)
    second = CurrentUser(second_root / "identity")
    with second.local("cli"):
        clone = SessionCrud(PersonalSessionRepository(second), crud.costs)
        with pytest.raises(HeinzelError, match="different search"):
            indexed_search(clone, HistorySearchRequest(session_id=session, query="needle", cursor=first.next_cursor))


def test_fresh_search_never_recovers_deleted_body_from_cached_index(make_kernel, tmp_path):
    kernel = make_kernel(builtins=True)
    kernel.bind(CurrentUser, CurrentUser(tmp_path / "identity"))
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    secret = "unique private needle marker"
    with current.local("cli"):
        session = crud.new_chat("retention-boundary").session_id
        for index in range(60):
            text = secret if index == 10 else f"needle ordinary {index}"
            crud.accept(session, MessageRequest(request_id=f"item-{index}", text=text))
        first = indexed_search(crud, HistorySearchRequest(session_id=session, query="needle"))
        assert len(first.items) == 50
        assert any(item.text == secret for item in first.items)
        event = next(
            event
            for event in crud.repository.control_read(session).events
            if event.kind == "message.accepted" and event.data.get("message_id") == "item-10"
        )
        body = (
            current.directory.parent
            / "heinzel-sessions"
            / crud.repository.principal_id
            / "control"
            / session
            / "turns"
            / f"{event.data['body_id']}.jsonl"
        )
        assert body.resolve().is_relative_to(tmp_path.resolve())
        assert not body.is_symlink()
        body.unlink()
        for query in ("needle", "ne", "unique"):
            cursor = None
            collected = []
            for _ in range(10):
                page = indexed_search(crud, HistorySearchRequest(session_id=session, query=query, cursor=cursor))
                assert len(page.items) <= 50
                collected.extend(page.items)
                cursor = page.next_cursor
                if cursor is None:
                    break
            assert cursor is None
            assert all(item.id != "item-10" and secret not in item.text for item in collected)
            assert len(collected) == (0 if query == "unique" else 59)
        cache = _CACHES[(crud.repository.search_storage_id, crud.repository.principal_id, session)]
        assert cache.db.execute("SELECT count(*) FROM docs WHERE seq = ?", (event.seq,)).fetchone()[0] == 0
        reopened = SessionCrud(PersonalSessionRepository(current), crud.costs)
        assert indexed_search(reopened, HistorySearchRequest(session_id=session, query="unique")).items == []
    foreign = CurrentUser(tmp_path / "foreign-owner" / "identity")
    with foreign.local("cli"):
        foreign_crud = SessionCrud(PersonalSessionRepository(foreign), crud.costs)
        with pytest.raises(HeinzelError):
            indexed_search(foreign_crud, HistorySearchRequest(session_id=session, query="unique"))
