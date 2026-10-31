"""Owner-bound, bounded history search keeps a stable prefix across appends."""

import pathlib
from datetime import UTC, datetime, timedelta

import pytest

from heinzel import CurrentUser, HeinzelError
from heinzel.modules.heinzel._history_search import HistorySearchRequest, search_history
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import MessageRequest
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

pytestmark = pytest.mark.integration


def test_search_pages_are_owner_and_query_bound_and_exclude_later_appends(make_kernel, tmp_path):
    kernel = make_kernel(builtins=True)
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    with current.local("cli"):
        session = crud.new_chat("new").session_id
        other = crud.new_chat("other").session_id
        for index in range(55):
            crud.accept(session, MessageRequest(request_id=f"m{index}", text=f"Needle {index}"))
        first = search_history(crud, HistorySearchRequest(session_id=session, query="needle"))
        assert first.scanned == 49
        assert len(first.items) == 49
        assert first.next_cursor
        crud.accept(session, MessageRequest(request_id="late", text="Needle appended"))
        second_request = HistorySearchRequest(session_id=session, query="needle", cursor=first.next_cursor)
        second = search_history(crud, second_request)
        assert second.scanned == 6
        assert len(second.items) == 6
        assert second.next_cursor is None
        assert search_history(crud, second_request) == second
        reopened = SessionCrud(PersonalSessionRepository(current), crud.costs)
        assert search_history(reopened, second_request) == second
        assert [item.id for item in first.items + second.items] == [f"m{index}" for index in range(55)]
        for bad in (
            HistorySearchRequest(session_id=session, query="other", cursor=first.next_cursor),
            HistorySearchRequest(session_id=other, query="needle", cursor=first.next_cursor),
            HistorySearchRequest(session_id=session, query="needle", cursor="not-a-cursor"),
        ):
            with pytest.raises(HeinzelError):
                search_history(crud, bad)
        when = first.items[0].at
        timed = search_history(
            crud,
            HistorySearchRequest(
                session_id=session, since=when - timedelta(seconds=1), until=when + timedelta(seconds=1)
            ),
        )
        assert timed.scanned == 49
        assert timed.next_cursor
        empty = search_history(crud, HistorySearchRequest(session_id=session, query="not-present"))
        assert empty.items == []
        assert empty.scanned == 49
        assert empty.next_cursor
        tail = search_history(
            crud, HistorySearchRequest(session_id=session, query="not-present", cursor=empty.next_cursor)
        )
        assert tail.items == []
        assert tail.scanned == 7
        assert tail.next_cursor is None
        future = search_history(crud, HistorySearchRequest(session_id=session, since=datetime(2100, 1, 1, tzinfo=UTC)))
        assert future.items == []
        assert future.next_cursor
        with pytest.raises(HeinzelError):
            search_history(
                crud,
                HistorySearchRequest(
                    session_id=session, since=datetime(2030, 1, 1, tzinfo=UTC), until=datetime(2020, 1, 1, tzinfo=UTC)
                ),
            )
    foreign = CurrentUser(tmp_path / "foreign" / "identity")
    with foreign.local("cli"):
        other_crud = SessionCrud(PersonalSessionRepository(foreign), crud.costs)
        with pytest.raises(HeinzelError):
            search_history(
                other_crud, HistorySearchRequest(session_id=session, query="needle", cursor=first.next_cursor)
            )


def test_registered_private_http_search(make_kernel, tmp_path):
    from tests.integration.chat_test import AUTH, web_client

    kernel = make_kernel(builtins=True)
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    with current.local("cli"):
        session = crud.new_chat("http").session_id
        crud.accept(session, MessageRequest(request_id="entry", text="A searchable item"))
    client = web_client(kernel, tmp_path)
    response = client.post(
        "/api/heinzel/search-conversation",
        headers=AUTH,
        json={"request": {"session_id": session, "query": "searchable"}},
    )
    assert response.status_code == 200, response.text
    assert [item["text"] for item in response.json()["items"]] == ["A searchable item"]
    assert (
        client.post(
            "/api/heinzel/search-conversation", json={"request": {"session_id": session, "query": "searchable"}}
        ).status_code
        == 401
    )


