"""A read-set value is returned only after a bounded, owner-bound, receipted read."""

import hashlib

import pytest

from heinzel import HeinzelError
from heinzel.kernel._approval import ApprovalConfirmation
from heinzel.modules.heinzel._connected_read_set_inputs import ReadSetCall, set_input_key, verified_set_inputs
from heinzel.modules.heinzel._session_models import MessageRequest
from tests.unit.modules.connected_read_set_approval_test import _prepare

pytestmark = pytest.mark.unit
pytest_plugins = ("tests.unit.modules.connected_grants_test",)


def _approved(connected):
    current, crud, session, grants, state, sets, reviews, review, approvals = _prepare(connected)
    proof = approvals.issue(session, "review", review.revision, "human-confirmation")
    payload = review.model_dump(mode="json")
    confirmation = ApprovalConfirmation()
    args = {"session_id": session, "request_id": "review", "review": payload, "approval_proof": proof}
    with confirmation.using("heinzel", "approve-connected-read-set", args):
        approvals.approve(session, "review", payload, proof, confirmation)
    return current, crud, session, grants, state, sets, reviews, review, approvals


def _turn(crud, session, handle, number=1):
    crud.accept(session, MessageRequest(request_id="fixture", text="Read the approved inputs."))
    call = ReadSetCall(handle=handle, turn="interactive-" + hashlib.sha256(b"fixture").hexdigest(), call=number)
    with crud.repository.control_writer(session) as writer:
        writer.append("interactive.started", key=call.turn, turn=call.turn, data={"message_id": "fixture"})
        writer.append(
            "interactive.tool",
            key=f"{call.turn}-{call.call}-requested",
            turn=call.turn,
            data={"tool": "connected_read_set", "status": "requested", "handle": call.handle},
        )
    return call


def test_approved_entry_returns_only_after_authenticated_current_turn_receipt(connected):
    current, crud, session, grants, state, _, _, review, approvals = _approved(connected)
    call = _turn(crud, session, review.proposal.entries[0].handle)
    with crud.repository.main_writer(session) as writer:
        value, sequence = grants.read_set_entry(session, writer, call, approvals)
        with pytest.raises(HeinzelError, match="already completed"):
            grants.read_set_entry(session, writer, call, approvals)
    assert value == {"events": ["fictional"]}
    assert state["reads"] == 1
    key, key_id = set_input_key(current, crud, session)
    events = verified_set_inputs(crud, session, key, key_id)
    assert len(events) == 1
    assert events[0].seq == sequence
    assert events[0].turn == call.turn
    assert "fictional" not in str(events[0].data)


def test_unknown_handle_unrequested_turn_and_daemon_never_read(connected):
    current, crud, session, grants, state, _, _, review, approvals = _approved(connected)
    call = _turn(crud, session, review.proposal.entries[0].handle)
    unknown = call.model_copy(update={"handle": "read-" + "0" * 32})
    unrequested = call.model_copy(update={"call": 2})
    with crud.repository.main_writer(session) as writer:
        with pytest.raises(HeinzelError, match="call is unavailable"):
            grants.read_set_entry(session, writer, unknown, approvals)
        with pytest.raises(HeinzelError, match="call is unavailable"):
            grants.read_set_entry(session, writer, unrequested, approvals)
        with current.local("daemon"), pytest.raises(HeinzelError, match="authorized turn"):
            grants.read_set_entry(session, writer, call, approvals)
    assert state["reads"] == 0


def test_account_changed_during_read_never_records_receipt_or_returns_value(connected):
    _, crud, session, grants, state, _, _, review, approvals = _approved(connected)
    call = _turn(crud, session, review.proposal.entries[0].handle)
    state["read_hook"] = lambda: state.update(account="other-account")
    with (
        crud.repository.main_writer(session) as writer,
        pytest.raises(HeinzelError, match="source or account could not be confirmed"),
    ):
        grants.read_set_entry(session, writer, call, approvals)
    assert state["reads"] == 1
    assert not any(e.kind == "connected-set-input.observed" for e in crud.repository.control_read(session).events)


def test_receipt_failure_hides_successful_value(connected, monkeypatch):
    from heinzel.modules.heinzel import _connected_read_set_inputs as receipts

    _, crud, session, grants, state, _, _, review, approvals = _approved(connected)
    call = _turn(crud, session, review.proposal.entries[0].handle)

    def failed(*_args):
        msg = "private receipt failure"
        raise OSError(msg)

    monkeypatch.setattr(receipts, "record_set_input", failed)
    with (
        crud.repository.main_writer(session) as writer,
        pytest.raises(HeinzelError, match="snapshot could not be recorded"),
    ):
        grants.read_set_entry(session, writer, call, approvals)
    assert state["reads"] == 1
    assert not any(e.kind == "connected-set-input.observed" for e in crud.repository.control_read(session).events)
    with crud.repository.main_writer(session) as writer, pytest.raises(HeinzelError, match="already completed"):
        grants.read_set_entry(session, writer, call, approvals)
    assert state["reads"] == 1


