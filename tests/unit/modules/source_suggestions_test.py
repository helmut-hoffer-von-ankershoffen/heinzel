"""Source suggestions are owner-bound, inert, revisioned and exactly replayed."""

import json

import pytest

from heinzel import CallIdentity, CurrentUser, HeinzelError, Principal
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository
from heinzel.modules.heinzel._source_suggestions import SourceSuggestionTools, latest_source_suggestion

pytestmark = pytest.mark.unit


def test_source_suggestion_inert_owner_revision_and_exact_replay(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = crud.new_chat("new").session_id
        terms = {
            "expected_revision": 0,
            "capability": "documents",
            "method": "read",
            "fixed_arguments": {"ref": "https://example.invalid/a"},
        }
        with repo.main_writer(session) as writer:
            tools = SourceSuggestionTools(crud, session, "turn", repo.principal_id, writer, lambda: True)
            first = json.loads(tools.dispatch("source:propose", terms, 1))
            assert first["status"] == "suggested"
            assert json.loads(tools.dispatch("source:propose", terms, 1)) == first
            assert latest_source_suggestion(crud, session).fixed_arguments == terms["fixed_arguments"]
            with pytest.raises(HeinzelError):
                tools.dispatch("source:propose", {**terms, "fixed_arguments": {"ref": "changed"}}, 1)
            with pytest.raises(HeinzelError):
                tools.dispatch("source:propose", terms, 2)
            with pytest.raises(HeinzelError):
                tools.dispatch(
                    "source:propose", {**terms, "expected_revision": 1, "fixed_arguments": {"x": float("nan")}}, 3
                )
            with pytest.raises(HeinzelError):
                tools.dispatch(
                    "source:propose", {**terms, "expected_revision": 1, "fixed_arguments": {"ref": "x" * 9000}}, 3
                )
            revoked = SourceSuggestionTools(crud, session, "turn", repo.principal_id, writer, lambda: False)
            with pytest.raises(HeinzelError):
                revoked.dispatch("source:current", {}, 4)
            foreign = SourceSuggestionTools(crud, session, "turn", "foreign", writer, lambda: True)
            with pytest.raises(HeinzelError):
                foreign.dispatch("source:current", {}, 4)
            assert json.loads(tools.dispatch("source:propose", {**terms, "expected_revision": 1}, 5))["revision"] == 2
        with pytest.raises(HeinzelError):
            tools.dispatch("source:current", {}, 6)
        stranger = CallIdentity(
            Principal("u-" + "a" * 32), "api-bearer", "other", "private", frozenset({"private:read"})
        )
        with current.using(stranger), pytest.raises(HeinzelError):
            latest_source_suggestion(crud, session)
