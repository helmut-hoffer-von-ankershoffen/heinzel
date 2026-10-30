"""A trusted one-use proof approves an exact set without performing a provider read."""

import pytest

from heinzel import CallIdentity, HeinzelError, Principal
from heinzel.kernel._approval import ApprovalConfirmation
from heinzel.modules.heinzel._connected_read_set_approvals import ConnectedReadSetApprovals
from heinzel.modules.heinzel._connected_read_set_reviews import ConnectedReadSetReviews
from heinzel.modules.heinzel._connected_read_sets import ConnectedReadSets
from tests.unit.modules.connected_read_set_review_test import _terms

pytestmark = pytest.mark.unit
pytest_plugins = ("tests.unit.modules.connected_grants_test",)


def _prepare(connected):
    current, crud, session, grants, fixed, state = connected
    sets = ConnectedReadSets(crud, current)
    proposal = sets.propose(session, "set", (_terms(current, session, grants, fixed, state),))
    reviews = ConnectedReadSetReviews(sets, grants)
    review = reviews.prepare(session, "review", "set", proposal.revision)
    return current, crud, session, grants, state, sets, reviews, review, ConnectedReadSetApprovals(reviews)


def test_trusted_exact_confirmation_consumes_one_proof_and_revokes_whole_set(connected):
    _, crud, session, grants, state, _, _, review, approvals = _prepare(connected)
    proof = approvals.issue(session, "review", review.revision, "human-confirmation")
    competing = approvals.issue(session, "review", review.revision, "human-confirmation")
    confirmation = ApprovalConfirmation()
    payload = review.model_dump(mode="json")
    args = {"session_id": session, "request_id": "review", "review": payload, "approval_proof": proof}
    with pytest.raises(HeinzelError, match="Approval is invalid"):
        approvals.approve(session, "review", payload, proof, confirmation)
    with confirmation.using("heinzel", "approve-connected-read-set", args):
        assert approvals.approve(session, "review", payload, proof, confirmation) == review
        with pytest.raises(HeinzelError, match="already consumed"):
            approvals.approve(session, "review", payload, proof, confirmation)
    other_args = {**args, "approval_proof": competing}
    with confirmation.using("heinzel", "approve-connected-read-set", other_args), pytest.raises(HeinzelError):
        approvals.approve(session, "review", payload, competing, confirmation)
    assert approvals.approved(session) == review
    assert grants.tool_schema(session) is None
    assert state["reads"] == 0
    assert sum(event.kind == "connected-set.approved" for event in crud.repository.control_read(session).events) == 1
    approvals.revoke(session, "review")
    with pytest.raises(HeinzelError, match="revoked"):
        approvals.approved(session)
    with pytest.raises(HeinzelError, match="already approved"):
        approvals.issue(session, "review", review.revision, "human-confirmation")


def test_changed_account_or_reviewed_terms_cannot_consume_proof(connected):
    _, crud, session, _, state, _, _, review, approvals = _prepare(connected)
    proof = approvals.issue(session, "review", review.revision, "human-confirmation")
    confirmation = ApprovalConfirmation()
    payload = review.model_dump(mode="json")
    args = {"session_id": session, "request_id": "review", "review": payload, "approval_proof": proof}
    state["account"] = "different-account"
    with (
        confirmation.using("heinzel", "approve-connected-read-set", args),
        pytest.raises(HeinzelError, match="source or account changed"),
    ):
        approvals.approve(session, "review", payload, proof, confirmation)
    state["account"] = "account-a"
    changed = review.model_copy(deep=True).model_dump(mode="json")
    changed["proposal"]["entries"][0]["handle"] = "read-" + "0" * 32
    altered_args = {**args, "review": changed}
    with (
        confirmation.using("heinzel", "approve-connected-read-set", altered_args),
        pytest.raises(HeinzelError, match="reviewed read set changed"),
    ):
        approvals.approve(session, "review", changed, proof, confirmation)
    assert state["reads"] == 0
    assert not any(event.kind == "connected-set.approved" for event in crud.repository.control_read(session).events)


def test_foreign_owner_and_session_cannot_issue_or_consume(connected):
    current, crud, session, _, state, _, _, review, approvals = _prepare(connected)
    proof = approvals.issue(session, "review", review.revision, "human-confirmation")
    confirmation = ApprovalConfirmation()
    payload = review.model_dump(mode="json")
    other = crud.new_chat("other").session_id
    other_args = {"session_id": other, "request_id": "review", "review": payload, "approval_proof": proof}
    with confirmation.using("heinzel", "approve-connected-read-set", other_args), pytest.raises(HeinzelError):
        approvals.approve(other, "review", payload, proof, confirmation)
    with current.local("daemon"), pytest.raises(HeinzelError, match="authenticated owner"):
        approvals.issue(session, "review", review.revision, "daemon")
    foreign = CallIdentity(Principal("u-" + "b" * 32), "api-bearer", "foreign", "private", frozenset({"private:write"}))
    with current.using(foreign), pytest.raises(HeinzelError, match="authenticated owner"):
        approvals.issue(session, "review", review.revision, "foreign")
    assert state["reads"] == 0
    with pytest.raises(HeinzelError, match="No connected read set"):
        approvals.approved(session)


