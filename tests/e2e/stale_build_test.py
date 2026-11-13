"""A version bump under an open page reloads it safely and keeps unsent text, on phones (web/e2e/stale-build.mjs)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e
REPO = Path(__file__).resolve().parents[2]


@pytest.mark.timeout(600)
def test_a_stale_page_reloads_itself_in_chromium_and_webkit():
    run = [sys.executable, str(REPO / "scripts" / "web_build.py")]
    subprocess.run(
        [*run, "exec", "playwright-core", "install", "chromium", "webkit"],
        cwd=REPO,
        capture_output=True,
        check=True,
        timeout=600,
    )
    result = subprocess.run(
        [*run, "exec", "node", "e2e/stale-build.mjs"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == [
        {"browser": engine, "scenario": scenario, "reloads": 1}
        for engine in ("chromium", "webkit")
        for scenario in ("idle", "draft")
    ]
