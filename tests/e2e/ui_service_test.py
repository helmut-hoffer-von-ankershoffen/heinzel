"""An installed uv tool retains authenticated web identity through replacement, exec and crash recovery."""

import importlib.metadata
import json
import shutil
import signal
import subprocess
import sys
from urllib.parse import urlsplit

import httpx
import pytest

from heinzel import Progress
from heinzel.kernel import Installation, private_write
from heinzel.modules.system.lifecycle._profile import default_profile
from heinzel.modules.system.lifecycle._recovery_launcher import interpreter
from heinzel.testing import scaled
from tests.e2e._ui_service import RunningService, startup_diagnostics
from tests.e2e.tool_install_test import sandbox, seed_uv_cache, wheel
from tests.integration.auto_update_real_uv_test import wheel as fixture_wheel
from tests.integration.ui_service_test import released_launcher, service, service_factory

__all__ = ["sandbox", "seed_uv_cache", "service", "service_factory", "wheel"]

pytestmark = [pytest.mark.e2e, pytest.mark.real_system]

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

_INTERRUPT_REAL_UPDATE = """
import os
import shutil
import signal
import sys
from pathlib import Path

from heinzel import Progress, Settings
from heinzel.kernel import Installation
from heinzel.kernel.install._installation import _run
from heinzel.modules.system.daemon import _installation_lifecycle
from heinzel.modules.system.daemon._models import DaemonSettings
from heinzel.modules.system.daemon._service import DaemonService
from heinzel.modules.system.lifecycle._service import WebService
from heinzel.modules.system.updater._adapter_state import FileInstallationState
from heinzel.modules.system.updater._announced import Announcement
from heinzel.modules.system.updater._update import UpdateService

state, prefix, home, wheels, uv, real_uv = (Path(sys.argv[i]).resolve() for i in (1, 2, 3, 5, 6, 7))
binary = Path(sys.argv[4])  # Keep the owned uv entrypoint path, not its resolved target.
interrupt = sys.argv[8]

if interrupt == 'snapshot':
    copytree = shutil.copytree
    def stop_after_snapshot_entry(source, target, *args, **kwargs):
        result = copytree(source, target, *args, **kwargs)
        if Path(target).parent == state / 'auto-update/before-update.partial':
            os.kill(os.getpid(), signal.SIGKILL)
        return result
    shutil.copytree = stop_after_snapshot_entry

def runner(command, environment=None):
    install = command[1:3] == ['tool', 'install']
    result = _run([*command, '--no-index', '--find-links', str(wheels)] if install else command, environment)
    if install and result.returncode == 0:
        os.kill(os.getpid(), signal.SIGKILL)
    return result

installation = Installation(Progress(), prefix=prefix, uv=str(real_uv), runner=runner)
settings = Settings(state_dir=state, data_dir=state / 'data', cache_dir=state / 'cache', log_dir=state / 'logs')
daemon = DaemonService(None, settings, DaemonSettings(unit_dir=state / 'units'),
                       continuity_fingerprint=installation.startup_fingerprint,
                       quiescence_installation=installation)
web = WebService(state, home, binary, prefix=prefix, uv=uv)
updater = UpdateService(installation, None, lifecycle=_installation_lifecycle(daemon), web=web,
                        state=FileInstallationState(state), home=home)
updater.apply_announced(Announcement('999.0.0', {}), current=installation.installed_core_version(),
                        folder=state / 'auto-update', trial=lambda _: False)
raise AssertionError(f'the updater did not reach the {interrupt} interrupt')
"""

