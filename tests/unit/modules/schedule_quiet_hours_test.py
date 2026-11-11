"""Quiet hours use the reviewed zone and half-open local time boundaries."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from heinzel.modules.heinzel._autonomous_schedule import ScheduleQuietHours

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("when", "expected"),
    [
        ("2026-01-01T20:59:59+00:00", False),
        ("2026-01-01T21:00:00+00:00", True),
        ("2026-01-02T05:59:59+00:00", True),
        ("2026-01-02T06:00:00+00:00", False),
    ],
)
def test_overnight_local_boundary(when, expected):
    hours = ScheduleQuietHours(timezone="Europe/Berlin", start="22:00", end="07:00")
    assert hours.contains(datetime.fromisoformat(when)) is expected


@pytest.mark.parametrize("when", ["2026-10-25T00:15:00+00:00", "2026-10-25T01:15:00+00:00"])
def test_both_occurrences_in_the_dst_fold_are_quiet(when):
    hours = ScheduleQuietHours(timezone="Europe/Berlin", start="02:00", end="02:30")
    assert hours.contains(datetime.fromisoformat(when))


@pytest.mark.parametrize(
    "override",
    [{"timezone": "Not/AZone"}, {"start": "7:00"}, {"start": "24:00"}, {"end": "22:00"}],
)
def test_invalid_or_ambiguous_intervals_refuse(override):
    with pytest.raises(ValidationError):
        ScheduleQuietHours.model_validate({"timezone": "UTC", "start": "22:00", "end": "07:00", **override})


def test_naive_instant_refuses():
    hours = ScheduleQuietHours(timezone="UTC", start="22:00", end="07:00")
    with pytest.raises(ValueError, match="time-zone-aware"):
        hours.contains(datetime.fromisoformat("2026-01-01T22:00"))
