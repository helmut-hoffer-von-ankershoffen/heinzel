"""Quiet hours are a local-time window that may run over midnight."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from heinzel.modules.heinzel._quiet_hours import QuietHours

pytestmark = pytest.mark.unit

NIGHT = QuietHours(start="22:00", end="07:00", timezone="Europe/Berlin")


@pytest.mark.parametrize(
    ("utc", "quiet"),
    [
        (datetime(2026, 7, 1, 19, 59, tzinfo=UTC), False),  # 21:59 in Berlin, summer time
        (datetime(2026, 7, 1, 20, 0, tzinfo=UTC), True),  # 22:00, inclusive
        (datetime(2026, 7, 1, 23, 0, tzinfo=UTC), True),  # after local midnight
        (datetime(2026, 7, 1, 4, 59, tzinfo=UTC), True),  # 06:59
        (datetime(2026, 7, 1, 5, 0, tzinfo=UTC), False),  # 07:00, exclusive
        (datetime(2026, 12, 1, 6, 0, tzinfo=UTC), False),  # 07:00 in winter time
    ],
)
def test_overnight_window_uses_the_window_timezone(utc, quiet):
    assert NIGHT.covers(utc) is quiet


def test_daytime_window():
    lunch = QuietHours(start="12:00", end="13:00", timezone="UTC")
    assert lunch.covers(datetime(2026, 7, 1, 12, 30, tzinfo=UTC))
    assert not lunch.covers(datetime(2026, 7, 1, 13, 0, tzinfo=UTC))


@pytest.mark.parametrize(
    "terms",
    [
        {"start": "24:00", "end": "07:00", "timezone": "UTC"},
        {"start": "7:00", "end": "08:00", "timezone": "UTC"},
        {"start": "07:00", "end": "07:00", "timezone": "UTC"},
        {"start": "22:00", "end": "07:00", "timezone": "Mars/Base"},
    ],
)
def test_invalid_windows_are_refused(terms):
    with pytest.raises(ValidationError):
        QuietHours.model_validate(terms)