_INTERRUPT_RECOVERY_WORKER = """
import os
import signal
import sys
from pathlib import Path

prefix, state = (Path(value).resolve() for value in sys.argv[1:3])
staged = state / 'supervised-recovery'
snapshot = state / 'auto-update/before-update'
sys.path.append(str(staged / 'site'))
from heinzel.modules.system._auto_recovery_worker import main

rename = Path.rename
def stop_after_restore_entry(source, target):
    result = rename(source, target)
    if source.parent == snapshot and Path(target).parent == prefix:
        os.kill(os.getpid(), signal.SIGKILL)
    return result

Path.rename = stop_after_restore_entry
sys.exit(main())
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


def test_installed_supervisor_recovers_its_server_after_the_update_owner_is_killed(supervised, sandbox, monkeypatch):
    service, running = supervised
    if service.platform != sys.platform:
        pytest.skip("The installed recovery worker validates the host's own service format")
    assert service.recovery_interpreter() is None
    service.recovery = interpreter(service.prefix)
    assert service.recovery is not None
    port = service.status().port
    assert port is not None

    # The isolated manager lets the job run after the transition fences are cleared.
    sleep = service._sleep

    def start_replacement(seconds):
        if not running.processes:
            running.launch()
        sleep(seconds)

    monkeypatch.setattr(service, "_sleep", start_replacement)
    url = service.attach(port)
    assert service.recovery_interpreter() == service.recovery
    process = running.processes[-1]
    first = running.ready()
    response = httpx.get(f"http://127.0.0.1:{port}/api/lifecycle", headers=session_headers(url), timeout=scaled(2))
    assert response.status_code == 200, response.text
    assert response.json()["instance"] == first.instance
    process.kill()
    process.communicate(timeout=scaled(3))
    state = service.directory.parent
    interrupted = subprocess.run(
        [service.prefix / "bin/python", "-I", "-B", "-c", _INTERRUPT_INSTALLED_UPDATE, state, service.prefix],
        env={**sandbox.env, "HOME": str(service.home)},
        capture_output=True,
        check=False,
        timeout=scaled(90),
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


def prepare_historical_recovery(service, sandbox):
    """Set up normal continuity and migrate an independently pinned old launcher."""
    # This fixture's diagnostic .pth executes code at startup, which the real updater correctly refuses.
    [site] = list((service.prefix / "lib").glob("python*/site-packages"))
    for suffix in (".pth", ".py"):
        (site / f"_heinzel_fixture_startup_diagnostics{suffix}").unlink()

    # Keep uv's entrypoint registration but model a normal index requirement, not a local wheel pin.
    receipt = service.prefix / "uv-receipt.toml"
    receipt.write_text(
        '[tool]\nrequirements = [{ name = "heinzel" }]\n'
        f'entrypoints = [{{ name = "hz", install-path = "{sandbox.hz_bin}" }}]\n'
    )
    installation = Installation(Progress(), prefix=service.prefix)
    installation.record_startup_state(expected=installation.startup_fingerprint())
    state = service.directory.parent

    # Recreate the published launcher with its independently pinned renderer, then use the supported migration.
    old = json.loads(service.marker.read_text())
    port = service.status().port
    assert port is not None
    old["launcher"] = released_launcher(service, port, old, "published015")
    private_write(service.launcher, old["launcher"])
    private_write(service.marker, json.dumps(old))
    assert service.historical_record(old)
    service.recovery = interpreter(service.prefix)
    assert service.recovery is not None
    service.install(port)
    assert not service.historical_record(json.loads(service.marker.read_text()))
    assert service.recovery_ready(state)
    return site, installation


@pytest.mark.timeout(300)
@pytest.mark.parametrize("interrupt", ["snapshot", "install", "recovery"])
def test_historical_migration_recovers_interrupted_real_uv_replacement(supervised, sandbox, tmp_path, interrupt):
    service, running = supervised
    if service.platform != sys.platform:
        pytest.skip("The installed recovery worker validates the host's own service format")

    site, installation = prepare_historical_recovery(service, sandbox)
    original = installation.content_fingerprint()
    state = service.directory.parent
    running.launch()
    first = running.ready()
    wheels = tmp_path / "replacement-wheels"
    wheels.mkdir()
    fixture_wheel(wheels, "heinzel", "999.0.0", script="hz")
    real_uv = shutil.which("uv")
    assert real_uv is not None
    interrupted = subprocess.run(
        [
            service.prefix / "bin/python",
            "-I",
            "-B",
            "-c",
            _INTERRUPT_REAL_UPDATE,
            state,
            service.prefix,
            service.home,
            sandbox.hz_bin,
            wheels,
            service.uv,
            real_uv,
            "snapshot" if interrupt == "snapshot" else "install",
        ],
        env={**sandbox.env, "HOME": str(service.home)},
        capture_output=True,
        check=False,
        timeout=scaled(180),
    )
    assert interrupted.returncode == -signal.SIGKILL, interrupted.stderr.decode(errors="replace")
    snapshot = state / "auto-update/before-update"
    partial = snapshot.with_suffix(".partial")
    pending = state / "daemon/mutation-pending.json"
    assert pending.is_file()
    if interrupt == "snapshot":
        assert any(partial.iterdir())
        assert not snapshot.exists()
        assert installation.content_fingerprint() == original
    else:
        [replacement] = importlib.metadata.distributions(path=[str(site)], name="heinzel")
        assert replacement.version == "999.0.0"
        assert snapshot.is_dir()
    running.processes[-1].kill()
    running.processes[-1].communicate(timeout=scaled(3))
    if interrupt == "recovery":
        # Fault-inject the staged worker in its own process without changing its sealed files.
        # The real launcher below must authenticate the same staging and finish the interrupted restore.
        recovering = subprocess.run(
            [service.recovery[0], "-I", "-S", "-B", "-c", _INTERRUPT_RECOVERY_WORKER, service.prefix, state, original],
            env={"HOME": str(service.home)},
            capture_output=True,
            check=False,
            timeout=scaled(90),
        )
        assert recovering.returncode == -signal.SIGKILL, recovering.stderr.decode(errors="replace")
        assert (state / "auto-update/after-update").is_dir()
        assert snapshot.is_dir()
        assert pending.is_file()
    process = running.launch()
    recovered = running.ready(first.instance)
    assert recovered.pid == process.pid
    assert recovered.version == first.version
    assert installation.content_fingerprint() == original
    assert installation.startup_state_matches()
    assert not pending.exists()
    assert not snapshot.exists()
    assert not partial.exists()
    assert not (state / "auto-update/after-update").exists()


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
