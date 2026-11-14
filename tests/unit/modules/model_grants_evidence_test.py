"""A saved model grant authorizes only with its whole proposal and approval trail."""

import pytest

from heinzel import CurrentUser, HeinzelError
from heinzel.modules.heinzel._model_grants import ModelGrant, ModelGrants
from heinzel.modules.heinzel._session_bodies import immutable_body
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

pytestmark = pytest.mark.unit


def _grant(**changes):
    values = {
        "request_id": "one",
        "revision": 1,
        "access": "pydantic-ai",
        "model": "openai:model",
        "requests": 1,
        "total_tokens": 100,
        "output_tokens": 10,
        "timeout_seconds": 10,
        "message_cap_usd": "0.1",
        "session_cap_usd": "1",
        "owner_cap_usd": "2",
    }
    return ModelGrant(**{**values, **changes})


@pytest.fixture
def chat(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        crud = SessionCrud(
            PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2)
        )
        yield crud, crud.new_chat("create").session_id, ModelGrants(crud)


def _approve(grants, session, grant):
    grants.propose(session, grant)
    proof = grants.issue(session, grant.request_id, "test-human-decision")
    return grants.approve(session, grant.request_id, proof)


def _forge_approval(crud, session, grant, key, data):
    with crud.repository.control_writer(session) as writer:
        body = immutable_body(writer, "approved-model", grant.model_dump(mode="json"), control=True)
        writer.append("model.grant-approved", key=key, data=body.model_dump(mode="json") | data)


def test_review_and_applied_follow_the_exact_request_through_approval(chat):
    _crud, session, grants = chat
    grant = _grant(
        allowed_tools={"mod.op:read": "a" * 64},
        allowed_skill_tools=("skill.propose_draft",),
    )
    grants.propose(session, grant)
    assert grants.review(session, "one") == grant
    with pytest.raises(HeinzelError, match="No such model-grant proposal"):
        grants.review(session, "missing")
    with pytest.raises(HeinzelError, match="No such model-grant proposal"):
        grants.approve(session, "missing", "x" * 43)
    assert not grants.applied(session, "one")
    approved = grants.approve(session, "one", grants.issue(session, "one", "test-human-decision"))
    assert approved == grant
    assert grants.applied(session, "one")
    assert not grants.applied(session, "another")
    assert grants.approved(session) == grant
    newer = _grant(request_id="two", revision=2)
    grants.propose(session, newer)
    assert grants.review(session, "two") == newer
    with pytest.raises(HeinzelError, match="reload before reviewing"):
        grants.review(session, "one")


def test_an_approval_without_a_saved_trail_never_authorizes(chat):
    crud, session, grants = chat
    with pytest.raises(HeinzelError, match="no approved model grant"):
        grants.approved(session)
    _forge_approval(crud, session, _grant(), "forged-one", {"proof_hash": "missing"})
    with pytest.raises(HeinzelError, match="lacks its trusted approval evidence"):
        grants.approved(session)
    with pytest.raises(HeinzelError, match="lacks its trusted approval evidence"):
        grants.applied(session, "one")


def test_unreadable_approval_evidence_never_authorizes(chat):
    crud, session, grants = chat
    with crud.repository.control_writer(session) as writer:
        writer.append("approval.issued", key="junk-proof", data={"unexpected": 1})
    _forge_approval(crud, session, _grant(), "forged-one", {"proof_hash": "junk-proof"})
    with pytest.raises(HeinzelError, match="invalid approval evidence"):
        grants.approved(session)


def test_an_approval_for_a_grant_nobody_proposed_never_authorizes(chat):
    crud, session, grants = chat
    _approve(grants, session, _grant())
    proof_hash = next(e.key for e in crud.repository.control_read(session).events if e.kind == "approval.issued")
    _forge_approval(crud, session, _grant(request_id="ghost", revision=2), "forged-ghost", {"proof_hash": proof_hash})
    with pytest.raises(HeinzelError, match="lacks its original proposal"):
        grants.approved(session)


def test_an_approval_that_reuses_another_decision_is_invalid(chat):
    crud, session, grants = chat
    _approve(grants, session, _grant())
    proof_hash = next(e.key for e in crud.repository.control_read(session).events if e.kind == "approval.issued")
    _forge_approval(crud, session, _grant(), "forged-reuse", {"proof_hash": proof_hash})
    with pytest.raises(HeinzelError, match="invalid or revoked approval evidence"):
        grants.approved(session)
