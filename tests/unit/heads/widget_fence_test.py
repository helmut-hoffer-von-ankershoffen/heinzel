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
    with pytest.raises(FenceConflictError):
        transition(issued, replace(request, commitment="2" * 64), now)


def test_reordered_mutations_cannot_overwrite_newer_state() -> None:
    now = datetime(2026, 11, 16, tzinfo=UTC)
    initial = FenceState.new()
    issue = Request(initial.epoch, initial.revision, "a" * 32, "issue", "1" * 64)
    first, issued = transition(initial, issue, now)
    revoke = Request(first.epoch, first.revision, "b" * 32, "revoke", None)
    revoked, receipt = transition(first, revoke, now)
    assert revoked.child_hash is None
    assert receipt.revision != issued.revision
    with pytest.raises(FenceConflictError):
        transition(revoked, replace(issue, attempt="c" * 32, commitment="2" * 64), now)
    replacement, _ = transition(revoked, Request(revoked.epoch, revoked.revision, "d" * 32, "issue", "3" * 64), now)
    with pytest.raises(FenceConflictError):
        transition(replacement, revoke, now)
    with pytest.raises(FenceConflictError):
        transition(replacement, Request(FenceState.new().epoch, replacement.revision, "e" * 32, "revoke", None), now)


def test_rejects_bad_commitments_and_naive_clock() -> None:
    state = FenceState.new()
    now = datetime(2026, 11, 16, tzinfo=UTC)
    with pytest.raises(FenceConflictError):
        transition(state, Request(state.epoch, state.revision, "a" * 32, "issue", "hzw_not_a_hash"), now)
    with pytest.raises(FenceConflictError):
        transition(state, Request(state.epoch, state.revision, "a" * 32, "revoke", None), now.replace(tzinfo=None))


def test_revoke_empty_is_fenced_and_replay_is_idempotent() -> None:
    now = datetime(2026, 11, 16, tzinfo=UTC)
    initial = FenceState.new()
    request = Request(initial.epoch, initial.revision, "f" * 32, "revoke", None)
    revoked, receipt = transition(initial, request, now)
    assert revoked.child_hash is None
    assert revoked.revision != initial.revision
    assert transition(revoked, request, now + timedelta(days=1)) == (revoked, receipt)
    with pytest.raises(FenceConflictError):
        transition(revoked, replace(request, operation="issue", commitment="1" * 64), now)
    with pytest.raises(FenceConflictError):
        transition(revoked, replace(request, revision=revoked.revision), now)
    with pytest.raises(FenceConflictError):
        transition(revoked, replace(request, epoch=FenceState.new().epoch), now)


def test_old_exact_issue_replay_is_rejected_after_supersession() -> None:
    now = datetime(2026, 11, 16, tzinfo=UTC)
    initial = FenceState.new()
    first = Request(initial.epoch, initial.revision, "a" * 32, "issue", "1" * 64)
    issued, _ = transition(initial, first, now)
    second = Request(issued.epoch, issued.revision, "b" * 32, "issue", "2" * 64)
    replaced, _ = transition(issued, second, now)
    with pytest.raises(FenceConflictError):
        transition(replaced, first, now)
