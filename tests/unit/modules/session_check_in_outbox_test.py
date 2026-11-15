"""The check-in outbox resolves each slot at most once, and refuses any journal it cannot read safely."""

from datetime import UTC, datetime, timedelta
from typing import override

import pytest
from pydantic import ValidationError

from heinzel import CurrentUser, HeinzelError, Notifications
from heinzel.kernel._jobs import JobInactive
from heinzel.kernel._session import SessionEvent
from heinzel.modules.heinzel import _session_crud as crud
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

pytestmark = pytest.mark.unit

AT = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
CHECKPOINT = "checkin-outbox"


class _Notices(Notifications):
    def __init__(self, *, emit=True, forget=True, recorded=False):
        self.emit, self.forget, self.recorded = emit, forget, recorded
        self.sent: list[str] = []
        self.forgotten: list[set[str]] = []

    @override
    def emit_once(self, _kind, title, body, *, route, command, key, helper=None):
        self.sent.append(key)
        return self.emit

    @override
    def forget_completed(self, keys):
        self.forgotten.append(set(keys))
        return self.forget

    @override
    def has_once(self, _key):
        return self.recorded


def _intent(slot=AT, revision=1, key="notice-1"):
    return crud._CheckInIntent(
        slot=slot,
        schedule_revision=revision,
        contract_version=1,
        notice_key=key,
        title="t",
        body="b",
        route="/r",
        command="hz x",
    )


def _slot(slot=AT, journal_seq=0, revision=1, active_since=AT - timedelta(hours=1)):
    return crud._CheckInSlot(
        owner="u",
        session_id="s",
        contract_version=1,
        schedule_revision=revision,
        profile_name="Pip",
        helper=None,
        slot=slot,
        journal_seq=journal_seq,
        active_since=active_since,
    )


def _event(seq, kind, key=None, **data):
    return SessionEvent(seq=seq, ts=AT, prev=None, kind=kind, key=key, data=data)


def _legacy(seq, key="k1", notice="notice-1", slot=AT, **override):
    data = {
        "dedup_version": 1,
        "slot": slot.isoformat() if isinstance(slot, datetime) else slot,
        "contract_version": 1,
        "notice_key": notice,
        "title": "t",
        "body": "b",
        "route": "/r",
        "command": "hz x",
    } | override
    return _event(seq, "check-in.intent", key, **data)


@pytest.fixture
def writer(tmp_path):
    current = CurrentUser(tmp_path / "state" / "identity")
    repository = PersonalSessionRepository(current, clock=lambda: AT)
    service = SessionCrud(repository, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2), clock=lambda: AT)
    with current.local("cli"):
        session = service.new_chat("create-1", "Pip", None)
        with repository.control_writer(session.session_id) as control:
            yield control


def _stored(writer):
    return crud._CheckInCheckpoint.model_validate(writer.checkpoint(CHECKPOINT), strict=False)


def _seed(writer, **fields):
    checkpoint = crud._CheckInCheckpoint(version=1, **fields)
    writer.replace_checkpoint(CHECKPOINT, checkpoint.model_dump(mode="json"))
    return checkpoint


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"last_completed": datetime(2026, 1, 1)}, "watermark must include a timezone"),  # noqa: DTZ001
        ({"pending": crud._CheckInIntent.model_construct(slot=datetime(2026, 1, 1))}, "slot must include a timezone"),  # noqa: DTZ001
        ({"pending": _intent(AT), "last_completed": AT}, "cannot precede its completion watermark"),
        ({"cleanup_key": "k"}, "receipt cleanup requires a completed slot"),
        ({"cleanup_key": "k", "last_completed": AT, "pending": _intent(AT + timedelta(minutes=5))}, "no pending"),
    ],
)
def test_an_impossible_checkpoint_is_rejected(fields, message):
    with pytest.raises((ValidationError, ValueError), match=message):
        crud._CheckInCheckpoint(version=1, **fields)


def test_a_slot_that_was_already_completed_is_not_notified_again(writer):
    _seed(writer, last_completed=AT)
    notices = _Notices()
    result = crud._dispatch_check_in_slot(writer, notices, _slot(AT), [])
    assert result == {"session_id": "s", "slot": AT.isoformat(), "notice": True}
    assert notices.sent == []


