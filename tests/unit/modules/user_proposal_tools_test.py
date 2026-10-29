"""Generic private proposal tools never activate or grant access."""

import json

import pytest

from heinzel import CallIdentity, CurrentUser, HeinzelError, Principal
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository
from heinzel.modules.heinzel._user_proposal_tools import UserProposalTools

pytestmark = pytest.mark.unit


def test_proposal_tools_scope_to_owner_and_leave_profile_schedule_and_files_inert(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = crud.new_chat("new", name="Original").session_id
        owner = current.principal().id
        link = "https://docs.google.com/document/d/abcdefghijk123/edit"
        with repo.main_writer(session) as writer:
            tools = UserProposalTools(crud, session, "turn", owner, writer, f"Please link {link}", lambda: True)
            contract = json.loads(
                tools.dispatch(
                    "agent:propose",
                    {
                        "expected_revision": 0,
                        "contract": "Review the supplied document",
                        "max_usd": 0.2,
                        "timeout_seconds": 30,
                    },
                    1,
                )
            )
            assert contract["status"] == "proposed"
            assert crud.latest_scheduled_agent_proposal(session).contract == "Review the supplied document"
            revised = json.loads(
                tools.dispatch(
                    "agent:propose",
                    {
                        "expected_revision": 1,
                        "contract": "Revise the document",
                        "max_usd": 0.2,
                        "timeout_seconds": 30,
                    },
                    2,
                )
            )
            assert revised["revision"] == 2
            with pytest.raises(HeinzelError):
                tools.dispatch(
                    "agent:propose",
                    {
                        "expected_revision": 1,
                        "contract": "stale",
                        "max_usd": 0.2,
                        "timeout_seconds": 30,
                    },
                    3,
                )
            proposal = json.loads(
                tools.dispatch(
                    "profile:propose",
                    {
                        "expected_revision": 1,
                        "name": "Proposed",
                        "appearance": {"age": "grown", "build": "round"},
                    },
                    4,
                )
            )
            assert proposal["status"] == "proposed"
            assert crud.latest_profile_proposal(session).name == "Proposed"
            assert crud.snapshot(session).name == "Original"
            skill = json.loads(
                tools.dispatch(
                    "skill:propose-draft",
                    {
                        "name": "helpful-skill",
                        "requested_scope": "personal",
                        "scope_ref": None,
                        "files": {"SKILL.md": "private instructions", "scripts/check.py": "print('private')"},
                    },
                    5,
                )
            )
            assert "private" not in json.dumps(skill)
            assert crud.skill_draft(session, skill["request_id"]).files["scripts/check.py"] == "print('private')"
            assert (
                json.loads(tools.dispatch("artifact:associate", {"kind": "document", "ref": link}, 6))["access"]
                == "unverified"
            )
            with pytest.raises(HeinzelError):
                tools.dispatch(
                    "artifact:associate",
                    {"kind": "document", "ref": link.replace("abcdefghijk123", "otherid123456")},
                    7,
                )
            ambiguous = UserProposalTools(crud, session, "turn", owner, writer, f"Look at {link}?other=1", lambda: True)
            with pytest.raises(HeinzelError):
                ambiguous.dispatch("artifact:associate", {"kind": "document", "ref": link}, 8)
            assert len(crud.personal_artifacts(session).artifacts) == 1
            assert crud.snapshot(session).schedule is None
            stranger = CallIdentity(Principal("u-" + "a" * 32), "api-bearer", "other", "private", frozenset())
            with current.using(stranger), pytest.raises(HeinzelError):
                tools.dispatch("profile:current", {}, 10)
        with pytest.raises(HeinzelError):
            tools.dispatch("profile:current", {}, 10)
