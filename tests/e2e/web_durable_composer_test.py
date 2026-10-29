"""Exercise durable composer recovery in Chromium against an isolated, mounted `hz ui` instance."""

import importlib.util
import os
import select
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

pytestmark = pytest.mark.e2e

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("web_build", REPO / "scripts" / "web_build.py")
assert spec
assert spec.loader
web_build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web_build)


def _startup_url(ui: subprocess.Popen[str]) -> str:
    """Read one bounded startup line without exposing it in timeout diagnostics."""
    assert ui.stdout is not None
    deadline = time.monotonic() + 30
    data = b""
    while b"\n" not in data:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([ui.stdout], [], [], remaining)[0]:
            pytest.fail("The isolated web head did not start in time")
        chunk = os.read(ui.stdout.fileno(), 4096)
        if not chunk or len(data) + len(chunk) > 16384:
            pytest.fail("The isolated web head did not provide a bounded session URL")
        data += chunk
    try:
        return data.split(b"\n", 1)[0].decode("utf-8").strip()
    except UnicodeDecodeError:
        pytest.fail("The isolated web head provided an invalid session URL")


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
        ["uv", "run", "hz", "ui", "--no-browser", "--port", "0"],
        cwd=REPO,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        url = _startup_url(ui)
        if not url.startswith("http://127.0.0.1:"):
            pytest.fail("The isolated web head did not provide a loopback URL")
        token = parse_qs(urlsplit(url).fragment).get("token", [""])[0]
        if not token:
            pytest.fail("The isolated web head did not provide a session token")
        timed_out = False
        try:
            driven = subprocess.run(
                [*run, "exec", "node", "e2e/durable-composer.mjs", url],
                cwd=REPO,
                capture_output=True,
                text=True,
                check=False,
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            timed_out = True
        if timed_out:
            pytest.fail("The isolated composer browser test timed out")
        if driven.returncode:
            diagnostics = f"{driven.stderr}\n{driven.stdout}".replace(token, "<redacted-session>")
            pytest.fail(diagnostics)
        if '"updateReload":"verified"' not in driven.stdout:
            pytest.fail("The isolated composer browser test did not verify reload")
    finally:
        try:
            ui.terminate()
            try:
                ui.wait(30)
            except subprocess.TimeoutExpired:
                ui.kill()
                ui.wait(30)
        finally:
            if ui.stdout is not None:
                ui.stdout.close()
