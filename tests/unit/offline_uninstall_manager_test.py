"""Offline removal uses a fixed verified manager/session, with stop separated from final file deletion."""

import os
import shutil
from datetime import timedelta
from pathlib import Path

import pytest

from heinzel import HeinzelError, Kernel, Settings
from heinzel.kernel import ProcessOutcome
from heinzel.modules.system.daemon import _service as daemon_module
from heinzel.modules.system.daemon._models import DaemonSettings
from heinzel.modules.system.daemon._service import DaemonService
from heinzel.modules.ui import _service as web_module
from tests.integration.ui_service_test import service_factory
from tests.unit.modules.daemon_service_test import bin_dir, make

__all__ = ["bin_dir", "make", "service_factory"]
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
    with pytest.raises(HeinzelError, match="fixed local user manager"):
        service.owned_manager_command(["/fixture/launchctl", "bootout", "foreign"])


@pytest.mark.parametrize("identity", [lambda: None, iter([(1, 2, 3), (4, 5, 6)]).__next__])
def test_wrong_or_changed_linux_user_bus_refuses(tmp_path, identity):
    calls = []
    service = manager(
        tmp_path,
        platform="linux",
        identity=identity,
        probe=lambda command: calls.append(command) or ProcessOutcome(0, "", ""),
    )
    with pytest.raises(HeinzelError, match=r"manager cannot be verified|uncertain"):
        service.owned_manager_command(["/usr/bin/systemctl", "--user", "show"])
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
    with pytest.raises((HeinzelError, FileNotFoundError), match=r"cannot be verified|unavailable|uncertain"):
        service.owned_manager_command(["/bin/launchctl", "bootout", "owned-fixture"])


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
        runner=service.owned_manager_command,
        which=lambda _: "/bin/launchctl",
    )
    with pytest.raises(HeinzelError, match="unowned"), web.removal_guard():
        pytest.fail("A foreign label must not authorize removal")
    assert mutations == []


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_web_stop_keeps_definitions_until_explicit_verified_cleanup_even_without_preview(
    service_factory, tmp_path, platform
):
    web, fake, port = service_factory(platform)
    web.install(port)
    web.selected_preview = False

    def probe(command):
        answer = fake(command)
        return ProcessOutcome(answer.returncode, answer.stdout, answer.stderr)

    service = manager(tmp_path, platform=platform, probe=probe)
    web.runner = service.owned_manager_command
    web.which = lambda _: "/bin/launchctl" if platform == "darwin" else "/usr/bin/systemctl"

    def refused_uv():
        with web.removal_guard() as plan:
            plan.stop()
            assert all(file.path.exists() for file in plan.files)
            assert not fake.active
            if platform == "darwin":
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


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_offline_daemon_stop_keeps_owned_definitions_until_verified_sdk_removal(make, bin_dir, tmp_path, platform):
    service = make(platform=platform)
    service.install(timedelta(minutes=5))
    installation = service._quiescence_installation
    original_probe = service._quiescence_probe

    def probe(command):
        if platform == "linux" and "--no-pager" in command:
            if command[-1] == "--property=Version":
                return ProcessOutcome(0, "Version=257 (fixture)\n", "")
            unit = command[-1]
            assert unit in {path.name for path in service._unit_paths()}
            active = bool(service._runner.active)
            output = (
                f"Id={unit}\nLoadState=loaded\nActiveState={'active' if active else 'inactive'}\n"
                f"SubState={'running' if active else 'dead'}\nJob=\nMainPID={service._runner.pid or 0}\nControlPID=0\n"
            )
            return ProcessOutcome(0, output, "")
        return original_probe(command)

    service._quiescence_probe = probe
    unrelated = tmp_path / "unrelated.service"
    unrelated.write_text("foreign service remains")
    with service.offline_removal_guard(bin_dir) as plan:
        service._write_state("standard-install-pending.json", {"token": "fixture-uninstall"})
        plan.stop("fixture-uninstall")
        assert all(file.path.exists() for file in plan.files)
        state = service.owned_manager_state(bin_dir)
        assert not state.active
        assert not state.enabled
        with pytest.raises(HeinzelError, match="removal is incomplete"):
            plan.finish()
        assert all(file.path.exists() for file in plan.files)
        shutil.rmtree(installation.prefix)
        (bin_dir / "hz").unlink()
        plan.finish()
    assert all(not file.path.exists() for file in plan.files)
    assert unrelated.read_text() == "foreign service remains"


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_offline_daemon_guard_never_stops_changed_unit_definitions(make, bin_dir, platform):
    service = make(platform=platform)
    service.install(timedelta(minutes=5))
    unit = service._unit_paths()[0]
    unit.write_text("foreign unit")
    before = list(service._runner.commands)
    with pytest.raises(HeinzelError, match="foreign or changed"), service.offline_removal_guard(bin_dir):
        pytest.fail("An edited unit must never yield removal authority")
    assert service._runner.commands == before
    assert unit.read_text() == "foreign unit"


@pytest.mark.parametrize("platform", ["darwin", "linux"])
@pytest.mark.parametrize("failure", ["refused", "timeout"])
def test_offline_daemon_stop_failure_retains_owned_files_and_sdk(make, bin_dir, platform, failure):
    service = make(platform=platform)
    service.install(timedelta(minutes=5))
    original = service._quiescence_probe
    verb = "bootout" if platform == "darwin" else "disable"

    def probe(command):
        if verb in command:
            return ProcessOutcome(None if failure == "timeout" else 1, "", "")
        return original(command)

    service._quiescence_probe = probe
    with service.offline_removal_guard(bin_dir) as plan:
        service._write_state("standard-install-pending.json", {"token": "fixture-uninstall"})
        with pytest.raises(HeinzelError, match=r"uncertain|could not be stopped"):
            plan.stop("fixture-uninstall")
        assert all(file.path.exists() for file in plan.files)
        assert service._quiescence_installation.prefix.is_dir()
        assert (service.state_dir / "standard-install-pending.json").is_file()
