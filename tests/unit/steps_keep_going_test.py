"""`STEPS_KEEP_GOING` lets the pre-push hook report every failing step in one pass."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from heinzel.testing import scaled

pytestmark = pytest.mark.unit

_RUNNER = Path(__file__).resolve().parents[2] / "scripts" / "steps.py"


def _run(keep_going: str) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if key != "VERBOSE"}  # CI sets VERBOSE=1
    env["STEPS_KEEP_GOING"] = keep_going
    return subprocess.run(
        [sys.executable, str(_RUNNER), "a=exit 3", "&b=sleep 1", "&c=exit 4", "d=true"],
        capture_output=True,
        text=True,
        env=env,
        timeout=scaled(30),
        check=False,
    )


def test_by_default_the_first_failure_cancels_the_rest():
    result = _run("")
    assert result.returncode == 3
    assert "(cancelled)" in result.stdout
    assert "- d cancelled" in result.stdout


def test_keep_going_runs_every_step_and_reports_each_failure():
    result = _run("1")
    assert result.returncode == 3  # the first failure's code
    assert "✗ a" in result.stdout
    assert "✗ c" in result.stdout
    assert "✓ b" in result.stdout
    assert "cancelled" not in result.stdout
    assert "✓ d" in result.stdout
