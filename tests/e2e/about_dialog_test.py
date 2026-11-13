"""The About dialog in Chromium and WebKit, on a phone and a desktop window (web/e2e/about-dialog.mjs)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e
REPO = Path(__file__).resolve().parents[2]


@pytest.mark.timeout(600)
def test_about_dialog_chromium_and_webkit_phone_and_desktop():
    run = [sys.executable, str(REPO / "scripts" / "web_build.py")]
    subprocess.run(
        [*run, "exec", "playwright-core", "install", "chromium", "webkit"],
        cwd=REPO,
        capture_output=True,
        check=True,
        timeout=600,
    )
    result = subprocess.run(
        [*run, "exec", "node", "e2e/about-dialog.mjs"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == [
        {"browser": engine, "width": width} for engine in ("chromium", "webkit") for width in (375, 1100)
    ]
