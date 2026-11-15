"""The scheduled check-in notice is recorded once per slot, resumes after a crash and never replays a slot."""

from datetime import datetime, timedelta

import pytest

from heinzel import CurrentUser, HeinzelError
from heinzel.kernel._jobs import JobInactive
from heinzel.kernel._notifications import Notifications
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository
from tests.unit.modules.session_crud_test import AT, _issue_test_approval

pytestmark = pytest.mark.unit

CHECKPOINT = "checkin-outbox"


class FakeNotices(Notifications):
    def __init__(self):
        self.recorded = []
        self.forgotten = []
        self.accept = True
        self.forget = True
        self.seen = False

    def emit_once(self, kind, title, body, *, route, command, key, helper=None, **_):  # noqa: PLR0913
        self.recorded.append((key, title, body, route, command, helper))
        return self.accept

    def forget_completed(self, keys):
        self.forgotten.append(set(keys))
        return self.forget

    def has_once(self, key):
        return self.seen


def slots(active):
    return [item[0].split("check-in:", 1)[1] for item in active.notices.recorded]


class Active:
    def __init__(self, current):
        self.current = current
        self.repository = PersonalSessionRepository(self.current, clock=lambda: AT)
        self.service = SessionCrud(
            self.repository, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2), clock=lambda: AT
        )
        self.notices = FakeNotices()
        self.session = self.service.new_chat("create-1", "Ada").session_id
        proposed = self.service.propose_check_in(self.session, "check-in-v1", "Europe/Berlin").schedule
        proof = _issue_test_approval(
            self.repository, self.current, self.session, "activate-1", 1, "activate", proposed.preview_hash
        )
        self.schedule = self.service.schedule_control(
            self.session, "activate-1", 1, "activate", proof, proposed.contract_version, proposed.preview_hash
        ).schedule

    def emit(self, minutes, *, revision=None):
        return self.service.emit_check_in(
            self.session,
            self.schedule.contract_version,
            revision or self.schedule.revision,
            AT + timedelta(minutes=minutes),
            self.notices,
        )

    def checkpoint(self):
        with self.repository.control_writer(self.session) as writer:
            return writer.checkpoint(CHECKPOINT)

    def write_checkpoint(self, value):
        with self.repository.control_writer(self.session) as writer:
            writer.replace_checkpoint(CHECKPOINT, value)

    def append(self, kind, key, data):
        with self.repository.control_writer(self.session) as writer:
            writer.append(kind, key=key, data=data)


@pytest.fixture
def active(tmp_path):
    current = CurrentUser(tmp_path / "state" / "identity")
    with current.local("cli"):
        yield Active(current)


def test_a_slot_is_recorded_once_and_older_or_replayed_slots_post_nothing(active):
    result = active.emit(10)
    assert result == {"session_id": active.session, "slot": (AT + timedelta(minutes=10)).isoformat(), "notice": True}
    [(key, title, body, route, command, helper)] = active.notices.recorded
    assert key.endswith(f"check-in:{(AT + timedelta(minutes=10)).isoformat()}")
    assert (title, route, command) == ("Ada checked in", f"/heinzel?chat={active.session}", "hz heinzel sessions")
    assert body == "Your scheduled five-minute check-in is due."
    assert helper.reason == "scheduled check-in"
    assert active.notices.forgotten == [{key}]
    assert active.checkpoint()["cleanup_key"] is None
    assert active.emit(10) == result
    assert active.emit(5) == {
        "session_id": active.session,
        "slot": (AT + timedelta(minutes=5)).isoformat(),
        "notice": True,
    }
    assert len(active.notices.recorded) == 1
    assert active.emit(15)["notice"] is True
    assert len(active.notices.recorded) == 2


def test_an_inactive_or_changed_schedule_or_unusable_slot_posts_nothing(active):
    assert active.emit(10, revision=active.schedule.revision + 1) == JobInactive(
        reason="the check-in schedule changed before this slot ran"
    )
    assert active.emit(-10) == JobInactive(reason="the check-in slot predates this active schedule")
    naive = datetime(2026, 10, 25, 0, 10)  # noqa: DTZ001 - a slot without a zone must be refused
    unaware = active.service.emit_check_in(
        active.session, active.schedule.contract_version, active.schedule.revision, naive, active.notices
    )
    assert unaware == JobInactive(reason="the check-in slot or activation time is unavailable")
    wrong_contract = active.service.emit_check_in(
        active.session, 9, active.schedule.revision, AT + timedelta(minutes=10), active.notices
    )
    assert wrong_contract == JobInactive(reason="the check-in schedule is no longer active")
    paused = active.service.schedule_control(active.session, "pause-1", active.schedule.revision, "pause")
    assert active.emit(10, revision=paused.schedule.revision) == JobInactive(
        reason="the check-in schedule is no longer active"
    )
    assert active.notices.recorded == []


def test_a_notice_that_cannot_be_recorded_stays_pending_and_blocks_older_slots(active):
    active.notices.accept = False
    with pytest.raises(HeinzelError, match="could not be recorded"):
        active.emit(10)
    assert active.checkpoint()["pending"]["slot"].startswith("2026-10-25T00:10")
    assert active.emit(5) == JobInactive(reason="an earlier compiled check-in cannot move the durable slot backwards")
    active.notices.accept = True
    result = active.emit(20)
    assert result["slot"] == (AT + timedelta(minutes=10)).isoformat()
    assert active.checkpoint()["pending"] is None
    assert slots(active) == [(AT + timedelta(minutes=10)).isoformat()] * 2


