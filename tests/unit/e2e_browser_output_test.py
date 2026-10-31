"""Browser fixture failures do not render a token-bearing command."""

import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("scenario", ["failure", "guarded-timeout", "run-timeout"])
def test_browser_failure_console_does_not_render_token(tmp_path, scenario):
    probe = tmp_path / "test_probe.py"
    probe.write_text(
        "import os, subprocess, sys\n"
        "from tests.e2e._browser_result import check_browser, wait_browser, run_browser, require_browser\n"
        "def test_failure():\n"
        " token = os.environ['HZ_FIXTURE_SECRET']\n"
        " mode = os.environ['HZ_FIXTURE_MODE']\n"
        " if mode == 'failure':\n"
        "  check_browser(1, ('Authorization: Bearer '+token).replace(token, '<redacted-session>'))\n"
        " if mode == 'guarded-timeout':\n"
        "  class Guard:\n"
        "   def wait(self, timeout): raise subprocess.TimeoutExpired([token], timeout)\n"
        "  require_browser(wait_browser(Guard(), 1))\n"
        " if mode == 'run-timeout':\n"
        "  result = run_browser([sys.executable, '-c', 'import time; time.sleep(3)', token], timeout=.05)\n"
        "  require_browser(result)\n"
    )
    sentinel = secrets.token_urlsafe(24)
    env = {
        "PATH": os.defpath,
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "HZ_FIXTURE_SECRET": sentinel,
        "HZ_FIXTURE_MODE": scenario,
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--noconftest", str(probe)],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode != 0
    assert sentinel not in result.stdout + result.stderr
    expected = "Authorization: Bearer <redacted-session>" if scenario == "failure" else "The browser fixture timed out."
    assert re.search(rf"(?m)^E\s+AssertionError: {re.escape(expected)}$", result.stdout)
