"""Run the mounted daemon and plugin recovery browser contracts in focused CI."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parents[2]
BROWSER_CACHE = Path.home() / ("Library/Caches/ms-playwright" if sys.platform == "darwin" else ".cache/ms-playwright")


@pytest.mark.parametrize("script", ["daemon-restart-proof.mjs", "plugin-recovery-proof.mjs"])
@pytest.mark.timeout(180)
def test_daemon_restart_and_plugin_recovery_browser_contract(script, tmp_path):
    """Exercise actual UI components against isolated synthetic streaming responses."""
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ.copy()
    env.update(HOME=str(home), PLAYWRIGHT_BROWSERS_PATH=str(BROWSER_CACHE))
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/web_build.py"), "exec", "node", f"e2e/{script}"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=150,
        check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


@pytest.mark.timeout(60)
def test_daemon_browser_fixture_request_guard(tmp_path):
    """Keep the browser proof's loopback-only request boundary under test."""
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ.copy()
    env.update(HOME=str(home), PLAYWRIGHT_BROWSERS_PATH=str(BROWSER_CACHE))
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/web_build.py"), "exec", "node", "--test", "e2e/local-fixture.test.mjs"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