def test_indexed_search_avoids_spine_reads_and_recovers_database(make_kernel, tmp_path, monkeypatch):
    import os

    import heinzel.kernel._session_store as store

    kernel = make_kernel(builtins=True)
    kernel.bind(CurrentUser, CurrentUser(tmp_path / "identity"))
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    with current.local("cli"):
        session = crud.new_chat("indexed").session_id
        crud.accept(session, MessageRequest(request_id="first", text="indexed item"))
        folder = current.directory.parent / "heinzel-sessions" / crud.repository.principal_id / "control" / session
        assert folder.resolve().is_relative_to(tmp_path.resolve())
        inode = folder.stat().st_ino
        original = store._read_spine
        scans = []

        def count(folder_fd):
            if os.fstat(folder_fd).st_ino == inode:
                scans.append(True)
            return original(folder_fd)

        monkeypatch.setattr(store, "_read_spine", count)
        request = HistorySearchRequest(session_id=session, query="indexed")
        assert len(search_history(crud, request).items) == 1
        assert scans == []
        crud.accept(session, MessageRequest(request_id="later", text="indexed next"))
        scans.clear()
        assert len(search_history(crud, request).items) == 2
        assert scans == []
        database = folder.parent / "index.sqlite"
        assert database.resolve().is_relative_to(tmp_path.resolve())
        assert not database.is_symlink()
        database.write_bytes(b"broken private derived index")
        assert len(search_history(crud, request).items) == 2
        assert scans  # Corrupt cache is rebuilt from the authoritative spine.


def test_truncated_source_and_missing_indexed_event_fail_closed(make_kernel, tmp_path):
    import sqlite3
    from contextlib import closing

    kernel = make_kernel(builtins=True)
    kernel.bind(CurrentUser, CurrentUser(tmp_path / "identity"))
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    with current.local("cli"):
        session = crud.new_chat("damage").session_id
        for index in range(3):
            crud.accept(session, MessageRequest(request_id=f"m{index}", text="private"))
        folder = current.directory.parent / "heinzel-sessions" / crud.repository.principal_id / "control" / session
        assert folder.resolve().is_relative_to(tmp_path.resolve())
        request = HistorySearchRequest(session_id=session, query="private")
        assert len(search_history(crud, request).items) == 3
        database = folder.parent / "index.sqlite"
        with closing(sqlite3.connect(database)) as index, index:
            index.execute("DELETE FROM events WHERE session = ? AND seq = 2", (session,))
        with pytest.raises(HeinzelError):
            search_history(crud, request)
        crud.repository._store("control").reindex()
        assert len(search_history(crud, request).items) == 3
        spine = folder / "session.jsonl"
        assert spine.resolve().is_relative_to(tmp_path.resolve())
        assert not spine.is_symlink()
        original = spine.read_bytes()
        spine.write_bytes(original[: original.index(b"\n") + 1])
        with pytest.raises(HeinzelError):
            search_history(crud, request)


def test_replaced_truncated_spine_is_not_adopted(make_kernel, tmp_path):

    kernel = make_kernel(builtins=True)
    kernel.bind(CurrentUser, CurrentUser(tmp_path / "identity"))
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    with current.local("cli"):
        session = crud.new_chat("replace").session_id
        crud.accept(session, MessageRequest(request_id="message", text="retained"))
        request = HistorySearchRequest(session_id=session, query="retained")
        assert len(search_history(crud, request).items) == 1
        folder = current.directory.parent / "heinzel-sessions" / crud.repository.principal_id / "control" / session
        assert folder.resolve().is_relative_to(tmp_path.resolve())
        spine = folder / "session.jsonl"
        replacement = folder / "temporary-fixture"
        assert not replacement.exists()
        assert not replacement.is_symlink()
        assert not spine.is_symlink()
        replacement.write_bytes(spine.read_bytes().splitlines(keepends=True)[0])
        pathlib.Path(replacement).replace(spine)
        with pytest.raises(HeinzelError):
            search_history(crud, request)
        with pytest.raises(HeinzelError):
            search_history(crud, request)


