"""An installed uv tool retains authenticated web identity through replacement, exec and crash recovery."""

import json
import subprocess
from urllib.parse import urlsplit

import httpx
import pytest

from heinzel.modules.ui._profile import default_profile
from tests.e2e._ui_service import RunningService, startup_diagnostics
from tests.e2e.tool_install_test import sandbox, seed_uv_cache, wheel
from tests.e2e.web_ui_test import REPO, _browser_ready
from tests.integration.ui_service_test import service, service_factory

__all__ = ["sandbox", "seed_uv_cache", "service", "service_factory", "wheel"]

pytestmark = pytest.mark.e2e


@pytest.fixture
def supervised(service, tmp_path, sandbox, wheel):
    service, manager, port = service
    sandbox.run("uv", "tool", "install", "--offline", wheel)
    service.executable = sandbox.hz_bin
    service.prefix = sandbox.root / "tools/heinzel"
    config = tmp_path / "selected-config/config.toml"
    config.parent.mkdir(mode=0o700)
    config.write_text(
        '[hz]\nfeatures = ["persistent_web"]\nupdate_check = false\nauto_update = false\nplugins_enabled = false\n'
        "[system.notify]\nnative = false\n"
    )
    service.profile = default_profile(service.home, service.directory.parent, service.prefix, service.executable)
    service.profile.update(
        {
            "HZ_CONFIG_HOME": str(config.parent),
            "HZ_CONFIG": str(config),
            "HZ_DATA_DIR": str(tmp_path / "data"),
            "HZ_CACHE_DIR": str(tmp_path / "cache"),
            "HZ_LOG_DIR": str(tmp_path / "logs"),
        }
    )
    service.install(port)
    startup_diagnostics(service.prefix, service.directory)
    foreign = tmp_path / "foreign-config.toml"
    foreign.write_text("[hz]\nfeatures = []\n")
    environment = {
        "HOME": str(tmp_path / "foreign-home"),
        "HZ_STATE_DIR": str(tmp_path / "foreign-state"),
        "HZ_DATA_DIR": str(tmp_path / "foreign-data"),
        "HZ_CONFIG": str(foreign),
        "HZ_CONFIG_HOME": str(tmp_path / "foreign-config"),
        "HZ_FEATURES": "poison-manager-feature",
        "HZ_SYSTEM_NOTIFY__NATIVE": "true",
        "OPENAI_API_KEY": "fixture-process-secret-must-not-propagate",
        "PYTHONPATH": str(tmp_path / "poison-python"),
        "PATH": "/usr/bin:/bin",
    }
    command = ["/bin/sh", str(service.launcher)]
    running = RunningService(service, manager, command, environment)
    try:
        yield service, running
    finally:
        running.close()


def session_headers(url):
    return {"Authorization": "Bearer " + urlsplit(url).fragment.removeprefix("token=")}


@pytest.mark.timeout(900)
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_installed_server_restarts_from_settings_and_reconnects_the_selected_profile(supervised, tmp_path, engine):
    service, running = supervised
    run = _browser_ready(engine)
    process = running.launch()
    first = running.ready()
    driven = subprocess.run(
        [*run, "exec", "node", "e2e/web-server-restart.mjs", engine, str(tmp_path / "screenshots")],
        input=service.open_local(),
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
        timeout=200,
    )
    assert driven.returncode == 0, driven.stderr
    assert json.loads(driven.stdout.strip().splitlines()[-1]) == {
        "replaced": True,
        "profilePreserved": True,
        "pagePreserved": True,
        "restarts": 1,
    }
    second = running.ready(first.instance)
    assert second.pid == first.pid == process.pid
    assert second.port == first.port


def test_installed_server_keeps_session_on_update_exec_and_pairing_after_cold_restart(supervised, sandbox, wheel):
    service, running = supervised
    process = running.launch()
    first = running.ready()
    url = service.open_local()
    origin = f"http://127.0.0.1:{service.status().port}"
    with httpx.Client(timeout=2) as client:
        configured = client.get(
            origin + "/api/system/config/get",
            params={"key": "hz.state_dir"},
            headers=session_headers(url),
        )
        assert configured.status_code == 200, configured.text
        assert configured.json()["value"] == str(service.directory.parent)
        native = client.get(
            origin + "/api/system/config/get", params={"key": "system.notify.native"}, headers=session_headers(url)
        )
        assert native.status_code == 200, native.text
        assert native.json()["value"] is False
        assert client.get(origin + "/api/lifecycle").status_code == 401
        assert client.get(origin + "/api/lifecycle", headers=session_headers(url)).json()["instance"] == first.instance
        paired = client.post(
            origin + "/api/_web/device", headers=session_headers(url), json={"name": "fixture browser"}
        )
        assert paired.status_code == 200
        device = {"Authorization": "Bearer " + paired.json()["token"]}
        assert client.get(origin + "/api/lifecycle", headers=device).status_code == 200
        pending = service.directory.parent / "daemon/mutation-pending.json"
        pending.parent.mkdir(parents=True, exist_ok=True)
        pending.write_text("pending fixture mutation")
        try:
            refused = client.post(origin + "/api/restart", headers=session_headers(url))
            assert refused.status_code == 400
            assert client.get(origin + "/api/lifecycle", headers=device).json()["instance"] == first.instance
        finally:
            pending.unlink()
        sandbox.run("uv", "tool", "install", "--force", "--offline", wheel)
        service.restart()
        second = running.ready(first.instance)
        assert second.pid == first.pid == process.pid
        assert service.open_local() == url
        assert client.get(origin + "/api/lifecycle", headers=session_headers(url)).json()["instance"] == second.instance
        process.kill()
        process.communicate(timeout=3)
        process = running.launch()
        third = running.ready(second.instance)
        assert third.pid == process.pid
        assert service.open_local() != url
        assert client.get(origin + "/api/lifecycle", headers=session_headers(url)).status_code == 401
        assert client.get(origin + "/api/lifecycle", headers=device).json()["instance"] == third.instance
        revoked = client.delete(origin + "/api/_web/device", headers=device)
        assert revoked.status_code == 200
        assert client.get(origin + "/api/lifecycle", headers=device).status_code == 401


@pytest.mark.parametrize("_cold_start", range(5))
def test_fresh_installed_server_repeatedly_publishes_authenticated_control(supervised, _cold_start):
    service, running = supervised
    process = running.launch()
    current = running.ready()
    assert current.pid == process.pid
    origin = f"http://127.0.0.1:{current.port}"
    with httpx.Client(timeout=2) as client:
        unauthenticated = client.get(origin + "/api/lifecycle")
        assert unauthenticated.status_code == 401
        headers = session_headers(service.open_local())
        authenticated = client.get(origin + "/api/lifecycle", headers=headers)
        assert authenticated.status_code == 200
        assert authenticated.json()["instance"] == current.instance