def test_a_new_slot_is_notified_once_and_its_receipt_is_compacted(writer):
    notices = _Notices()
    result = crud._dispatch_check_in_slot(writer, notices, _slot(AT), [])
    assert result["notice"] is True
    assert len(notices.sent) == 1
    assert notices.forgotten == [set(notices.sent)]
    stored = _stored(writer)
    assert stored.last_completed == AT
    assert stored.pending is None
    assert stored.cleanup_key is None


def test_a_notice_that_cannot_be_recorded_keeps_the_slot_pending_for_retry(writer):
    with pytest.raises(HeinzelError, match="could not be recorded"):
        crud._dispatch_check_in_slot(writer, _Notices(emit=False), _slot(AT), [])
    assert _stored(writer).pending is not None
    retry = _Notices()
    crud._dispatch_check_in_slot(writer, retry, _slot(AT + timedelta(minutes=5)), [])
    assert len(retry.sent) == 1
    assert _stored(writer).pending is None


def test_a_receipt_that_cannot_be_compacted_is_reported_after_the_slot_completed(writer):
    with pytest.raises(HeinzelError, match="receipt cleanup is pending"):
        crud._dispatch_check_in_slot(writer, _Notices(forget=False), _slot(AT), [])
    stored = _stored(writer)
    assert stored.last_completed == AT
    assert stored.cleanup_key is not None
    # The next run repeats only the cleanup, and replays no notification.
    notices = _Notices()
    crud._dispatch_check_in_slot(writer, notices, _slot(AT), [])
    assert notices.sent == []
    assert _stored(writer).cleanup_key is None


def test_a_pending_cleanup_that_still_fails_stops_the_slot(writer):
    _seed(writer, last_completed=AT, cleanup_key="old")
    with pytest.raises(HeinzelError, match="could not be compacted"):
        crud._dispatch_check_in_slot(writer, _Notices(forget=False), _slot(AT), [])


def test_a_journal_behind_its_checkpoint_is_refused(writer):
    _seed(writer, journal_seq=5)
    with pytest.raises(HeinzelError, match="moved behind its delivery checkpoint"):
        crud._dispatch_check_in_slot(writer, _Notices(), _slot(AT, journal_seq=2), [])


def test_an_earlier_compiled_slot_cannot_move_the_durable_slot_backwards(writer):
    _seed(writer, pending=_intent(AT + timedelta(minutes=10)))
    result = crud._dispatch_check_in_slot(writer, _Notices(), _slot(AT), [])
    assert isinstance(result, JobInactive)


def test_legacy_delivery_records_are_folded_into_the_checkpoint_and_their_receipts_compacted(writer):
    events = [_legacy(1), _event(2, "check-in.recorded", "k1", notice_key="notice-1")]
    notices = _Notices()
    crud._dispatch_check_in_slot(writer, notices, _slot(AT, journal_seq=2), events)
    assert {"notice-1"} in notices.forgotten
    assert _stored(writer).last_completed == AT
    assert notices.sent == []


def test_legacy_receipts_that_cannot_be_compacted_stop_the_migration(writer):
    events = [_legacy(1), _event(2, "check-in.recorded", "k1", notice_key="notice-1")]
    with pytest.raises(HeinzelError, match="Legacy completed check-in receipts could not be compacted"):
        crud._dispatch_check_in_slot(writer, _Notices(forget=False), _slot(AT, journal_seq=2), events)


def test_an_older_pending_slot_from_a_prior_activation_is_resolved_without_a_new_notice(writer):
    _seed(writer, pending=_intent(AT - timedelta(hours=2), revision=1))
    notices = _Notices(recorded=True)
    crud._dispatch_check_in_slot(writer, notices, _slot(AT, revision=2), [])
    assert notices.sent[0].startswith("heinzel:")
    assert "notice-1" in {key for keys in notices.forgotten for key in keys}
    assert _stored(writer).last_completed == AT


