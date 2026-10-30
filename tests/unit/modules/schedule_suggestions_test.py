"""Chat schedule suggestions remain owner-bound, revisioned and inert."""

import json

import pytest

from heinzel import CurrentUser, HeinzelError
from heinzel.modules.heinzel._schedule_suggestions import ScheduleSuggestionTools
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

pytestmark = pytest.mark.unit


def test_schedule_suggestions_replay_revision_and_writer_fence(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = crud.new_chat("new").session_id
        terms = {
            "expected_revision": 0,
            "trigger": {"cron": "*/5 * * * *", "timezone": "Europe/Berlin"},
            "suggested_reads": [{"capability": "calendar", "method": "events"}],
        }
        with repo.main_writer(session) as writer:
            tools = ScheduleSuggestionTools(crud, session, "turn", repo.principal_id, writer, lambda: True)
            first = json.loads(tools.dispatch("schedule:propose", terms, 1))
            assert first["status"] == "suggested"
            assert json.loads(tools.dispatch("schedule:propose", terms, 1)) == first
            with pytest.raises(HeinzelError):
                tools.dispatch("schedule:propose", {**terms, "suggested_reads": []}, 1)
            foreign = ScheduleSuggestionTools(crud, session, "turn", "foreign", writer, lambda: True)
            with pytest.raises(HeinzelError):
                foreign.dispatch("schedule:current", {}, 2)
            revoked = ScheduleSuggestionTools(crud, session, "turn", repo.principal_id, writer, lambda: False)
            with pytest.raises(HeinzelError):
                revoked.dispatch("schedule:current", {}, 2)
            assert json.loads(tools.dispatch("schedule:current", {}, 2))["revision"] == 1
            with pytest.raises(HeinzelError):
                tools.dispatch("schedule:propose", terms, 3)
            amended = {**terms, "expected_revision": 1, "trigger": {"cron": "0 9 * * 1", "timezone": "UTC"}}
            assert json.loads(tools.dispatch("schedule:propose", amended, 4))["revision"] == 2
            with pytest.raises(HeinzelError):
                tools.dispatch("schedule:propose", {**terms, "trigger": {"cron": "bad", "timezone": "UTC"}}, 1)
            assert crud.snapshot(session).schedule is None
        with pytest.raises(HeinzelError):
            tools.dispatch("schedule:current", {}, 5)