def test_lost_receipt_key_fails_before_another_provider_read(connected):
    current, crud, session, grants, state, _, _, review, approvals = _approved(connected)
    first = _turn(crud, session, review.proposal.entries[0].handle)
    with crud.repository.main_writer(session) as writer:
        grants.read_set_entry(session, writer, first, approvals)
    (current.directory / "input-receipt-key-v1").unlink()
    second = _turn(crud, session, review.proposal.entries[0].handle, number=2)
    with crud.repository.main_writer(session) as writer, pytest.raises(HeinzelError, match="history needs repair"):
        grants.read_set_entry(session, writer, second, approvals)
    assert state["reads"] == 1


def test_forged_receipt_is_rejected_before_read(connected):
    _, crud, session, grants, state, _, _, review, approvals = _approved(connected)
    call = _turn(crud, session, review.proposal.entries[0].handle)
    with crud.repository.control_writer(session) as writer:
        writer.append(
            "connected-set-input.observed",
            key="forged",
            turn=call.turn,
            data={
                "version": 1,
                "key_id": "0" * 64,
                "turn": call.turn,
                "call": 999,
                "scope": "0" * 64,
                "digest": "0" * 64,
                "mac": "0" * 64,
            },
        )
    with crud.repository.main_writer(session) as writer, pytest.raises(HeinzelError):
        grants.read_set_entry(session, writer, call, approvals)
    assert state["reads"] == 0


def test_failed_lease_cleanup_never_returns_or_records_result(connected, monkeypatch):
    from heinzel import ConnectedReadService

    _, crud, session, grants, state, _, _, review, approvals = _approved(connected)
    call = _turn(crud, session, review.proposal.entries[0].handle)
    _record, service = grants.kernel.selected_capability_service(ConnectedReadService, "calendar")
    original = service.open_lease

    def broken_close(*args):
        lease = original(*args)

        def fail():
            msg = "private cleanup failed"
            raise OSError(msg)

        lease.close = fail
        return lease

    monkeypatch.setattr(service, "open_lease", broken_close)
    with (
        crud.repository.main_writer(session) as writer,
        pytest.raises(HeinzelError, match="cleanup could not be confirmed"),
    ):
        grants.read_set_entry(session, writer, call, approvals)
    assert state["reads"] == 1
    assert not any(e.kind == "connected-set-input.observed" for e in crud.repository.control_read(session).events)


def test_two_handles_read_only_own_fixed_terms_and_distinct_receipt_scopes(connected):
    from heinzel.modules.heinzel._connected_read_set_approvals import ConnectedReadSetApprovals
    from heinzel.modules.heinzel._connected_read_set_reviews import ConnectedReadSetReviews
    from heinzel.modules.heinzel._connected_read_sets import ConnectedReadSets
    from tests.unit.modules.connected_read_set_review_test import _terms

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
    confirmation = ApprovalConfirmation()
    args = {"session_id": session, "request_id": "review", "review": payload, "approval_proof": proof}
    with confirmation.using("heinzel", "approve-connected-read-set", args):
        approvals.approve(session, "review", payload, proof, confirmation)
    first_call = _turn(crud, session, proposal.entries[0].handle)
    second_call = _turn(crud, session, proposal.entries[1].handle, number=2)
    swapped = first_call.model_copy(update={"handle": second_call.handle})
    with crud.repository.main_writer(session) as writer:
        with pytest.raises(HeinzelError, match="call is unavailable"):
            grants.read_set_entry(session, writer, swapped, approvals)
        assert state["reads"] == 0
        first_value, _ = grants.read_set_entry(session, writer, first_call, approvals)
        assert state["last_fixed"] == fixed
        second_value, _ = grants.read_set_entry(session, writer, second_call, approvals)
        assert state["last_fixed"] == second
    assert first_value == second_value
    key, key_id = set_input_key(current, crud, session)
    receipts = verified_set_inputs(crud, session, key, key_id)
    assert len(receipts) == 2
    assert receipts[0].data["scope"] != receipts[1].data["scope"]
    assert receipts[0].data["digest"] != receipts[1].data["digest"]


def test_receipt_binding_repr_hides_owner_private_key(connected):
    from heinzel.modules.heinzel._connected_read_set_inputs import ReadSetReceiptBinding

    _, crud, session, _, _, _, _, review, _ = _approved(connected)
    call = _turn(crud, session, review.proposal.entries[0].handle)
    secret = b"private-key-not-for-output"
    binding = ReadSetReceiptBinding(call, review, secret, "fixture-key-id")
    assert "private-key-not-for-output" not in repr(binding)
