"""Offline removal uses a fixed verified manager/session, with stop separated from final file deletion."""

import os
from pathlib import Path

import pytest

from heinzel import HeinzelError, Kernel, Settings
from heinzel.kernel import ProcessOutcome
from heinzel.modules.system.daemon import _service as daemon_module
from heinzel.modules.system.daemon._models import DaemonSettings
from heinzel.modules.system.daemon._service import DaemonService
from heinzel.modules.ui import _service as web_module
from tests.integration.ui_service_test import service_factory

__all__ = ["service_factory"]
pytestmark = pytest.mark.unit


def manager(tmp_path, platform="darwin", probe=None, identity=lambda: (1, 2, 3)):
    settings = Settings(state_dir=tmp_path / "state", data_dir=tmp_path / "data", log_dir=tmp_path / "logs")
    return DaemonService(
        Kernel(settings),
        settings,
        DaemonSettings(unit_dir=tmp_path / "units"),
        platform=platform,
        quiescence_probe=probe,
        linux_manager_identity=identity,
    )


def test_fixed_manager_environment_cannot_inherit_path_or_bus_substitution(monkeypatch):
    for name in ("PATH", "DBUS_SESSION_BUS_ADDRESS", "SYSTEMD_BUS_ADDRESS", "LAUNCHD_SOCKET", "UV_TOOL_DIR"):
        monkeypatch.setenv(name, "foreign-selected-authority")
    linux = daemon_module._manager_environment(["/usr/bin/systemctl", "--user", "show"])
    mac = daemon_module._manager_environment(["/bin/launchctl", "list"])
    assert linux == {"PATH": "/usr/bin:/bin", "LANG": "C", "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}"}
    assert mac == {"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": str(Path.home())}


def test_path_selected_manager_is_refused_without_execution(tmp_path):
    service = manager(tmp_path, probe=lambda *args: pytest.fail("A PATH-substituted manager must not execute"))
    with pytest.raises(HeinzelError, match="fixed local authority"):
        service.offline_manager_command(["/fixture/launchctl", "bootout", "foreign"])


@pytest.mark.parametrize("identity", [lambda: None, iter([(1, 2, 3), (4, 5, 6)]).__next__])
def test_wrong_or_changed_linux_user_bus_refuses(tmp_path, identity):
    calls = []
    service = manager(
        tmp_path,
        platform="linux",
        identity=identity,
        probe=lambda command: calls.append(command) or ProcessOutcome(0, "", ""),
    )
    with pytest.raises(HeinzelError, match=r"bus|changed"):
        service.offline_manager_command(["/usr/bin/systemctl", "--user", "show"])
    assert len(calls) <= 1


@pytest.mark.parametrize("answer", ["foreign-session", "missing", "timeout"])
def test_mac_unknown_manager_or_stop_timeout_cannot_claim_success(tmp_path, answer):
    def probe(command):
        if command[1] == "managername":
            return ProcessOutcome(0, "Background" if answer == "foreign-session" else "Aqua", "")
        if command[1] == "manageruid":
            return ProcessOutcome(0, str(os.getuid()), "")
        if answer == "missing":
            msg = "fixed manager unavailable"
            raise FileNotFoundError(msg)
        return ProcessOutcome(None, "", "")

    service = manager(tmp_path, probe=probe)
    with pytest.raises((HeinzelError, FileNotFoundError), match=r"session|unavailable|did not finish"):
        service.offline_manager_command(["/bin/launchctl", "bootout", "owned-fixture"])


def test_foreign_loaded_web_label_refuses_before_stop(tmp_path):
    mutations = []

    def probe(command):
        if command[1] == "managername":
            return ProcessOutcome(0, "Aqua", "")
        if command[1] == "manageruid":
            return ProcessOutcome(0, str(os.getuid()), "")
        if command[1] == "print":
            return ProcessOutcome(0, "foreign loaded job", "")
        mutations.append(command)
        return ProcessOutcome(0, "", "")

    service = manager(tmp_path, probe=probe)
    web = web_module.WebService(
        tmp_path / "state",
        tmp_path / "home",
        tmp_path / "bin/hz",
        platform="darwin",
        runner=service.offline_manager_command,
        which=lambda _: "/bin/launchctl",
    )
    with pytest.raises(HeinzelError, match="unowned"), web.removal_guard():
        pytest.fail("A foreign label must not authorize removal")
    assert mutations == []


def test_web_stop_keeps_definitions_until_explicit_verified_cleanup_even_without_preview(service_factory, tmp_path):
    web, fake, port = service_factory("darwin")
    web.install(port)
    web.selected_preview = False

    def probe(command):
        answer = fake(command)
        return ProcessOutcome(answer.returncode, answer.stdout, answer.stderr)

    service = manager(tmp_path, probe=probe)
    web.runner = service.offline_manager_command
    web.which = lambda _: "/bin/launchctl"

    def refused_uv():
        with web.removal_guard() as plan:
            plan.stop()
            assert all(file.path.exists() for file in plan.files)
            assert not fake.loaded
            message = "uv refused; do not delete integration"
            raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="uv refused"):
        refused_uv()
    assert web.unit.exists()
    assert web.launcher.exists()
    assert web.marker.exists()
    with web.removal_guard() as plan:
        plan.stop()
        assert plan.finish().changed
    assert not web.unit.exists()
    assert not web.launcher.exists()
    assert not web.marker.exists()
