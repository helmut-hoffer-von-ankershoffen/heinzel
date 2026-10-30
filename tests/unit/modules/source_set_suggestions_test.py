"""Multi-source conversational suggestions stay inert and owner bound."""

import json

import pytest

from heinzel import CurrentUser, HeinzelError
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository
from heinzel.modules.heinzel._source_set_suggestions import SourceSetSuggestionTools, latest_source_set_suggestion

pytestmark = pytest.mark.unit


def test_bounded_suggestion_replay_and_writer_fence(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = crud.new_chat("new").session_id
        entries = [
            {"capability": "calendar", "method": "events", "fixed_arguments": {"window": "today"}},
            {"capability": "documents", "method": "read", "fixed_arguments": {"id": "fixture"}},
        ]
        args = {"expected_revision": 0, "entries": entries}
        with repo.main_writer(session) as writer:
            tools = SourceSetSuggestionTools(crud, session, "turn", repo.principal_id, writer, lambda: True)
            first = json.loads(tools.dispatch("source-set:propose", args, 1))
            assert json.loads(tools.dispatch("source-set:propose", args, 1)) == first
            saved = latest_source_set_suggestion(crud, session)
            assert [item.model_dump(mode="json") for item in saved.entries] == entries
            with pytest.raises(HeinzelError):
                tools.dispatch("source-set:propose", {**args, "entries": entries[:1]}, 1)
            with pytest.raises(HeinzelError):
                tools.dispatch("source-set:propose", {**args, "entries": entries * 3}, 2)
            with pytest.raises(HeinzelError):
                tools.dispatch("source-set:propose", args, 2)
            denied = SourceSetSuggestionTools(crud, session, "turn", repo.principal_id, writer, lambda: False)
            with pytest.raises(HeinzelError):
                denied.dispatch("source-set:current", {}, 3)
        with pytest.raises(HeinzelError):
            tools.dispatch("source-set:current", {}, 3)
        events = repo.control_read(session).events
        assert not any(
            event.kind in {"connected-set.proposed", "connected-set.approved", "connected-set-input.observed"}
            for event in events
        )
