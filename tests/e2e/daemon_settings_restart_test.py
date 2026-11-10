"""Settings restart through the real stream, with a disposable service manager."""

import json
import os
from datetime import timedelta
from urllib.parse import urlsplit

import pytest

from heinzel.modules.system.daemon import DaemonService
from tests.e2e._browser_result import check_browser, require_browser, run_browser
from tests.e2e._mounted import MountedServer
from tests.e2e.daemon_install_ui_test import ROOT, _fake_service
from tests.e2e.durable_five_minute_journey_test import BROWSER_CACHE, web_build

pytestmark = pytest.mark.e2e


@pytest.mark.timeout(120)
def test_settings_restart_retains_real_failed_steps(make_kernel, tmp_path, monkeypatch):
    assert web_build.current(), "build the exact candidate web sources before this test"
    assert web_build.installed(), "install the locked web dependencies before this test"
    monkeypatch.delenv("HEINZEL_UI_RESUME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(BROWSER_CACHE))
    for name in ("CONFIG", "DATA", "STATE", "CACHE"):
        monkeypatch.setenv(f"XDG_{name}_HOME", str(tmp_path / name.lower()))
    (tmp_path / "home").mkdir()
    kernel, manager, release, _ = _fake_service(make_kernel, tmp_path, "Aqua")
    release.touch()
    service = kernel.resolve(DaemonService)
    service.install(timedelta(minutes=5))
    manager.codes["bootstrap"] = 5
    with MountedServer(kernel, monkeypatch) as server:
        address = urlsplit(server.url)
        if address.hostname != "127.0.0.1" or address.port in (None, 8737):
            msg = "Mounted server did not select an isolated loopback port"
            raise AssertionError(msg)
        run = require_browser(
            run_browser(
                [str(web_build.node_bin() / "node"), "e2e/daemon-settings-restart.mjs", server.url],
                cwd=ROOT / "web",
                env=os.environ.copy(),
                capture_output=True,
                text=True,
                timeout=90,
            )
        )
        check_browser(run.returncode, (run.stderr + run.stdout).replace(server.token, "<redacted>"))
        assert json.loads(run.stdout.strip().splitlines()[-1]) == {"posts": 1, "failed": True}
    # Installation, then restart bootout and failed load, then the attempted recovery load.
    assert manager.mutations == ["bootstrap", "bootout", "bootstrap", "bootstrap"]
