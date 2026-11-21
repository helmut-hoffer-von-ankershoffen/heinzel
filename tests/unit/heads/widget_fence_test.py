"""Prove fenced widget issuance and revocation transitions without real tokens."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from heinzel.heads.web._widget_fence import FenceConflictError, FenceState, Request, transition

pytestmark = pytest.mark.unit


def test_retry_is_identical_and_does_not_extend_expiry() -> None:
    now = datetime(2026, 11, 16, tzinfo=UTC)
    initial = FenceState.new()
    request = Request(initial.epoch, initial.revision, "a" * 32, "issue", "1" * 64)
    issued, receipt = transition(initial, request, now)
    replayed, retry = transition(issued, request, now + timedelta(hours=2))
    assert replayed == issued
    assert retry == receipt
    assert retry.expires == now + timedelta(hours=24)
    expired_replay, expired_receipt = transition(issued, request, now + timedelta(hours=25))
    assert expired_replay == issued
    assert expired_receipt.expires == receipt.expires
    changed_commitment = replace(request, commitment="2" * 64)
    with pytest.raises(FenceConflictError):
        transition(issued, changed_commitment, now)


def test_reordered_mutations_cannot_overwrite_newer_state() -> None:
    now = datetime(2026, 11, 16, tzinfo=UTC)
    initial = FenceState.new()
    issue = Request(initial.epoch, initial.revision, "a" * 32, "issue", "1" * 64)
    first, issued = transition(initial, issue, now)
    revoke = Request(first.epoch, first.revision, "b" * 32, "revoke", None)
    revoked, receipt = transition(first, revoke, now)
    assert revoked.child_hash is None
    assert receipt.revision != issued.revision
    stale_issue = replace(issue, attempt="c" * 32, commitment="2" * 64)
    with pytest.raises(FenceConflictError):
        transition(revoked, stale_issue, now)
    replacement, _ = transition(revoked, Request(revoked.epoch, revoked.revision, "d" * 32, "issue", "3" * 64), now)
    with pytest.raises(FenceConflictError):
        transition(replacement, revoke, now)
    wrong_epoch = Request(FenceState.new().epoch, replacement.revision, "e" * 32, "revoke", None)
    with pytest.raises(FenceConflictError):
        transition(replacement, wrong_epoch, now)


def test_rejects_bad_commitments_and_naive_clock() -> None:
    state = FenceState.new()
    now = datetime(2026, 11, 16, tzinfo=UTC)
    bad_commitment = Request(state.epoch, state.revision, "a" * 32, "issue", "hzw_not_a_hash")
    with pytest.raises(FenceConflictError):
        transition(state, bad_commitment, now)
    revoke = Request(state.epoch, state.revision, "a" * 32, "revoke", None)
    naive_now = now.replace(tzinfo=None)
    with pytest.raises(FenceConflictError):
        transition(state, revoke, naive_now)


def test_revoke_empty_is_fenced_and_replay_is_idempotent() -> None:
    now = datetime(2026, 11, 16, tzinfo=UTC)
    initial = FenceState.new()
    request = Request(initial.epoch, initial.revision, "f" * 32, "revoke", None)
    revoked, receipt = transition(initial, request, now)
    assert revoked.child_hash is None
    assert revoked.revision != initial.revision
    assert transition(revoked, request, now + timedelta(days=1)) == (revoked, receipt)
    changed_operation = replace(request, operation="issue", commitment="1" * 64)
    with pytest.raises(FenceConflictError):
        transition(revoked, changed_operation, now)
    changed_revision = replace(request, revision=revoked.revision)
    with pytest.raises(FenceConflictError):
        transition(revoked, changed_revision, now)
    changed_epoch = replace(request, epoch=FenceState.new().epoch)
    with pytest.raises(FenceConflictError):
        transition(revoked, changed_epoch, now)


def test_old_exact_issue_replay_is_rejected_after_supersession() -> None:
    now = datetime(2026, 11, 16, tzinfo=UTC)
    initial = FenceState.new()
    first = Request(initial.epoch, initial.revision, "a" * 32, "issue", "1" * 64)
    issued, _ = transition(initial, first, now)
    second = Request(issued.epoch, issued.revision, "b" * 32, "issue", "2" * 64)
    replaced, _ = transition(issued, second, now)
    with pytest.raises(FenceConflictError):
        transition(replaced, first, now)
