"""Opt-in disposable hosted diagnostic; never execute on the developer's machine."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.timeout(1150)]


def test_legacy_discovery_after_genuine_failure():
    if (
        os.environ.get("GITHUB_WORKFLOW") != "Focused job"
        or os.environ.get("FOCUSED_SELECTOR") != "tests/integration/legacy_discovery_test.py"
    ):
        pytest.skip("explicit focused hosted diagnostic only")
    repo = Path(__file__).resolve().parents[2]
    # Guarding platform, attempt, selection, committed source and environment is the script's first action.
    result = subprocess.run(
        [sys.executable, "-I", str(repo / "scripts/acceptance/hosted_legacy_discovery.py")],
        cwd=repo,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=1140,
        check=False,
    )
    assert result.returncode == 0, "Diagnostic retained its original failure/refusal; inspect the separate artifacts"
