"""The separately selected read-only check for a real installed receipt."""

import os
from pathlib import Path

import pytest

from heinzel import Progress
from heinzel.kernel import Installation

pytestmark = pytest.mark.unit


def test_opt_in_current_installed_receipt_target_read_only():
    prefix = os.environ.get("HZ_READONLY_UV_PREFIX")
    if not prefix:
        pytest.skip("Set HZ_READONLY_UV_PREFIX to inspect an installed tool receipt without mutations")
    installation = Installation(Progress(), prefix=Path(prefix), uv="unused")
    target = installation._tool_target()
    assert target["UV_TOOL_DIR"] == str(Path(prefix).resolve(strict=True).parent)
    assert (Path(target["UV_TOOL_BIN_DIR"]) / "hz").resolve(strict=True) == (
        Path(prefix).resolve(strict=True) / "bin" / "hz"
    ).resolve(strict=True)
