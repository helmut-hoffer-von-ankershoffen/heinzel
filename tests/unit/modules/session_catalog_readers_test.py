"""Every catalog reader tolerates deletion records before anything writes them (ADR 0148 §8, §9)."""

from datetime import UTC, datetime

import pytest

from heinzel import CurrentUser, HeinzelError, SessionStore
from heinzel.modules.heinzel._cross_history import CrossHistoryRequest, search_owner_history
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

pytestmark = pytest.mark.unit

AT = datetime(2026, 11, 13, tzinfo=UTC)


@pytest.fixture
def crud(tmp_path):
    current = CurrentUser(tmp_path / "state" / "identity")
    with current.local("cli"):
        yield SessionCrud(
            PersonalSessionRepository(current, clock=lambda: AT),
            lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2),
            clock=lambda: AT,
        )


def _catalog(crud: SessionCrud) -> SessionStore:
    repository = crud.repository
    assert isinstance(repository, PersonalSessionRepository)
    root = repository.current.directory.parent / "heinzel-sessions" / repository.principal_id / "catalog"
    return SessionStore(root, clock=lambda: AT)


def _record(crud: SessionCrud, kind: str, session: str) -> None:
    with _catalog(crud).writer("creations", wait=True) as writer:
        writer.append(kind, key=f"{kind}:{session}", data={"session_id": session})


@pytest.mark.parametrize("kind", ["creation.deleting", "creation.deleted"])
def test_a_session_with_a_deletion_record_is_gone_for_every_reader(crud, kind):
    kept = crud.new_chat("create-keep", "Keep", initial_message="Blue door").session_id
    gone = crud.new_chat("create-gone", "Gone", initial_message="Blue door").session_id
    _record(crud, kind, gone)
    repository = crud.repository

    assert repository.reserved_session_ids() == (kept,)
    assert [head.session for head in repository.heads()] == [kept]
    assert [item.session_id for item in crud.listing().sessions] == [kept]
    with pytest.raises(HeinzelError, match="No such personal Heinzel"):
        crud.snapshot(gone)
    with pytest.raises(HeinzelError, match="No such personal Heinzel"), repository.control_writer(gone):
        pytest.fail("a gone session takes no write")
    # Its folders may still exist mid-deletion; the store check tolerates them instead of reporting tampering.
    repository.verify_reserved_session_stores(repository.reserved_session_ids())
    with pytest.raises(HeinzelError, match="was deleted"):
        crud.new_chat("create-gone", "Gone", initial_message="Blue door")

    seen, cursor = [], None
    for _ in range(20):
        page = search_owner_history(crud, CrossHistoryRequest(query="Blue door", cursor=cursor))
        assert page.status != "blocked"
        seen.append(page.session_id)
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    assert kept in seen
    assert gone not in seen


def test_a_forgotten_record_is_skipped_by_cross_heinzel_search(crud):
    kept = crud.new_chat("create-keep", "Keep", initial_message="Blue door").session_id
    gone = crud.new_chat("create-gone", "Gone").session_id
    _record(crud, "creation.deleting", gone)
    _record(crud, "creation.forgotten", gone)
    pages = []
    cursor = None
    for _ in range(20):
        page = search_owner_history(crud, CrossHistoryRequest(query="Blue door", cursor=cursor))
        pages.append(page)
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    assert pages[-1].status == "done"
    assert kept in {page.session_id for page in pages}
