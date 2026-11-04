"""Calendar reminder boundary and identity planning."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from heinzel import Event
from heinzel.modules.org._reminders import plan

pytestmark = pytest.mark.unit


def event(name, start, end, **fields):
    return Event(id=name, title=name, start=start, end=end, **fields)


def test_current_and_next_ignore_cancelled_all_day_declined_and_choose_overlaps():
    now = datetime(2026, 10, 21, 10, tzinfo=UTC)
    events = [
        event("earlier", now - timedelta(hours=1), now + timedelta(minutes=10)),
        event("current", now - timedelta(minutes=1), now + timedelta(minutes=7)),
        event("next", now + timedelta(minutes=8), now + timedelta(hours=1)),
        event("cancelled", now + timedelta(minutes=1), now + timedelta(hours=1), status="cancelled"),
        event("declined", now + timedelta(minutes=2), now + timedelta(hours=1), response="declined"),
        event("all day", now, now + timedelta(days=1), all_day=True),
    ]
    current, upcoming = plan(events, now)
    assert (current.title, current.kind, current.due, current.expires) == (
        "current",
        "end",
        now + timedelta(minutes=2),
        now + timedelta(minutes=7),
    )
    assert (upcoming.title, upcoming.kind, upcoming.due) == ("next", "start", now + timedelta(minutes=3))
    assert plan(list(reversed(events)), now) == [current, upcoming]


def test_moving_changes_key_and_daylight_saving_uses_actual_instant():
    zone = ZoneInfo("Europe/Berlin")
    now = datetime(2026, 10, 25, 0, 55, tzinfo=UTC)
    start = datetime(2026, 10, 25, 2, 3, tzinfo=zone, fold=1)
    entry = event("meeting", start, start + timedelta(hours=1))
    original = plan([entry], now)[0]
    assert original.due == datetime(2026, 10, 25, 0, 58, tzinfo=UTC)
    moved = event("meeting", start + timedelta(minutes=1), start + timedelta(hours=1))
    assert plan([moved], now)[0].key != original.key
    assert plan([entry.model_copy(update={"status": "cancelled"})], now) == []
