"""Exercise durable composer recovery in Chromium against an isolated, mounted `hz ui` instance."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("web_build", REPO / "scripts" / "web_build.py")
assert spec
assert spec.loader
web_build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web_build)


@pytest.mark.timeout(600)
def test_durable_composer_survives_update_reload_and_stays_session_scoped(tmp_path):
    """Mock package/restart responses, but run the actual update UI and browser reload in a disposable profile."""
    web_build.ensure_built()
    run = [sys.executable, str(REPO / "scripts" / "web_build.py")]
    subprocess.run(
        [*run, "exec", "playwright-core", "install", "chromium"],
        cwd=REPO,
        check=True,
        timeout=600,
        capture_output=True,
    )
    user_dir = tmp_path / "workflows"
    user_dir.mkdir()
    env = {key: value for key, value in os.environ.items() if not key.startswith("HZ_")}
    env |= {
        "HZ_CONFIG_HOME": str(tmp_path / "config"),
        "HZ_DATA_DIR": str(tmp_path / "data"),
        "HZ_CACHE_DIR": str(tmp_path / "cache"),
        "HZ_STATE_DIR": str(tmp_path / "state"),
        "HZ_LOG_DIR": str(tmp_path / "log"),
        "HZ_SYSTEM_DAEMON__UNIT_DIR": str(tmp_path / "units"),
        "HZ_SYSTEM_CREDENTIAL__BACKEND": "environment",
        "HZ_PLUGINS_ENABLED": "false",
        "HZ_HEINZEL__USER_DIR": str(user_dir),
        "HZ_ORG__HANDLE": "mira",
        "HZ_ORG__ROLE": "engineering-manager",
        "HZ_ORG__TIMEZONE": "Europe/Berlin",
        "HZ_ORG__NAME": "Mira Example",
        "HZ_ORG__EMAIL": "mira@example.com",
    }
    ui = subprocess.Popen(
        ["uv", "run", "hz", "ui", "--port", "0"], cwd=REPO, env=env, stderr=subprocess.PIPE, text=True
    )
    try:
        assert ui.stderr is not None
        for line in ui.stderr:
            if "runs at" in line:
                url = line.strip().rsplit(" ", 1)[-1]
                break
        else:
            pytest.fail("The isolated web head exited before printing its session URL")
        assert url.startswith("http://127.0.0.1:")
        driven = subprocess.run(
            [*run, "exec", "node", "e2e/durable-composer.mjs", url],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        assert driven.returncode == 0, f"{driven.stderr}\n{driven.stdout}"
        assert '"updateReload":"verified"' in driven.stdout
    finally:
        ui.terminate()
        ui.wait(30)
        if ui.stderr is not None:
            ui.stderr.close()
