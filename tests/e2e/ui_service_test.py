"""An installed uv tool retains authenticated web identity through replacement, exec and crash recovery."""

import json
import signal
import subprocess
import sys
from urllib.parse import urlsplit

import httpx
import pytest

from heinzel.modules.system.lifecycle._profile import default_profile
from heinzel.modules.system.lifecycle._recovery_launcher import interpreter
from tests.e2e._ui_service import RunningService, startup_diagnostics
from tests.e2e.tool_install_test import sandbox, seed_uv_cache, wheel
from tests.integration.ui_service_test import service, service_factory

__all__ = ["sandbox", "seed_uv_cache", "service", "service_factory", "wheel"]

pytestmark = pytest.mark.e2e

_INTERRUPT_INSTALLED_UPDATE = """
import json
import os
import signal
import sys
from pathlib import Path

from heinzel import Progress
from heinzel.kernel import Installation, private_lock, private_write
from heinzel.modules.system.daemon._abandoned_mutation import host_boot, mutation_record
from heinzel.modules.system.lifecycle._service import WebService
from heinzel.modules.system.updater._adapter_state import FileInstallationState
from heinzel.modules.system.updater._announced import ToolSnapshot
from heinzel.modules.system.updater._recovery_staging import supervised_recovery

state, prefix = (Path(value).resolve() for value in sys.argv[1:])
installation = Installation(Progress(), prefix=prefix)
installation.record_startup_state(expected=installation.startup_fingerprint())
_, binary = installation.tool_destination()
web = WebService(state, Path.home(), binary / "hz", prefix=prefix, uv=prefix / "bin/uv")
with (
    private_lock(state / "web" / "auto-update-apply.lock"),
    supervised_recovery(installation, state / "auto-update", web.recovery_ready),
    FileInstallationState(state).lock("update"),
    private_lock(state / "daemon/lifecycle.lock"),
):
    marker = mutation_record("b" * 64, os.getpid(), host_boot()[0])
    private_write(state / "daemon/mutation-pending.json", json.dumps(marker, indent=2) + "\\n")
    ToolSnapshot.take(prefix, state / "auto-update", content=installation.content_fingerprint())
    (prefix / "bin/hz").unlink()
    os.kill(os.getpid(), signal.SIGKILL)
"""


@pytest.fixture
def supervised_install_options():
    """The cold-start tests exercise uv's default, source-only tool installation."""
    return ()


@pytest.fixture
def supervised(service, tmp_path, sandbox, wheel, supervised_install_options):
    service, manager, port = service
    sandbox.run("uv", "tool", "install", "--offline", *supervised_install_options, wheel)
    service.executable = sandbox.hz_bin
    service.prefix = sandbox.root / "tools/heinzel"
    service.uv = service.prefix / "bin/uv"
    config = tmp_path / "selected-config/config.toml"
    config.parent.mkdir(mode=0o700)
    config.write_text(
        "[hz]\nupdate_check = false\nauto_update = false\nplugins_enabled = false\n[system.notify]\nnative = false\n"
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


def test_installed_supervisor_recovers_its_server_after_the_update_owner_is_killed(supervised, sandbox):
    service, running = supervised
    if service.platform != sys.platform:
        pytest.skip("The installed recovery worker validates the host's own service format")
    assert service.recovery_interpreter() is None
    service.recovery = interpreter(service.prefix)
    assert service.recovery is not None
    port = service.status().port
    assert port is not None
    assert service.install(port).changed
    assert service.recovery_interpreter() == service.recovery
    process = running.launch()
    first = running.ready()
    process.kill()
    process.communicate(timeout=3)
    state = service.directory.parent
    interrupted = subprocess.run(
        [service.prefix / "bin/python", "-I", "-B", "-c", _INTERRUPT_INSTALLED_UPDATE, state, service.prefix],
        env={**sandbox.env, "HOME": str(service.home)},
        capture_output=True,
        check=False,
        timeout=90,
    )
    assert interrupted.returncode == -signal.SIGKILL, interrupted.stderr.decode(errors="replace")
    pending = state / "daemon/mutation-pending.json"
    assert pending.is_file()
    assert not (service.prefix / "bin/hz").exists()
    process = running.launch()
    recovered = running.ready(first.instance)
    assert recovered.pid == process.pid
    assert recovered.version == first.version
    assert not pending.exists()
    assert not (state / "auto-update/before-update").exists()
    sandbox.run(
        service.prefix / "bin/python",
        "-I",
        "-c",
        "from heinzel import Progress; from heinzel.kernel import Installation; "
        "assert Installation(Progress()).startup_state_matches()",
        HOME=str(service.home),
    )
    heartbeat = state / "daemon/heartbeat.json"
    assert not heartbeat.exists()
    tick = json.loads(sandbox.run(sandbox.hz_bin, "system", "daemon", "tick", "--json", **service.profile))
    assert not tick["locked"]
    assert not tick["paused"]
    assert json.loads(heartbeat.read_text())["outcome"] == "completed"


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


def test_installed_ui_group_is_inert_and_default_install_retains_profile_refusal(sandbox, wheel):
    sandbox.run("uv", "tool", "install", "--offline", wheel)
    help_text = sandbox.hz("ui")
    assert "serve" in help_text
    assert "install" in help_text
    assert "status" in help_text
    assert "http://127.0.0.1:" not in help_text
    assert "--supervised" not in help_text
    assert "--port" in sandbox.hz("ui", "serve", "--help")
    refused = subprocess.run(
        [str(sandbox.hz_bin), "ui", "install", "--dry-run", "--json"],
        env=sandbox.env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert refused.returncode == 1
    assert "process-only settings" in refused.stderr
    assert "preview" not in refused.stderr
    assert not (sandbox.root / "state/ui/service.json").exists()
    assert not (sandbox.root / "units").exists()


def test_installed_account_free_complete_profile_has_green_doctor(sandbox, wheel):
    sandbox.run("uv", "tool", "install", "--offline", wheel)
    environment = {**sandbox.env, "HOME": str((sandbox.root / "private-home").resolve())}
    for key, value in {
        "org.name": "Mira",
        "team.name": "Payments",
        "org.handle": "mira",
        "org.role": "engineer",
        "org.timezone": "Europe/Berlin",
        "capabilities.ai_access": "none",
        "capabilities.agent_runner": "none",
    }.items():
        result = subprocess.run(
            [str(sandbox.hz_bin), "system", "config", "set", key, value, "--json"],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
    observed = subprocess.run(
        [str(sandbox.hz_bin), "system", "doctor", "--json"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert observed.returncode == 0, observed.stderr
    report = json.loads(observed.stdout)
    assert report["status"] == "up", json.dumps(report, indent=2)
    assert report["checks"]["ai.access"][0]["detail"].startswith("intentionally not configured")
    assert all(check["status"] == "up" for checks in report["checks"].values() for check in checks)
    assert not (sandbox.root / "units").exists()