def test_indexed_pages_preserve_hashes_of_noncanonical_valid_spines(tmp_path):
    import hashlib
    import json
    import os

    from heinzel import SessionEvent, SessionStore

    store = SessionStore(tmp_path / "store")
    with store.writer("session"):
        pass
    spine = tmp_path / "store" / "session" / "session.jsonl"
    assert spine.parent.resolve().is_relative_to(tmp_path.resolve())
    assert not spine.exists()
    assert not spine.is_symlink()
    first = SessionEvent(seq=1, ts=datetime(2026, 1, 1, tzinfo=UTC), prev=None, kind="custom.first", data={})
    raw_first = (json.dumps(first.model_dump(mode="json"), ensure_ascii=False) + "\n").encode()
    first_hash = hashlib.sha256(raw_first).hexdigest()
    second = SessionEvent(seq=2, ts=datetime(2026, 1, 1, tzinfo=UTC), prev=first_hash, kind="custom.second", data={})
    raw_second = (json.dumps(second.model_dump(mode="json"), ensure_ascii=False) + "\n").encode()
    descriptor = os.open(spine, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(raw_first + raw_second)
    page, anchor = store.indexed_page("session", position=(0, None), anchor=None, limit=1)
    assert page.hashes == [first_hash]
    assert anchor == (2, hashlib.sha256(raw_second).hexdigest())
    tail, _ = store.indexed_page("session", position=(1, first_hash), anchor=anchor, limit=1)
    assert tail.events[0].kind == "custom.second"
    store.reindex()
    assert store.indexed_page("session", position=(0, None), anchor=anchor, limit=2)[0].hashes == [
        first_hash,
        anchor[1],
    ]


def test_old_index_migrates_exact_digests_from_authoritative_spine(make_kernel, tmp_path):
    import sqlite3
    from contextlib import closing

    kernel = make_kernel(builtins=True)
    kernel.bind(CurrentUser, CurrentUser(tmp_path / "identity"))
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    with current.local("cli"):
        session = crud.new_chat("migrate").session_id
        crud.accept(session, MessageRequest(request_id="message", text="migration item"))
        root = current.directory.parent / "heinzel-sessions" / crud.repository.principal_id / "control"
        assert root.resolve().is_relative_to(tmp_path.resolve())
        database = root / "index.sqlite"
        assert not database.is_symlink()
        with closing(sqlite3.connect(database)) as index, index:
            index.execute("DROP TABLE event_lines")
        page = search_history(crud, HistorySearchRequest(session_id=session, query="migration"))
        assert [item.text for item in page.items] == ["migration item"]
        with closing(sqlite3.connect(database)) as index, index:
            index.execute("DELETE FROM digest_meta")
            index.execute("DELETE FROM event_lines")
        resumed = search_history(crud, HistorySearchRequest(session_id=session, query="migration"))
        assert [item.text for item in resumed.items] == ["migration item"]


def test_indexed_event_corruption_cannot_fabricate_message(make_kernel, tmp_path):
    import sqlite3
    from contextlib import closing

    kernel = make_kernel(builtins=True)
    kernel.bind(CurrentUser, CurrentUser(tmp_path / "identity"))
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    with current.local("cli"):
        session = crud.new_chat("mutated-index").session_id
        crud.accept(session, MessageRequest(request_id="actual", text="verified original"))
        request = HistorySearchRequest(session_id=session, query="original")
        assert len(search_history(crud, request).items) == 1
        root = current.directory.parent / "heinzel-sessions" / crud.repository.principal_id / "control"
        assert root.resolve().is_relative_to(tmp_path.resolve())
        database = root / "index.sqlite"
        assert not database.is_symlink()
        with closing(sqlite3.connect(database)) as index, index:
            index.execute("UPDATE events SET event = '{}' WHERE session = ? AND seq = 2", (session,))
        assert [item.text for item in search_history(crud, request).items] == ["verified original"]
        with closing(sqlite3.connect(database)) as index, index:
            index.execute("UPDATE event_lines SET raw = ? WHERE session = ? AND seq = 2", (b"{}\n", session))
        with pytest.raises(HeinzelError):
            search_history(crud, request)


def test_changed_page_boundary_digest_is_rejected_before_return(make_kernel, tmp_path):
    import hashlib
    import json
    import sqlite3
    from contextlib import closing

    kernel = make_kernel(builtins=True)
    kernel.bind(CurrentUser, CurrentUser(tmp_path / "identity"))
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    with current.local("cli"):
        session = crud.new_chat("boundary").session_id
        for number in range(55):
            crud.accept(session, MessageRequest(request_id=f"m{number}", text="bounded item"))
        root = current.directory.parent / "heinzel-sessions" / crud.repository.principal_id / "control"
        assert root.resolve().is_relative_to(tmp_path.resolve())
        database = root / "index.sqlite"
        assert not database.is_symlink()
        with closing(sqlite3.connect(database)) as index, index:
            raw = index.execute("SELECT raw FROM event_lines WHERE session = ? AND seq = 50", (session,)).fetchone()[0]
            event = json.loads(raw)
            event["kind"] = "custom.changed"
            changed = (json.dumps(event) + "\n").encode()
            index.execute(
                "UPDATE event_lines SET raw = ?, digest = ? WHERE session = ? AND seq = 50",
                (changed, hashlib.sha256(changed).hexdigest(), session),
            )
        with pytest.raises(HeinzelError):
            search_history(crud, HistorySearchRequest(session_id=session, query="bounded"))


def test_old_search_jump_uses_owner_session_and_verified_prefix(make_kernel, tmp_path):
    import base64
    import json

    from heinzel.modules.heinzel._history_fts import indexed_search

    kernel = make_kernel(builtins=True)
    kernel.bind(CurrentUser, CurrentUser(tmp_path / "identity"))
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    with current.local("cli"):
        session = crud.new_chat("long").session_id
        other = crud.new_chat("other").session_id
        for index in range(160):
            text = "Ancient distinctive needle" if index == 123 else f"Ordinary message {index}"
            crud.accept(session, MessageRequest(request_id=f"message-{index}", text=text))
        first_page = crud.snapshot(session)
        assert len(first_page.messages) == 50
        assert all(item.id != "message-123" for item in first_page.messages)
        page = indexed_search(crud, HistorySearchRequest(session_id=session, query="distinctive needle"))
        assert [item.id for item in page.items] == ["message-123"]
        cursor = page.jump_cursors[0]
        landed = crud.snapshot(session, cursor=cursor)
        assert len(landed.messages) <= 50
        assert landed.messages[0].id == "message-123"
        crud.accept(session, MessageRequest(request_id="later", text="New message"))
        assert crud.snapshot(session, cursor=cursor).messages[0].id == "message-123"
        with pytest.raises(HeinzelError):
            crud.snapshot(other, cursor=cursor)
        decoded = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        for field, bad in (("high_hash", "0" * 64), ("owner", "u-" + "f" * 32), ("storage", "f" * 64)):
            altered = {**decoded, field: bad}
            forged = base64.urlsafe_b64encode(json.dumps(altered).encode()).decode().rstrip("=")
            with pytest.raises(HeinzelError):
                crud.snapshot(session, cursor=forged)
        target = next(
            event
            for event in crud.repository.control_read(session).events
            if event.kind == "message.accepted" and event.data.get("message_id") == "message-123"
        )
        body = (
            current.directory.parent
            / "heinzel-sessions"
            / crud.repository.principal_id
            / "control"
            / session
            / "turns"
            / f"{target.data['body_id']}.jsonl"
        )
        assert body.resolve().is_relative_to(tmp_path.resolve())
        assert not body.is_symlink()
        body.unlink()
        with pytest.raises(HeinzelError, match="no longer retained"):
            crud.snapshot(session, cursor=cursor)
    foreign = CurrentUser(tmp_path / "foreign" / "identity")
    with foreign.local("cli"):
        foreign_crud = SessionCrud(PersonalSessionRepository(foreign), crud.costs)
        with pytest.raises(HeinzelError):
            foreign_crud.snapshot(session, cursor=cursor)


def test_recent_model_context_reads_only_tail_without_deleting_history(make_kernel, tmp_path, monkeypatch):
    kernel = make_kernel(builtins=True)
    kernel.bind(CurrentUser, CurrentUser(tmp_path / "identity"))
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    with current.local("cli"):
        session = crud.new_chat("retained").session_id
        for index in range(160):
            crud.accept(session, MessageRequest(request_id=f"entry-{index}", text=f"Message {index}"))
        original = crud.read_message
        reads = []

        def counted(session_id, event):
            reads.append(event.seq)
            return original(session_id, event)

        monkeypatch.setattr(crud, "read_message", counted)
        recent = crud.recent_history(session)
        assert len(reads) == len(recent) == 20
        assert [message.id for message in recent] == [f"entry-{index}" for index in range(140, 160)]
        assert (
            len([event for event in crud.repository.control_read(session).events if event.kind == "message.accepted"])
            == 160
        )
        assert crud.snapshot(session).messages[0].id == "entry-0"
