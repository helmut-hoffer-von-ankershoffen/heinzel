from datetime import UTC, datetime

import pytest

from heinzel.modules.system.daemon._schedule import latest, next_slot, problem, slots

pytestmark = pytest.mark.unit


def at(hour: int, minute: int = 0, day: int = 5) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


@pytest.mark.parametrize("schedule", ["0 9 * * MON", "*/5 * * * *", "@daily"])
def test_accepts_five_fields_and_aliases(schedule):
    assert problem(schedule) is None


@pytest.mark.parametrize("schedule", ["0 9 * *", "0 0 9 * * MON", "every monday", "61 * * * *"])
def test_rejects_other_expressions(schedule):
    assert "invalid schedule" in (problem(schedule) or "")


def test_slots_exclude_the_start_and_include_the_end():
    assert slots("0 * * * *", at(9), at(12)) == [at(10), at(11), at(12)]


def test_slots_stop_at_the_limit_oldest_first():
    assert slots("0 * * * *", at(0), at(23), limit=2) == [at(1), at(2)]


def test_latest_includes_a_slot_on_the_end():
    assert latest("0 * * * *", at(9), at(12)) == at(12)
    assert latest("0 * * * *", at(9), at(12, 30)) == at(12)


def test_latest_is_none_without_a_slot_in_the_window():
    assert latest("0 * * * *", at(9), at(9, 30)) is None


def test_next_slot():
    assert next_slot("0 9 * * MON", at(10)) == at(9, day=12)
