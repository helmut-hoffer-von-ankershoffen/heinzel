"""Mounted private skill review at phone widths; all API responses are local mocks."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e
REPO = Path(__file__).resolve().parents[2]


@pytest.mark.timeout(600)
def test_private_skill_review_mobile_chromium_and_webkit():
    run = [sys.executable, str(REPO / "scripts" / "web_build.py")]
    subprocess.run(
        [*run, "exec", "playwright-core", "install", "chromium", "webkit"],
        cwd=REPO,
        capture_output=True,
        check=True,
        timeout=600,
    )
    result = subprocess.run(
        [*run, "exec", "node", "e2e/private-skill-mobile.mjs"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == [
        {"browser": engine, "width": width} for engine in ("chromium", "webkit") for width in (320, 390)
    ]
