"""E2E scheduling hints change order only for the known xdist load policy."""

import pytest

from tests.conftest import _spread_early_e2e

pytestmark = pytest.mark.unit


class _Item(pytest.Item):
    def runtest(self) -> None:
        pass


def test_early_hints_are_spread_over_initial_batches_without_losing_items(request: pytest.FixtureRequest) -> None:
    session = request.session
    items = [_Item.from_parent(session, name=f"case-{index}") for index in range(39)]
    for index in (31, 35, 38):
        items[index].add_marker(pytest.mark.e2e_early)
    result = _spread_early_e2e(items, 3)
    assert len(result) == len(items)
    assert {id(item) for item in result} == {id(item) for item in items}
    assert [result[index].name for index in (0, 3, 6)] == ["case-31", "case-35", "case-38"]
    assert [item.name for item in result if not item.get_closest_marker("e2e_early")] == [
        item.name for item in items if not item.get_closest_marker("e2e_early")
    ]


def test_unknown_worker_counts_and_small_collections_keep_original_order(request: pytest.FixtureRequest) -> None:
    session = request.session
    items = [_Item.from_parent(session, name=f"case-{index}") for index in range(5)]
    items[-1].add_marker(pytest.mark.e2e_early)
    assert _spread_early_e2e(items, 3) == items
    assert _spread_early_e2e(items, 2) == items
