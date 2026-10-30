"""One owner-wide receipt key must never be silently replaced across sessions or grant kinds."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from heinzel import HeinzelError
from heinzel.kernel._approval import ApprovalConfirmation
from heinzel.modules.heinzel._input_receipts import ApprovedInputSnapshots, input_receipt_key
from tests.unit.modules.connected_read_set_input_test import _approved, _turn

pytestmark = pytest.mark.unit
pytest_plugins = ("tests.unit.modules.connected_grants_test",)


def _approve_legacy(grants, session, fixed):
    proposal = grants.propose(session, "legacy", "calendar", "events", fixed)
    proof = grants.issue(session, "legacy", "human-confirmation")
    review = proposal.model_dump(mode="json")
    confirmation = ApprovalConfirmation()
    args = {"session_id": session, "request_id": "legacy", "review": review, "approval_proof": proof}
    with confirmation.using("heinzel", "approve-connected-read", args):
        grants.approve(session, "legacy", review, proof, confirmation)


def test_missing_set_key_cannot_be_recreated_by_legacy_read_in_other_session(connected):
    current, crud, session, grants, state, _, _, review, approvals = _approved(connected)
    call = _turn(crud, session, review.proposal.entries[0].handle)
    with crud.repository.main_writer(session) as writer:
        grants.read_set_entry(session, writer, call, approvals)
    other = crud.new_chat("other").session_id
    fixed = review.proposal.entries[0].terms.review.fixed_arguments
    _approve_legacy(grants, other, fixed)
    (current.directory / "input-receipt-key-v1").unlink()
    # Exercise migration too: no marker could exist before this version of the host.
    (current.directory / "input-receipt-key-id-v1").unlink()
    with crud.repository.main_writer(other) as writer, pytest.raises(HeinzelError, match="history needs repair"):
        ApprovedInputSnapshots(grants, current).read(other, writer)
    assert state["reads"] == 1
    assert not (current.directory / "input-receipt-key-v1").exists()


def test_missing_legacy_key_cannot_be_recreated_by_set_read_in_other_session(connected):
    current, crud, session, grants, state, _, _, review, approvals = _approved(connected)
    fixed = review.proposal.entries[0].terms.review.fixed_arguments
    other = crud.new_chat("other").session_id
    _approve_legacy(grants, other, fixed)
    with crud.repository.main_writer(other) as writer:
        ApprovedInputSnapshots(grants, current).read(other, writer)
    (current.directory / "input-receipt-key-v1").unlink()
    (current.directory / "input-receipt-key-id-v1").unlink()
    call = _turn(crud, session, review.proposal.entries[0].handle)
    with crud.repository.main_writer(session) as writer, pytest.raises(HeinzelError, match="history needs repair"):
        grants.read_set_entry(session, writer, call, approvals)
    assert state["reads"] == 1
    assert not (current.directory / "input-receipt-key-v1").exists()


def test_replaced_or_interrupted_key_is_never_silently_accepted(connected, monkeypatch):
    from heinzel.modules.heinzel import _input_receipts as receipts

    current, _, _session, _grants, _fixed, _state = connected
    original_rename = receipts.os.rename

    def interrupted(*_args, **_kwargs):
        msg = "fixture interrupted"
        raise OSError(msg)

    monkeypatch.setattr(receipts.os, "rename", interrupted)
    with pytest.raises(HeinzelError, match="key is unavailable"):
        input_receipt_key(current, create=True)
    assert (current.directory / "input-receipt-key-id-v1").exists()
    monkeypatch.setattr(receipts.os, "rename", original_rename)
    with pytest.raises(HeinzelError, match="history needs repair"):
        input_receipt_key(current, create=True)
    (current.directory / "input-receipt-key-v1").write_bytes(b"x" * 32)
    (current.directory / "input-receipt-key-v1").chmod(0o600)
    with pytest.raises(HeinzelError, match="key needs repair"):
        input_receipt_key(current, create=True)


def test_concurrent_initialization_yields_one_owner_key(connected):
    current, _, _session, _grants, _fixed, _state = connected
    barrier = Barrier(2)

    def run():
        with current.local("cli"):
            barrier.wait(timeout=3)
            return input_receipt_key(current, create=True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: run(), range(2)))
    assert first == second
    assert len(first) == 32


def test_upgrade_bootstrap_rejects_replaced_key_with_old_receipts(connected):
    current, crud, session, grants, fixed, state = connected
    _approve_legacy(grants, session, fixed)
    with crud.repository.main_writer(session) as writer:
        ApprovedInputSnapshots(grants, current).read(session, writer)
    (current.directory / "input-receipt-key-id-v1").unlink()
    (current.directory / "input-receipt-key-v1").write_bytes(b"x" * 32)
    with pytest.raises(HeinzelError, match="history needs repair"):
        input_receipt_key(current, create=True)
    assert state["reads"] == 1
    assert not (current.directory / "input-receipt-key-id-v1").exists()


def test_incomplete_reserved_owner_session_refuses_first_key_creation(connected):
    current, crud, _session, _grants, _fixed, _state = connected
    crud.repository.reserve("interrupted-creation", "a" * 64)
    with pytest.raises(HeinzelError):
        input_receipt_key(current, create=True)
    assert not (current.directory / "input-receipt-key-v1").exists()
    assert not (current.directory / "input-receipt-key-id-v1").exists()


@pytest.mark.parametrize("old_kind", ["set", "legacy"])
def test_deleted_catalog_with_surviving_owner_journals_cannot_rekey(connected, old_kind):
    import shutil

    current, crud, session, grants, state, _, _, review, approvals = _approved(connected)
    fixed = review.proposal.entries[0].terms.review.fixed_arguments
    if old_kind == "set":
        call = _turn(crud, session, review.proposal.entries[0].handle)
        with crud.repository.main_writer(session) as writer:
            grants.read_set_entry(session, writer, call, approvals)
    else:
        _approve_legacy(grants, session, fixed)
        with crud.repository.main_writer(session) as writer:
            ApprovedInputSnapshots(grants, current).read(session, writer)
    (current.directory / "input-receipt-key-v1").unlink()
    (current.directory / "input-receipt-key-id-v1").unlink()
    root = current.directory.parent / "heinzel-sessions" / current.principal().id
    shutil.rmtree(root / "catalog")
    other = crud.new_chat("fresh-catalog").session_id
    _approve_legacy(grants, other, fixed)
    with crud.repository.main_writer(other) as writer, pytest.raises(HeinzelError, match="session stores need repair"):
        ApprovedInputSnapshots(grants, current).read(other, writer)
    assert state["reads"] == 1
    assert not (current.directory / "input-receipt-key-v1").exists()
    assert not (current.directory / "input-receipt-key-id-v1").exists()


def test_fresh_owner_without_session_stores_can_initialize_key(tmp_path):
    from heinzel import CurrentUser

    current = CurrentUser(tmp_path / "fresh-identity")
    with current.local("cli"):
        first = input_receipt_key(current, create=True)
        assert input_receipt_key(current, create=True) == first
    assert len(first) == 32


def test_unreserved_symlink_in_owner_store_refuses_bootstrap(connected, tmp_path):
    current, _crud, _session, _grants, _fixed, _state = connected
    root = current.directory.parent / "heinzel-sessions" / current.principal().id / "control"
    (root / "hz-suspicious").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(HeinzelError, match="session stores need repair"):
        input_receipt_key(current, create=True)
    assert not (current.directory / "input-receipt-key-v1").exists()
