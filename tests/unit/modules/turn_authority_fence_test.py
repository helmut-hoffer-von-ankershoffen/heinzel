"""The shared authority fence follows the actual owner-scoped main writer lease."""

import pytest

from heinzel import CurrentUser, HeinzelError
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository
from heinzel.modules.heinzel._turn_authority_fence import TurnAuthorityFence

pytestmark = pytest.mark.unit


def test_fence_binds_exact_owner_session_and_expires_with_writer(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=1))
        first = crud.new_chat("first").session_id
        second = crud.new_chat("second").session_id
        with repo.main_writer(first) as writer:
            fence = TurnAuthorityFence(repo, first, writer)
            fence.bind(repo, first)
            with pytest.raises(HeinzelError):
                fence.bind(repo, second)
            with pytest.raises(HeinzelError):
                fence.bind(PersonalSessionRepository(current), first)
        with pytest.raises(HeinzelError):
            fence.check()
