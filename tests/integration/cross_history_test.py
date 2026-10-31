"""Owner-catalog search exercises real journals, not fabricated index rows."""

import pytest

from heinzel import CurrentUser, HeinzelError
from heinzel.modules.heinzel._cross_history import CrossHistoryRequest, search_owner_history
from heinzel.modules.heinzel._history_fts import _CACHES
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import MessageRequest
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository
from tests.integration.chat_test import AUTH, web_client

pytestmark = pytest.mark.integration


def test_owner_catalog_progress_append_and_reopen(make_kernel, tmp_path, monkeypatch):  # noqa: PLR0915
    kernel = make_kernel(builtins=True)
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    with current.local("cli"):
        first = crud.new_chat("one").session_id
        second = crud.new_chat("two").session_id
        for number in range(65):
            text = "rare needle" if number == 61 else "ordinary"
            crud.accept(first, MessageRequest(request_id=f"a{number}", text=text))
        crud.accept(second, MessageRequest(request_id="b", text="needle in another agent"))
        reads = []
        original = crud.read_message

        def counted(*args):
            reads.append(1)
            return original(*args)

        monkeypatch.setattr(crud, "read_message", counted)
        page = search_owner_history(crud, CrossHistoryRequest(query="needle"))
        assert page.status == "indexing"
        assert page.next_cursor
        assert len(reads) <= 50
        cache = _CACHES[(crud.repository.search_storage_id, crud.repository.principal_id, first)]
        checkpoint = cache.high
        # Cancellation leaves no background job or further index advancement.
        assert cache.high == checkpoint
        cursor = page.next_cursor
        owner_cursor = cursor
        crud.accept(first, MessageRequest(request_id="later", text="needle after anchor"))
        crud.new_chat("after-catalog-anchor")
        reopened = SessionCrud(PersonalSessionRepository(current), crud.costs)
        with pytest.raises(HeinzelError):
            search_owner_history(reopened, CrossHistoryRequest(query="different", cursor=cursor))
        hits = []
        visited = set()
        statuses = {page.status}
        for _ in range(30):
            page = search_owner_history(reopened, CrossHistoryRequest(query="needle", cursor=cursor))
            statuses.add(page.status)
            assert page.completeness == "unknown"
            if page.session_id:
                visited.add(page.session_id)
            hits.extend((page.session_id, item.id) for item in page.items)
            cursor = page.next_cursor
            if cursor is None:
                break
        assert cursor is None
        assert hits == [(first, "a61"), (second, "b")]
        assert visited == {first, second}
        assert "indexing" in statuses
        assert "done" in statuses
    foreign = CurrentUser(tmp_path / "foreign" / "identity")
    with foreign.local("cli"):
        other = SessionCrud(PersonalSessionRepository(foreign), crud.costs)
        other_session = other.new_chat("foreign").session_id
        other.accept(other_session, MessageRequest(request_id="foreign", text="needle"))
        with pytest.raises(HeinzelError):
            search_owner_history(other, CrossHistoryRequest(query="needle", cursor=owner_cursor))
        result = search_owner_history(other, CrossHistoryRequest(query="needle"))
        assert result.session_id == other_session


def test_private_http_and_catalog_loss_refuses_empty(make_kernel, tmp_path):
    kernel = make_kernel(builtins=True)
    kernel.bind(CurrentUser, CurrentUser(tmp_path / "identity"))
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    with current.local("cli"):
        session = crud.new_chat("http").session_id
        crud.accept(session, MessageRequest(request_id="x", text="needle"))
    client = web_client(kernel, tmp_path)
    body = {"request": {"query": "needle"}}
    assert client.post("/api/heinzel/search-agent-conversations", json=body).status_code == 401
    response = client.post("/api/heinzel/search-agent-conversations", headers=AUTH, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["session_id"] == session
    with current.local("cli"):
        catalog = current.directory.parent / "heinzel-sessions" / crud.repository.principal_id / "catalog" / "creations"
        assert catalog.resolve().is_relative_to(tmp_path.resolve())
        assert not catalog.is_symlink()
        import shutil

        shutil.rmtree(catalog)
        with pytest.raises((HeinzelError, FileNotFoundError)):
            search_owner_history(crud, CrossHistoryRequest(query="needle"))


def test_blocked_session_does_not_skip_to_next(make_kernel, monkeypatch):
    kernel = make_kernel(builtins=True)
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    with current.local("cli"):
        first = crud.new_chat("blocked").session_id
        crud.new_chat("later")
        original = crud.repository.control_indexed_page

        def broken(session_id, **kwargs):
            if session_id == first:
                msg = "fixture damaged"
                raise HeinzelError(msg)
            return original(session_id, **kwargs)

        monkeypatch.setattr(crud.repository, "control_indexed_page", broken)
        page = search_owner_history(crud, CrossHistoryRequest(query="needle"))
        assert page.status == "blocked"
        assert page.session_id == first
        assert page.next_cursor
        repeated = search_owner_history(crud, CrossHistoryRequest(query="needle", cursor=page.next_cursor))
        assert repeated == page
        monkeypatch.setattr(crud.repository, "control_indexed_page", original)
        resumed = search_owner_history(crud, CrossHistoryRequest(query="needle", cursor=page.next_cursor))
        assert resumed.status != "blocked"
        assert resumed.session_id == first


def test_fresh_owner_empty_catalog_is_complete_with_unknown_search_coverage(make_kernel):
    kernel = make_kernel(builtins=True)
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    with current.local("cli"):
        page = search_owner_history(crud, CrossHistoryRequest(query="needle"))
        assert page.status == "done"
        assert page.next_cursor is None
        assert not page.items
        assert page.completeness == "unknown"