def test_an_unrecorded_older_pending_slot_is_dropped_not_posted(writer):
    _seed(writer, pending=_intent(AT - timedelta(hours=2), revision=1))
    result = crud._reconcile_stale_check_in(writer, _Notices(recorded=False), _stored(writer), _slot(AT, revision=2))
    assert result.pending is None
    assert result.last_completed is None
    assert result.cleanup_key is None


def test_an_older_slot_whose_outcome_cannot_be_inspected_stops_the_run(writer):
    _seed(writer, pending=_intent(AT - timedelta(hours=2), revision=1))

    class Unknown(_Notices):
        @override
        def has_once(self, _key):
            return None

    with pytest.raises(HeinzelError, match="cannot be inspected safely"):
        crud._reconcile_stale_check_in(writer, Unknown(), _stored(writer), _slot(AT, revision=2))


def test_a_recorded_older_slot_whose_cleanup_fails_is_reported(writer):
    _seed(writer, pending=_intent(AT - timedelta(hours=2), revision=1))
    with pytest.raises(HeinzelError, match="prior check-in is recorded"):
        crud._reconcile_stale_check_in(
            writer, _Notices(recorded=True, forget=False), _stored(writer), _slot(AT, revision=2)
        )


@pytest.mark.parametrize(
    "event",
    [
        _event(1, "check-in.intent", None),
        _legacy(1, dedup_version=2),
        _legacy(1, slot=5),
        _legacy(1, contract_version="1"),
        _legacy(1, slot="not-a-time"),
        _legacy(1, notice=""),
    ],
)
def test_an_ambiguous_legacy_intent_is_refused(event):
    with pytest.raises(HeinzelError, match="Legacy check-in delivery history"):
        crud._parse_legacy_check_in(event, 1)


def test_a_legacy_intent_field_that_is_not_text_is_refused():
    with pytest.raises(HeinzelError, match="invalid field types"):
        crud._parse_legacy_check_in(_legacy(1, title=7), 1)


def _fold(*events, checkpoint=None):
    return crud._reconcile_legacy_check_in_events(checkpoint or crud._CheckInCheckpoint(version=1), list(events), 9, 1)


@pytest.mark.parametrize(
    ("events", "message"),
    [
        ([_legacy(1), _event(2, "check-in.recorded", None, notice_key="notice-1")], "has no identity"),
        ([_event(1, "check-in.recorded", "k9", notice_key="n")], "no matching intent"),
        ([_legacy(1), _event(2, "check-in.recorded", "k1", notice_key="other")], "does not match its intent"),
        ([_legacy(1, "k1", "n1"), _legacy(2, "k2", "n2", slot=AT + timedelta(minutes=5))], "Several legacy"),
    ],
)
def test_contradictory_legacy_records_are_refused(events, message):
    with pytest.raises(HeinzelError, match=message):
        _fold(*events)


def test_legacy_records_with_one_unresolved_intent_become_the_pending_slot():
    done = [_legacy(1, "k1", "n1"), _event(2, "check-in.recorded", "k1", notice_key="n1")]
    later = _legacy(3, "k2", "n2", slot=AT + timedelta(minutes=5))
    result, completed = _fold(*done, later)
    assert completed == ["n1"]
    assert result.last_completed == AT
    assert result.pending is not None
    assert result.pending.notice_key == "n2"
    assert result.journal_seq == 9


def test_a_legacy_intent_that_predates_the_watermark_is_refused():
    with pytest.raises(HeinzelError, match="predates its completion watermark"):
        _fold(_legacy(1), checkpoint=crud._CheckInCheckpoint(version=1, last_completed=AT))


def test_legacy_records_overlapping_the_bounded_outbox_are_refused():
    pending = crud._CheckInCheckpoint(version=1, pending=_intent(AT, key="mine"))
    with pytest.raises(HeinzelError, match="overlaps the bounded outbox"):
        _fold(_legacy(1, notice="theirs"), checkpoint=pending)
    done = [_legacy(1, "k1", "theirs"), _event(2, "check-in.recorded", "k1", notice_key="theirs")]
    with pytest.raises(HeinzelError, match="overlaps an unresolved check-in"):
        _fold(*done, checkpoint=pending)
