"""An installed uv tool retains authenticated web identity through replacement, exec and crash recovery."""

import contextlib
import subprocess
import threading
import time
from urllib.parse import urlsplit

import httpx
import pytest

pytest_plugins = ("tests.e2e.tool_install_test", "tests.integration.ui_service_test")
pytestmark = pytest.mark.e2e


class RunningService:
    """Only the processes this fixture creates may be killed or joined."""

    def __init__(self, service, manager, command, environment):
        self.service, self.manager = service, manager
        self.command, self.environment = command, environment
        self.processes = []

    def launch(self):
        process = subprocess.Popen(self.command, env=self.environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.processes.append(process)
        self.manager.pid = process.pid
        return process

    def ready(self, previous=None):
        deadline = time.monotonic() + 6
        changed = threading.Event()
        while time.monotonic() < deadline:
            status = self.service.status()
            if status.ready and status.instance != previous:
                return status
            assert self.processes[-1].poll() is None, self.processes[-1].communicate(timeout=1)
            changed.wait(0.02)
        pytest.fail("The owned web server did not become ready with a new lifecycle instance.")

    def close(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                output, errors = process.communicate(timeout=3)
                assert b"#token=" not in output + errors
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=3)


@pytest.fixture
def supervised(service, tmp_path, sandbox, wheel):
    service, manager, port = service
    sandbox.run("uv", "tool", "install", "--offline", wheel)
    service.executable = sandbox.hz_bin
    service.prefix = sandbox.root / "tools/heinzel"
    service.install(port)
    directories = {
        "HZ_STATE_DIR": str(service.directory.parent),
        "HZ_DATA_DIR": str(tmp_path / "data"),
        "HZ_CACHE_DIR": str(tmp_path / "cache"),
        "HZ_LOG_DIR": str(tmp_path / "logs"),
        "HZ_CONFIG_HOME": str(tmp_path / "config"),
    }
    environment = {key: value for key, value in sandbox.env.items() if not key.startswith(("HZ_", "HEINZEL_"))}
    config = tmp_path / "conflicting.toml"
    config.write_text('[hz]\nstate_dir = "' + str(tmp_path / "foreign-config-state") + '"\n')
    environment.update(
        directories
        | {
            "HZ_STATE_DIR": str(tmp_path / "foreign-environment-state"),
            "HZ_CONFIG": str(config),
            "HZ_UPDATE_CHECK": "false",
            "HZ_PLUGINS_ENABLED": "false",
            "HZ_TEST": "1",
            "HZ_FEATURES": "persistent_web",
            "PATH": "/usr/bin:/bin",
        }
    )
    command = ["/bin/sh", str(service.launcher)]
    running = RunningService(service, manager, command, environment)
    try:
        yield service, running
    finally:
        running.close()


def session_headers(url):
    return {"Authorization": "Bearer " + urlsplit(url).fragment.removeprefix("token=")}


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
