"""World clock opens and scrolls on an emulated phone."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e
REPO = Path(__file__).resolve().parents[2]


@pytest.mark.timeout(600)
def test_clock_mobile_gesture(tmp_path):
    """Open the clock through its header action and scroll to its last location."""
    spec = importlib.util.spec_from_file_location("web_build", REPO / "scripts" / "web_build.py")
    assert spec
    assert spec.loader
    web_build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(web_build)
    web_build.ensure_built()
    run = [sys.executable, str(REPO / "scripts" / "web_build.py")]
    subprocess.run(
        [*run, "exec", "playwright-core", "install", "chromium"],
        cwd=REPO,
        check=True,
        timeout=600,
        capture_output=True,
    )
    env = {key: value for key, value in os.environ.items() if not key.startswith("HZ_")}
    for name in ("CONFIG_HOME", "DATA_DIR", "CACHE_DIR", "STATE_DIR", "LOG_DIR"):
        env[f"HZ_{name}"] = str(tmp_path / name.lower())
    env["HZ_SYSTEM_DAEMON__UNIT_DIR"] = str(tmp_path / "units")
    env["HZ_SYSTEM_CREDENTIAL__BACKEND"] = "environment"
    env["HZ_PLUGINS_ENABLED"] = "false"
    ui = subprocess.Popen(
        ["uv", "run", "hz", "ui", "--no-browser", "--port", "0"],
        cwd=REPO,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert ui.stdout is not None
        line = ui.stdout.readline().strip()
        assert line.startswith("http://127.0.0.1:")
        assert "#token=" in line
        driven = subprocess.run(
            [*run, "exec", "node", "e2e/clock-mobile-gesture.mjs", line],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        assert driven.returncode == 0, driven.stderr
    finally:
        ui.terminate()
        ui.wait(30)
        if ui.stdout is not None:
            ui.stdout.close()
        if ui.stderr is not None:
            ui.stderr.close()
