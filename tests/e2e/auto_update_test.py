"""Automatic updates in a real browser against a fake newer release (ADR 0116).

Nothing is installed and nothing is fetched: `tests/fixtures/auto_update/sitecustomize.py` fakes the release, the
installer and the server's clock in `hz ui` and in the process its restart in place becomes. The test runs its own
`hz ui` on a free port, never the one on 8737.
"""

import importlib.util
import json
import os
import signal
import socket
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("web_build", REPO / "scripts" / "web_build.py")
assert spec is not None
assert spec.loader is not None
web_build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web_build)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.mark.timeout(900)
def test_countdown_postpone_and_apply_with_a_fake_newer_release(tmp_path):
    web_build.ensure_built()
    run = [sys.executable, str(REPO / "scripts" / "web_build.py")]
    subprocess.run(
        [*run, "exec", "playwright-core", "install", "chromium"], cwd=REPO, check=True, timeout=900, capture_output=True
    )
    fake = tmp_path / "fake-release"
    fake.mkdir()
    env = {key: value for key, value in os.environ.items() if not key.startswith("HZ_")}
    env |= {
        "HZ_CONFIG_HOME": str(tmp_path / "config"),
        "HZ_DATA_DIR": str(tmp_path / "data"),
        "HZ_CACHE_DIR": str(tmp_path / "cache"),
        "HZ_STATE_DIR": str(tmp_path / "state"),
        "HZ_LOG_DIR": str(tmp_path / "log"),
        "HZ_SYSTEM_DAEMON__UNIT_DIR": str(tmp_path / "units"),
        "HZ_SYSTEM_CREDENTIAL__BACKEND": "environment",
        # On by default: no HZ_AUTO_UPDATE, so both switches can change it. This hz runs from a checkout under pytest,
        # which is development; the test override lets it drive the real flow all the same.
        "HEINZEL_AUTO_UPDATE_TEST": "1",
        "HZ_UPDATE_CHANNEL": "stable",
        "HZ_E2E_FAKE_RELEASE": str(fake),
        "PYTHONPATH": str(REPO / "tests" / "fixtures" / "auto_update"),
        "BROWSER": "true",
        "HZ_LOG_LEVEL": "INFO",  # the server's automatic-update steps, for the failure report
    }
    port = _free_port()
    server_log = tmp_path / "hz-ui.log"
    log_file = server_log.open("w", encoding="utf-8")
    ui = subprocess.Popen(
        [sys.executable, "-m", "heinzel", "ui", "--port", str(port), "--no-browser"],
        cwd=tmp_path,
        env=env,
        stderr=log_file,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert ui.stdout is not None
        url = ui.stdout.readline().strip()
        assert url.startswith(f"http://127.0.0.1:{port}/#token="), url
        driven = subprocess.run(
            [*run, "exec", "node", "e2e/auto-update.mjs", url, str(fake)],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
            timeout=400,
        )
    finally:
        ui.send_signal(signal.SIGINT)
        try:
            ui.wait(30)
        except subprocess.TimeoutExpired:
            ui.kill()
            ui.wait(30)
        for stream in (ui.stdout, log_file):
            if stream is not None:
                stream.close()
    tail = "\n".join(server_log.read_text(encoding="utf-8", errors="replace").splitlines()[-80:])
    assert driven.returncode == 0, f"{driven.stderr}{driven.stdout}\n--- hz ui (last 80 lines) ---\n{tail}"
    result = json.loads(driven.stdout.strip().splitlines()[-1])
    assert result["footerEnabled"] is True
    assert result["settingsShowsFooterOff"] is True
    assert result["footerShowsSettingsOn"] is True
    assert "hz updates itself in" in result["text"]
    assert "hz 99.0.0, heinzel-example 9.0.0" in result["text"]
    assert result["buttons"] == ["Postpone 1 hour"]
    assert 26 <= result["first"] <= 30  # every window sees the countdown within seconds
    assert result["ticking"] < result["first"]
    assert result["afterReload"] < result["first"]  # the server's countdown, not a fresh one
    assert (result["postponed"], result["postponements"], result["movedBySeconds"]) == ("waiting", 1, 3600)
    assert result["returned"] is True
    assert result["restarted"] is True
    assert "updated automatically" in result["message"]
    assert result["signedIn"] is True
    assert result["after"] == "idle"
    assert (fake / "applied").read_text() == "fake update applied: 99.0.0 {'heinzel-example': '9.0.0'}\n"
