"""Shared fixtures.

heinzel.testing (a pytest plugin, loaded automatically) brings the rest: every test is exactly one of unit,
integration or e2e and gets its level's timeout (docs/CODING_STANDARDS.md#tests), and every test has its own
HZ_CONFIG_HOME, HZ_DATA_DIR, HZ_CACHE_DIR, HZ_STATE_DIR, HZ_LOG_DIR and daemon unit directory, without the developer's
HZ_* variables.
"""

from collections.abc import Callable

import pytest
from heinzel import Kernel, Settings
from heinzel.kernel import discover


@pytest.fixture
def boot() -> Callable[..., Kernel]:
    """Boot a kernel the way the `hz` command does: Heinzelmännchen's built-in modules plus every installed plugin."""

    def make(**settings: object) -> Kernel:
        records, errors = discover()
        return Kernel(Settings(**settings), records, errors)

    return make