def test_expired_proof_and_newer_set_cannot_be_consumed(connected, monkeypatch):
    from datetime import timedelta

    current, crud, session, grants, state, sets, _, review, approvals = _prepare(connected)
    proof = approvals.issue(session, "review", review.revision, "human-confirmation")
    confirmation = ApprovalConfirmation()
    payload = review.model_dump(mode="json")
    args = {"session_id": session, "request_id": "review", "review": payload, "approval_proof": proof}
    clock = crud.clock
    monkeypatch.setattr(crud, "clock", lambda: clock() + timedelta(minutes=6))
    with confirmation.using("heinzel", "approve-connected-read-set", args), pytest.raises(HeinzelError):
        approvals.approve(session, "review", payload, proof, confirmation)
    monkeypatch.setattr(crud, "clock", clock)
    fixed = review.proposal.entries[0].terms.review.fixed_arguments
    sets.propose(session, "new-set", (_terms(current, session, grants, fixed, state),))
    with confirmation.using("heinzel", "approve-connected-read-set", args), pytest.raises(HeinzelError):
        approvals.approve(session, "review", payload, proof, confirmation)
    assert not any(event.kind == "connected-set.approved" for event in crud.repository.control_read(session).events)
    assert state["reads"] == 0


def test_two_concurrent_confirmations_do_not_approve_twice(connected):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    current, crud, session, _, state, _, _, review, approvals = _prepare(connected)
    proofs = [approvals.issue(session, "review", review.revision, "human-confirmation") for _ in range(2)]
    barrier = Barrier(2)
    payload = review.model_dump(mode="json")

    def run(proof):
        confirmation = ApprovalConfirmation()
        args = {"session_id": session, "request_id": "review", "review": payload, "approval_proof": proof}
        with current.local("cli"), confirmation.using("heinzel", "approve-connected-read-set", args):
            barrier.wait(timeout=3)
            try:
                approvals.approve(session, "review", payload, proof, confirmation)
            except HeinzelError:
                return False
            return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run, proofs)) == [False, True]
    assert sum(e.kind == "connected-set.approved" for e in crud.repository.control_read(session).events) == 1
    assert state["reads"] == 0


def test_approved_set_reopens_without_creating_legacy_grant(connected):
    from heinzel.modules.heinzel._connected_grants import ConnectedReadGrants
    from heinzel.modules.heinzel._session_crud import SessionCrud
    from heinzel.modules.heinzel._session_models import SessionCosts
    from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

    current, _, session, grants, state, _, _, review, approvals = _prepare(connected)
    proof = approvals.issue(session, "review", review.revision, "human-confirmation")
    payload = review.model_dump(mode="json")
    confirmation = ApprovalConfirmation()
    args = {"session_id": session, "request_id": "review", "review": payload, "approval_proof": proof}
    with confirmation.using("heinzel", "approve-connected-read-set", args):
        approvals.approve(session, "review", payload, proof, confirmation)
    reopened = SessionCrud(
        PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2)
    )
    reopened_grants = ConnectedReadGrants(reopened, current, grants.kernel)
    reopened_sets = ConnectedReadSets(reopened, current)
    reopened_reviews = ConnectedReadSetReviews(reopened_sets, reopened_grants)
    assert ConnectedReadSetApprovals(reopened_reviews).approved(session) == review
    assert reopened_grants.state(session).status == "none"
    assert reopened_grants.tool_schema(session) is None
    assert state["reads"] == 0


def test_two_entry_confirmation_cannot_change_one_handle_or_partially_approve(connected):
    current, crud, session, grants, fixed, state = connected
    second = {"window": {"since": "2026-01-03T00:00:00Z", "until": "2026-01-04T00:00:00Z"}}
    sets = ConnectedReadSets(crud, current)
    proposal = sets.propose(
        session,
        "set",
        (_terms(current, session, grants, fixed, state), _terms(current, session, grants, second, state)),
    )
    reviews = ConnectedReadSetReviews(sets, grants)
    review = reviews.prepare(session, "review", "set", proposal.revision)
    approvals = ConnectedReadSetApprovals(reviews)
    proof = approvals.issue(session, "review", review.revision, "human-confirmation")
    payload = review.model_dump(mode="json")
    payload["proposal"]["entries"][1]["handle"] = "read-" + "0" * 32
    confirmation = ApprovalConfirmation()
    args = {"session_id": session, "request_id": "review", "review": payload, "approval_proof": proof}
    with (
        confirmation.using("heinzel", "approve-connected-read-set", args),
        pytest.raises(HeinzelError, match="reviewed read set changed"),
    ):
        approvals.approve(session, "review", payload, proof, confirmation)
    assert not any(e.kind == "connected-set.approved" for e in crud.repository.control_read(session).events)
    assert state["reads"] == 0


def test_forged_duplicate_consumption_invalidates_projection(connected):
    _, crud, session, _, state, _, _, review, approvals = _prepare(connected)
    proof = approvals.issue(session, "review", review.revision, "human-confirmation")
    payload = review.model_dump(mode="json")
    confirmation = ApprovalConfirmation()
    args = {"session_id": session, "request_id": "review", "review": payload, "approval_proof": proof}
    with confirmation.using("heinzel", "approve-connected-read-set", args):
        approvals.approve(session, "review", payload, proof, confirmation)
    event = next(e for e in crud.repository.control_read(session).events if e.kind == "connected-set.approved")
    with crud.repository.control_writer(session) as writer:
        writer.append("connected-set.approved", key="forged", data=event.data)
    with pytest.raises(HeinzelError, match="invalid or revoked"):
        approvals.approved(session)
    assert state["reads"] == 0