def test_receipt_cleanup_failures_are_retried_before_the_next_slot(active):
    active.notices.forget = False
    with pytest.raises(HeinzelError, match="receipt cleanup is pending"):
        active.emit(10)
    saved = active.checkpoint()
    assert saved["cleanup_key"] is not None
    assert saved["pending"] is None
    with pytest.raises(HeinzelError, match="could not be compacted"):
        active.emit(15)
    assert len(active.notices.recorded) == 1
    active.notices.forget = True
    assert active.emit(15)["notice"] is True
    assert active.checkpoint()["cleanup_key"] is None
    assert len(active.notices.recorded) == 2


def test_a_checkpoint_ahead_of_the_journal_is_refused(active):
    active.write_checkpoint({"version": 1, "journal_seq": 1_000_000})
    with pytest.raises(HeinzelError, match="moved behind its delivery checkpoint"):
        active.emit(10)
    assert active.notices.recorded == []


def _pause_and_resume(active):
    paused = active.service.schedule_control(active.session, "pause-1", active.schedule.revision, "pause")
    proof = _issue_test_approval(
        active.repository,
        active.current,
        active.session,
        "resume-1",
        paused.schedule.revision,
        "resume",
        paused.schedule.preview_hash,
    )
    active.schedule = active.service.schedule_control(
        active.session,
        "resume-1",
        paused.schedule.revision,
        "resume",
        proof,
        paused.schedule.contract_version,
        paused.schedule.preview_hash,
    ).schedule


def test_a_slot_left_pending_across_a_pause_is_settled_without_a_second_post(active):
    active.notices.accept = False
    with pytest.raises(HeinzelError, match="could not be recorded"):
        active.emit(10)
    _pause_and_resume(active)
    active.notices.accept = True
    active.notices.seen = True
    assert active.emit(20)["notice"] is True
    assert slots(active) == [(AT + timedelta(minutes=m)).isoformat() for m in (10, 20)]
    assert active.emit(10)["notice"] is True
    assert len(active.notices.recorded) == 2
    assert active.checkpoint()["pending"] is None


def test_a_stale_slot_that_was_never_recorded_is_dropped_and_an_unknown_one_blocks(active):
    active.notices.accept = False
    with pytest.raises(HeinzelError, match="could not be recorded"):
        active.emit(10)
    _pause_and_resume(active)
    active.notices.accept = True
    active.notices.has_once = lambda _key: None
    with pytest.raises(HeinzelError, match="cannot be inspected safely"):
        active.emit(20)
    assert active.checkpoint()["pending"] is not None
    active.notices.has_once = lambda _key: False
    assert active.emit(20)["slot"] == (AT + timedelta(minutes=20)).isoformat()
    assert active.checkpoint()["last_completed"].startswith("2026-10-25T00:20")


def test_a_recorded_stale_slot_whose_receipt_cannot_be_compacted_blocks_the_next_slot(active):
    active.notices.accept = False
    with pytest.raises(HeinzelError, match="could not be recorded"):
        active.emit(10)
    _pause_and_resume(active)
    active.notices.accept = True
    active.notices.seen = True
    active.notices.forget = False
    with pytest.raises(HeinzelError, match="prior check-in is recorded"):
        active.emit(20)
    assert len(active.notices.recorded) == 1
    assert active.checkpoint()["cleanup_key"] is not None


def _legacy_intent(active, key, minutes, notice_key):
    slot = (AT + timedelta(minutes=minutes)).isoformat()
    active.append(
        "check-in.intent",
        key,
        {
            "dedup_version": 1,
            "slot": slot,
            "contract_version": active.schedule.contract_version,
            "notice_key": notice_key,
            "title": "Ada checked in",
            "body": "Your scheduled five-minute check-in is due.",
            "route": "/heinzel",
            "command": "hz heinzel sessions",
        },
    )


def test_a_completed_legacy_check_in_becomes_the_watermark_and_is_compacted(active):
    _legacy_intent(active, "slot-a", 10, "legacy-key")
    active.append("check-in.recorded", "slot-a", {"notice_key": "legacy-key"})
    assert active.emit(10)["notice"] is True
    assert active.notices.recorded == []
    assert {"legacy-key"} in active.notices.forgotten
    assert active.checkpoint()["last_completed"].startswith("2026-10-25T00:10")
    assert active.emit(15)["notice"] is True
    assert len(active.notices.recorded) == 1


def test_a_legacy_receipt_that_cannot_be_compacted_blocks_the_slot_and_retries(active):
    _legacy_intent(active, "slot-a", 10, "legacy-key")
    active.append("check-in.recorded", "slot-a", {"notice_key": "legacy-key"})
    active.notices.forget = False
    with pytest.raises(HeinzelError, match="Legacy completed check-in receipts could not be compacted"):
        active.emit(15)
    assert active.checkpoint() is None
    active.notices.forget = True
    assert active.emit(15)["notice"] is True


def test_an_unresolved_legacy_intent_is_delivered_before_any_newer_slot(active):
    _legacy_intent(active, "slot-a", 10, "legacy-pending")
    result = active.emit(20)
    assert result["slot"] == (AT + timedelta(minutes=10)).isoformat()
    [(key, title, _body, route, _command, _helper)] = active.notices.recorded
    assert (key, title, route) == ("legacy-pending", "Ada checked in", "/heinzel")


def test_a_legacy_intent_the_bounded_outbox_cannot_adopt_is_refused(active):
    _legacy_intent(active, "slot-a", 10, "legacy-a")
    active.append("check-in.recorded", "slot-a", {"notice_key": "legacy-a"})
    _legacy_intent(active, "slot-b", 5, "legacy-b")
    with pytest.raises(HeinzelError, match="predates its completion watermark"):
        active.emit(20)
    assert active.notices.recorded == []
