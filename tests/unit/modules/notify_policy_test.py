"""The notification policy (ADR 0041): working hours, quiet mode, the rate limit and repeats, with a fixed clock."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from heinzel import Notice, NoticeKind
from heinzel.modules.system.notify._models import NotifySettings
from heinzel.modules.system.notify._policy import Hours, decide, next_start, repeated, working_hours

pytestmark = pytest.mark.unit

BERLIN = ZoneInfo("Europe/Berlin")
HOURS = Hours(start=9, end=18, zone=BERLIN)
WEDNESDAY_NOON = datetime(2026, 10, 21, 12, 0, tzinfo=BERLIN)


def settings(**values: object) -> NotifySettings:
    return NotifySettings.model_validate(values)


def notice(at: datetime, *, key: str = "k", popup: bool = True) -> Notice:
    return Notice(
        id=f"{at:%H%M%S}",
        kind=NoticeKind.DRAFT,
        title="Draft ready",
        body="",
        route="/",
        command="hz heinzel drafts",
        key=key,
        at=at,
        due=at,
        popup=popup,
    )


def test_working_hours_come_from_the_profile_with_defaults():
    assert working_hours({"working_hours": "08-17", "timezone": "Europe/Berlin"}, UTC) == Hours(8, 17, BERLIN)
    assert working_hours({"working_hours": [7, 15]}, UTC) == Hours(7, 15, UTC)
    assert working_hours({"working_hours": "[10, 19]"}, UTC) == Hours(10, 19, UTC)
    for broken in ({}, {"working_hours": "late"}, {"working_hours": [18, 9]}, {"working_hours": [True, 5]}):
        assert working_hours(broken, UTC) == Hours(9, 18, UTC)
    assert working_hours({"timezone": "Mars/Olympus"}, UTC).zone is UTC


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (WEDNESDAY_NOON, None),  # working
        (WEDNESDAY_NOON.replace(hour=9), None),  # the first minute counts
        (WEDNESDAY_NOON.replace(hour=7), WEDNESDAY_NOON.replace(hour=9)),  # before work: today
        (WEDNESDAY_NOON.replace(hour=18), datetime(2026, 10, 22, 9, tzinfo=BERLIN)),  # after work: tomorrow
        (datetime(2026, 10, 23, 20, tzinfo=BERLIN), datetime(2026, 10, 26, 9, tzinfo=BERLIN)),  # Friday night: Monday
        (datetime(2026, 10, 24, 11, tzinfo=BERLIN), datetime(2026, 10, 26, 9, tzinfo=BERLIN)),  # Saturday: Monday
        (datetime(2026, 10, 26, 6, tzinfo=UTC), datetime(2026, 10, 26, 9, tzinfo=BERLIN)),  # another zone's clock
    ],
)
def test_the_next_start_of_working_hours(now, expected):
    assert next_start(HOURS, now) == expected


def test_a_notice_pops_up_now_during_working_hours():
    decision = decide(settings(), HOURS, WEDNESDAY_NOON, urgent=False, recent=[])
    assert (decision.popup, decision.held, decision.due) == (True, None, WEDNESDAY_NOON)


def test_outside_working_hours_a_notice_waits_for_them_unless_it_is_urgent():
    evening = WEDNESDAY_NOON.replace(hour=21)
    waiting = decide(settings(), HOURS, evening, urgent=False, recent=[])
    assert (waiting.popup, waiting.due) == (True, datetime(2026, 10, 22, 9, tzinfo=BERLIN))
    assert decide(settings(), HOURS, evening, urgent=True, recent=[]).due == evening
    assert decide(settings(working_hours=False), HOURS, evening, urgent=False, recent=[]).due == evening


def test_quiet_mode_holds_everything_urgent_notices_too():
    decision = decide(settings(quiet=True), HOURS, WEDNESDAY_NOON, urgent=True, recent=[])
    assert decision.popup is False
    assert decision.held is not None
    assert "quiet" in decision.held


def test_the_rate_limit_counts_the_pop_ups_of_the_last_hour():
    recent = [notice(WEDNESDAY_NOON - timedelta(minutes=m)) for m in (1, 10, 59)]
    held = decide(settings(per_hour=3), HOURS, WEDNESDAY_NOON, urgent=True, recent=recent)
    assert held.popup is False
    assert held.held is not None
    assert "per_hour" in held.held
    older = [*recent[:2], notice(WEDNESDAY_NOON - timedelta(minutes=61))]
    assert decide(settings(per_hour=3), HOURS, WEDNESDAY_NOON, urgent=False, recent=older).popup is True
    in_the_list_only = [notice(WEDNESDAY_NOON, popup=False)] * 5
    assert decide(settings(per_hour=3), HOURS, WEDNESDAY_NOON, urgent=False, recent=in_the_list_only).popup is True


def test_the_same_notice_within_the_window_is_a_repeat():
    earlier = notice(WEDNESDAY_NOON - timedelta(hours=2), key="draft:me:weekly")
    window = timedelta(hours=24)
    assert repeated([earlier], "draft:me:weekly", WEDNESDAY_NOON, window) == earlier
    assert repeated([earlier], "draft:me:other", WEDNESDAY_NOON, window) is None
    assert repeated([earlier], "draft:me:weekly", WEDNESDAY_NOON, timedelta(hours=1)) is None


def test_kinds_are_switched_off_one_by_one_or_all_at_once():
    assert settings().wants(NoticeKind.DRAFT)
    assert not settings().wants(NoticeKind.RELEASE)  # off unless asked for
    assert not settings(draft=False).wants(NoticeKind.DRAFT)
    assert settings(draft=False).wants(NoticeKind.TEST)
    assert not settings(enabled=False).wants(NoticeKind.TEST)
