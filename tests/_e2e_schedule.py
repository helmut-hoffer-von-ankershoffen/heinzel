"""The E2E scheduling hint for the known three-worker xdist load scheduler, as a pytest plugin of its own.

The root conftest loads it; tests/integration/e2e_early_schedule_test.py copies this file alone into its nested run, so
the three nested workers start without importing heinzel (bead 0bb).
"""

from importlib.metadata import PackageNotFoundError, version
from typing import Protocol

import pytest


def _spread_early_e2e(items: list[pytest.Item], workers: int) -> list[pytest.Item]:
    """Place one hinted test per known xdist initial batch, preserving all other order."""
    if workers != 3 or len(items) < 2 * workers:
        return list(items)
    early = [item for item in items if item.get_closest_marker("e2e_early")]
    if not early:
        return list(items)
    regular = [item for item in items if not item.get_closest_marker("e2e_early")]
    batch = max(2, (len(items) // workers) // 4)
    result: list[pytest.Item] = []
    for _ in range(workers):
        capacity = batch
        if early:
            result.append(early.pop(0))
            capacity -= 1
        take = min(capacity, len(regular))
        result.extend(regular[:take])
        del regular[:take]
    return result + early + regular


class _WorkerNode(Protocol):
    config: pytest.Config
    workerinput: dict[str, object]


def _early_scheduler(config: pytest.Config) -> bool:
    if (
        config.getoption("markexpr") != "e2e"
        or getattr(config.option, "numprocesses", None) != 3
        or getattr(config.option, "dist", None) != "load"
        or getattr(config.option, "maxschedchunk", None) is not None
        or getattr(config.option, "maxprocesses", None) is not None
    ):
        return False
    try:
        return version("pytest-xdist") == "3.8.0"
    except PackageNotFoundError:
        return False


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node: _WorkerNode) -> None:
    """Propagate the checked controller scheduler choice to each xdist worker."""
    node.workerinput["heinzel_e2e_early"] = _early_scheduler(node.config)


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Use E2E hints only for the known three-worker xdist load scheduler."""
    workerinput = getattr(config, "workerinput", None)
    if isinstance(workerinput, dict):
        enabled = workerinput.get("heinzel_e2e_early") is True and workerinput.get("workercount") == 3
    else:
        enabled = _early_scheduler(config)
    if enabled:
        items[:] = _spread_early_e2e(items, 3)
