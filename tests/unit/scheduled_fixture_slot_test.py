"""Real-clock browser fixture leaves a scheduled turn room before the next slot."""

from datetime import UTC, datetime

import pytest

from tests.e2e.scheduled_connected_support import _wait_for_slot

pytestmark = pytest.mark.unit


def test_slot_wait_only_near_selected_cadence_boundary():
    waits = []
    _wait_for_slot(3600, lambda: datetime(2026, 10, 30, 9, 59, 58, tzinfo=UTC), waits.append)
    assert waits == [2.1]
    waits.clear()
    _wait_for_slot(3600, lambda: datetime(2026, 10, 30, 9, 59, 45, tzinfo=UTC), waits.append)
    assert waits == []
    _wait_for_slot(420, lambda: datetime.fromtimestamp(419, UTC), waits.append)
    assert waits == [1.1]
