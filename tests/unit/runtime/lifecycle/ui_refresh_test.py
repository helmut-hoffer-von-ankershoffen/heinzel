"""After an update the owned web server restarts into the installed code, and only its successor's report counts."""

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from heinzel import HeinzelError
from heinzel.modules.system.lifecycle._adapter_launchd import LaunchdWebCommands
from heinzel.modules.system.lifecycle._adapter_systemd import SystemdWebCommands
from heinzel.modules.system.lifecycle._service import LABEL, READY_SECONDS, UNIT, WebRestart, WebService, WebStatus
from heinzel.modules.ui import _web_head_refresh

pytestmark = pytest.mark.unit


def test_update_prepares_web_service_before_replacing_installed_modules(tmp_path, monkeypatch):
    """An update must not boot new modules against the old interpreter's already imported kernel."""
    from heinzel.kernel import HeadRefresh
    from heinzel.modules import ui

    record = tmp_path / "ui/service.json"
    record.parent.mkdir()
    record.touch()
    monkeypatch.setattr(
        ui.Config, "load", lambda _home: SimpleNamespace(settings=lambda *_: SimpleNamespace(state_dir=tmp_path))
    )
    installed = {"replaced": False}
    targets: list[str] = []

    class Kernel:
        def resolve(self, service):
            assert service is WebService
            if installed["replaced"]:
                msg = "cannot import name 'ByArguments' from 'heinzel'"
                raise ImportError(msg)
            return SimpleNamespace(
                refresh=lambda target: targets.append(target) or HeadRefresh(head="web", status="verified")
            )

    web = _web_head_refresh(Kernel())
    installed["replaced"] = True
    assert web.refresh("0.9.46").status == "verified"
    assert targets == ["0.9.46"]


@pytest.mark.parametrize("new_export", ["ByArguments", "DocumentHit"])
def test_web_refresh_survives_real_mixed_generation_import(tmp_path, monkeypatch, new_export):
    """A new file cannot import its export from the updater's still-loaded old kernel."""
    from heinzel.modules import ui

    record = tmp_path / "ui/service.json"
    record.parent.mkdir()
    record.touch()
    monkeypatch.setattr(
        ui.Config, "load", lambda _home: SimpleNamespace(settings=lambda *_: SimpleNamespace(state_dir=tmp_path))
    )
    old_kernel = ModuleType("fixture_old_kernel")
    monkeypatch.setitem(sys.modules, "fixture_old_kernel", old_kernel)
    old = tmp_path / "old_service.py"
    new = tmp_path / "new_service.py"
    old.write_text(
        "from heinzel.kernel import HeadRefresh\n"
        "class Service:\n"
        "    def refresh(self, target):\n"
        "        return HeadRefresh(head='web', status='verified', version=target)\n"
    )
    new.write_text(f"from fixture_old_kernel import {new_export}\n")
    installed = {"path": old}

    class Kernel:
        def resolve(self, service):
            assert service is WebService
            spec = importlib.util.spec_from_file_location("fixture_web_service", installed["path"])
            assert spec is not None
            assert spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module.Service()

    kernel = Kernel()
    refresh = _web_head_refresh(kernel)
    assert refresh.recovery_ready is None
    installed["path"] = new
    with pytest.raises(ImportError, match=new_export):
        kernel.resolve(WebService)
    result = refresh.refresh("0.9.46")
    assert (result.status, result.version) == ("verified", "0.9.46")


def test_update_does_not_resolve_a_missing_web_service(tmp_path, monkeypatch):
    from heinzel.modules import ui

    monkeypatch.setattr(
        ui.Config, "load", lambda _home: SimpleNamespace(settings=lambda *_: SimpleNamespace(state_dir=tmp_path))
    )

    class Kernel:
        def resolve(self, _service):
            msg = "a missing service must not be resolved"
            raise AssertionError(msg)

    web = _web_head_refresh(Kernel())
    assert web.refresh("0.9.46").status == "not-installed"
    record = tmp_path / "ui/service.json"
    record.parent.mkdir()
    record.touch()
    assert web.refresh("0.9.46").status == "pending"


def test_web_service_preparation_failure_is_reported_after_update(tmp_path, monkeypatch):
    from heinzel.modules import ui

    record = tmp_path / "ui/service.json"
    record.parent.mkdir()
    record.touch()
    monkeypatch.setattr(
        ui.Config, "load", lambda _home: SimpleNamespace(settings=lambda *_: SimpleNamespace(state_dir=tmp_path))
    )

    class Kernel:
        def resolve(self, _service):
            msg = "unavailable"
            raise ImportError(msg)

    web = _web_head_refresh(Kernel())
    result = web.refresh("0.9.46")
    assert result.status == "failed"
    assert "hz ui status" in result.detail


def test_persistent_web_opens_only_when_the_service_record_exists(tmp_path, monkeypatch):
    from heinzel.modules import ui

    monkeypatch.setattr(
        ui.Config, "load", lambda _home: SimpleNamespace(settings=lambda *_: SimpleNamespace(state_dir=tmp_path))
    )
    calls = []

    class Kernel:
        def resolve(self, service):
            assert service is WebService
            calls.append(service)
            return SimpleNamespace(open_local=lambda: "http://127.0.0.1:8737")

    persistent = ui._persistent_web(Kernel())
    assert persistent.open_local() is None
    assert calls == []
    record = tmp_path / "ui/service.json"
    record.parent.mkdir()
    record.touch()
    assert persistent.open_local() == "http://127.0.0.1:8737"
    assert calls == [WebService]


def test_unreadable_record_is_reported_before_or_after_service_preparation(tmp_path, monkeypatch):
    from heinzel.modules import ui

    monkeypatch.setattr(
        ui.Config, "load", lambda _home: SimpleNamespace(settings=lambda *_: SimpleNamespace(state_dir=tmp_path))
    )
    record = tmp_path / "ui/service.json"
    original = Path.lstat
    unreadable = False

    def lstat(path):
        if path == record and unreadable:
            msg = "unreadable"
            raise PermissionError(msg)
        return original(path)

    monkeypatch.setattr(Path, "lstat", lstat)
    absent = _web_head_refresh(object())
    unreadable = True
    assert absent.refresh("0.9.46").status == "failed"
    assert _web_head_refresh(object()).refresh("0.9.46").status == "failed"


def test_unreadable_configuration_is_reported_without_preventing_update(monkeypatch):
    from heinzel.modules import ui

    def load(_home):
        msg = "unreadable"
        raise OSError(msg)

    monkeypatch.setattr(ui.Config, "load", load)
    assert _web_head_refresh(object()).refresh("0.9.46").status == "failed"


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def ready(instance: str, version: str, pid: int = 4242) -> WebStatus:
    return WebStatus(installed=True, loaded=True, ready=True, pid=pid, port=8737, instance=instance, version=version)


def service(tmp_path, statuses, *, restart=None):
    clock = Clock()
    web = WebService(
        tmp_path / "state",
        tmp_path / "home",
        tmp_path / "bin/hz",
        platform="darwin",
        profile={},
        clock=clock,
        sleep=clock.sleep,
    )
    seen = iter(statuses)
    last: list[WebStatus] = []

    def status() -> WebStatus:
        last[:] = [next(seen, last[0] if last else statuses[-1])]
        return last[0]

    restarts: list[int] = []

    def restarted() -> WebRestart:
        restarts.append(1)
        if restart is not None:
            raise restart
        return WebRestart(requested=True, retiring_instance="a" * 32, message="")

    web.status = status  # type: ignore[method-assign]
    web.restart_job = restarted  # type: ignore[method-assign]
    return web, restarts, clock


def test_a_server_already_on_the_target_core_still_restarts_so_new_plugins_load(tmp_path):
    web, restarts, _ = service(tmp_path, [ready("a" * 32, "0.9.26"), ready("b" * 32, "0.9.26")])
    result = web.refresh("0.9.26")
    assert restarts == [1]
    assert (result.status, result.version) == ("verified", "0.9.26")


def test_the_old_server_that_kept_running_old_code_is_restarted_and_its_successor_verified(tmp_path):
    old, waiting, new = ready("a" * 32, "0.9.22"), ready("a" * 32, "0.9.22"), ready("b" * 32, "0.9.26")
    web, restarts, clock = service(tmp_path, [old, waiting, waiting, new])
    result = web.refresh("0.9.26")
    assert (restarts, result.status, result.version) == ([1], "verified", "0.9.26")
    assert 0 < clock.now < READY_SECONDS


def test_a_successor_on_another_version_fails_and_names_the_fix(tmp_path):
    web, _, _ = service(tmp_path, [ready("a" * 32, "0.9.22"), ready("b" * 32, "0.9.22")])
    result = web.refresh("0.9.26")
    assert (result.status, result.version) == ("failed", "0.9.22")
    assert "hz ui restart" in result.detail


def test_no_successor_within_the_readiness_bound_stays_pending(tmp_path):
    web, _, clock = service(tmp_path, [ready("a" * 32, "0.9.22")])
    result = web.refresh("0.9.26")
    assert result.status == "pending"
    assert clock.now == pytest.approx(READY_SECONDS)
    assert "hz ui status" in result.detail


def test_a_refused_restart_is_a_failure_not_an_exception(tmp_path):
    web, _, _ = service(tmp_path, [ready("a" * 32, "0.9.22")], restart=HeinzelError("mutation pending"))
    result = web.refresh("0.9.26")
    assert result.status == "failed"
    assert "mutation pending" in result.detail


def test_the_server_running_the_update_is_never_restarted_from_inside(tmp_path):
    web, restarts, _ = service(tmp_path, [ready("a" * 32, "0.9.22", pid=os.getpid())])
    assert web.refresh("0.9.26").status == "self"
    assert restarts == []


def test_nothing_installed_is_nothing_to_refresh(tmp_path):
    web, restarts, _ = service(tmp_path, [WebStatus(installed=False, loaded=False, ready=False)])
    assert web.refresh("0.9.26").status == "not-installed"
    assert restarts == []


def _restartable(tmp_path, repair=None):
    prefix = tmp_path / "tool"
    prefix.mkdir()
    web = WebService(
        tmp_path / "state",
        tmp_path / "home",
        tmp_path / "bin/hz",
        platform="darwin",
        profile={},
        prefix=prefix,
        repair=repair,
    )
    controls: list[str] = []
    web._transition_needed = lambda: False  # type: ignore[method-assign]
    web._record = lambda: {"port": 8737}  # type: ignore[method-assign]
    web._manager = lambda _record: (True, 4242)  # type: ignore[method-assign]

    def control(_record, _pid, verb):
        controls.append(verb)
        return {"instance": "a" * 32}

    web._control = control  # type: ignore[method-assign]
    return web, prefix / ".heinzel-startup-pending", controls


def test_restart_repairs_a_marker_a_put_back_left_and_restarts(tmp_path):
    """The Mini, 0.9.28: CLI and daemon ran the new version, web kept the old one, `hz ui restart` refused."""
    removed: list[int] = []

    def repair() -> bool:
        removed.append(1)
        marker.unlink()
        return True

    web, marker, controls = _restartable(tmp_path, repair)
    marker.touch()
    assert web.restart().requested
    assert (removed, controls) == ([1], ["restart"])


def test_restart_names_the_way_out_when_the_marker_must_stay(tmp_path):
    web, marker, controls = _restartable(tmp_path, lambda: False)
    marker.touch()
    with pytest.raises(HeinzelError, match="hz system update"):
        web.restart()
    assert controls == []
    assert marker.exists()


def test_restart_never_repairs_past_another_unfinished_change(tmp_path):
    repairs: list[int] = []
    web, marker, controls = _restartable(tmp_path, lambda: bool(repairs.append(1)))
    marker.touch()
    gate = tmp_path / "state/daemon/mutation-pending.json"
    gate.parent.mkdir(parents=True)
    gate.write_text("{}")
    with pytest.raises(HeinzelError, match="requires completion or recovery"):
        web.restart()
    assert (repairs, controls) == ([], [])


def _managed(tmp_path, platform, *, returncode=0, loaded=True):
    web, _marker, _controls = _restartable(tmp_path)
    web.platform = platform
    web.uid = 501
    web._manager = lambda _record: (loaded, 4242)  # type: ignore[method-assign]
    web.which = lambda _name: "mgr"
    commands: list[list[str]] = []

    def runner(command):
        commands.append(list(command))
        return SimpleNamespace(returncode=returncode, stdout="", stderr="no such job")

    web.runner = runner  # type: ignore[assignment]
    return web, commands


def test_restart_falls_back_to_the_launchd_job_when_the_control_folder_is_gone(tmp_path):
    """The Mini, 0.9.52: the /tmp control folder was missing and `hz ui restart` printed only its path."""
    web, commands = _managed(tmp_path, "darwin")

    def gone(_record, _pid, _verb):
        path = "/private/tmp/hz-ui-501-628f7bee6e0e5926"
        raise FileNotFoundError(path)

    web._control = gone  # type: ignore[method-assign]
    result = web.restart()
    assert result.requested
    assert commands == [["mgr", "kickstart", "-k", "gui/501/io.github.helmut-hoffer-von-ankershoffen.heinzel.web"]]


def test_restart_falls_back_when_the_control_socket_times_out(tmp_path):
    web, commands = _managed(tmp_path, "linux")

    def hung(_record, _pid, _verb):
        raise TimeoutError

    web._control = hung  # type: ignore[method-assign]
    assert web.restart().requested
    assert commands == [["mgr", "--user", "restart", "hz-web.service"]]


def test_restart_job_names_a_manager_refusal_in_a_sentence(tmp_path):
    web, _ = _managed(tmp_path, "darwin", returncode=113)
    with pytest.raises(HeinzelError, match=r"service manager could not restart the web service \(no such job\)"):
        web.restart_job()


def test_restart_job_refuses_when_nothing_is_loaded(tmp_path):
    web, commands = _managed(tmp_path, "darwin", loaded=False)
    with pytest.raises(HeinzelError, match="nothing to restart"):
        web.restart_job()
    assert commands == []


def test_update_restarts_through_the_job_and_checks_the_served_version(tmp_path):
    """The old server hung but kept its port: the update kickstarts the job and accepts only the target version."""
    web, restarts, _ = service(tmp_path, [ready("a" * 32, "0.9.51"), ready("b" * 32, "0.9.52")])
    result = web.refresh("0.9.52")
    assert (restarts, result.status, result.version) == ([1], "verified", "0.9.52")


def test_restart_refuses_when_the_job_is_not_loaded(tmp_path):
    web, _ = _managed(tmp_path, "darwin", loaded=False)
    with pytest.raises(HeinzelError, match="No owned running web service can restart"):
        web.restart()


def test_restart_names_a_repair_that_fails(tmp_path):
    def broken() -> bool:
        msg = "locked"
        raise OSError(msg)

    web, marker, controls = _restartable(tmp_path, broken)
    marker.touch()
    with pytest.raises(HeinzelError, match="requires completion or recovery"):
        web.restart()
    assert controls == []


def test_install_refuses_a_port_outside_the_range(tmp_path):
    web, _ = _managed(tmp_path, "darwin")
    with pytest.raises(HeinzelError, match="between 1 and 65535"):
        web.install(70000)


def test_an_unsupported_platform_has_no_service_manager(tmp_path):
    web, _ = _managed(tmp_path, "win32")
    with pytest.raises(HeinzelError, match="launchd or Linux systemd"):
        web._commands()


def test_a_loaded_label_with_unknown_identity_is_refused(tmp_path):
    web, _ = _managed(tmp_path, "darwin")
    web.which = lambda _name: "/bin/launchctl"

    def runner(command):
        if "print" in command:
            return SimpleNamespace(returncode=113, stdout="", stderr="")
        listing = "PID\tStatus\tLabel\n-\t0\tio.github.helmut-hoffer-von-ankershoffen.heinzel.web\n"
        return SimpleNamespace(returncode=0, stdout=listing, stderr="")

    web.runner = runner  # type: ignore[assignment]
    with pytest.raises(HeinzelError, match="loaded identity is unknown"):
        web._manager_darwin(None)


def test_status_reports_a_pending_launcher_transition(tmp_path, monkeypatch):
    from heinzel.modules.system.lifecycle import _service

    web, _ = _managed(tmp_path, "darwin")
    transition = tmp_path / "transition.json"
    transition.touch()
    monkeypatch.setattr(_service, "LauncherTransition", lambda *_: SimpleNamespace(path=transition))
    assert "transition requires" in (web.status().problem or "")


class _Locks:
    def __init__(self, _paths):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _in_transition(tmp_path, monkeypatch, *, pending, instance=None, loaded=True):
    from heinzel.modules.system.lifecycle import _service

    web, _ = _managed(tmp_path, "darwin", loaded=loaded)
    web._transition_needed = lambda: True  # type: ignore[method-assign]
    path = tmp_path / "transition.json"
    if pending:
        path.touch()
    migrated: list[str] = []
    monkeypatch.setattr(_service, "MaintenanceLocks", _Locks)
    monkeypatch.setattr(
        _service,
        "LauncherTransition",
        lambda *_: SimpleNamespace(path=path, load=lambda: SimpleNamespace(retiring_instance=instance)),
    )
    web._transition = lambda action, **kw: migrated.append(kw["retiring_instance"])  # type: ignore[method-assign]
    web._control = lambda *_: {"instance": "d" * 32}  # type: ignore[method-assign]
    return web, migrated


def test_restart_during_a_pending_transition_without_an_instance_names_the_command(tmp_path, monkeypatch):
    web, migrated = _in_transition(tmp_path, monkeypatch, pending=True)
    with pytest.raises(HeinzelError, match="Complete the pending launcher operation"):
        web.restart()
    assert migrated == []


def test_restart_during_a_pending_transition_completes_it_for_the_recorded_instance(tmp_path, monkeypatch):
    web, migrated = _in_transition(tmp_path, monkeypatch, pending=True, instance="e" * 32)
    assert web.restart().retiring_instance == "e" * 32
    assert migrated == ["e" * 32]


def test_restart_migrates_a_historical_launcher_through_its_running_instance(tmp_path, monkeypatch):
    web, migrated = _in_transition(tmp_path, monkeypatch, pending=False)
    assert web.restart().retiring_instance == "d" * 32
    assert migrated == ["d" * 32]


def test_restart_cannot_migrate_a_historical_launcher_that_is_not_running(tmp_path, monkeypatch):
    web, _ = _in_transition(tmp_path, monkeypatch, pending=False, loaded=False)
    with pytest.raises(HeinzelError, match="use `hz ui install` to migrate"):
        web.restart()


def test_restart_job_reports_the_instance_the_runtime_record_names(tmp_path, monkeypatch):
    from heinzel.modules.system.lifecycle import _service

    web, _ = _managed(tmp_path, "darwin")
    monkeypatch.setattr(_service, "private_read", lambda _path: '{"instance": "' + "f" * 32 + '"}')
    assert web.restart_job().retiring_instance == "f" * 32


def test_the_default_runner_runs_the_fixed_vector_with_a_timeout(monkeypatch):
    from heinzel.modules.system.lifecycle import _service

    seen: dict[str, object] = {}

    def run(command, **kwargs):
        seen.update(command=command, **kwargs)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(_service.subprocess, "run", run)
    _service._run(["launchctl", "list"])
    assert seen["command"] == ["launchctl", "list"]
    assert seen["timeout"] == 10


PLIST = "/Users/a/Library/LaunchAgents/job.plist"


def test_launchd_spells_every_command_of_the_web_job() -> None:
    """The web job's command lines live in the launchd adapter, spelled as the service ran them before."""
    commands = LaunchdWebCommands("/bin/launchctl", 501, LABEL)
    assert commands.base() == ["/bin/launchctl"]
    assert commands.inspect() == ["/bin/launchctl", "print", f"gui/501/{LABEL}"]
    assert commands.listing() == ["/bin/launchctl", "list"]
    assert commands.session() == (["/bin/launchctl", "managername"], ["/bin/launchctl", "manageruid"])
    assert commands.load(PLIST) == [["/bin/launchctl", "bootstrap", "gui/501", PLIST]]
    assert commands.start(PLIST) == ["/bin/launchctl", "bootstrap", "gui/501", PLIST]
    assert commands.unload() == ["/bin/launchctl", "bootout", f"gui/501/{LABEL}"]
    assert (commands.reload(), commands.stop_state()) == (None, None)


def test_systemd_spells_every_command_of_the_web_job() -> None:
    """The same for systemd's user instance."""
    commands = SystemdWebCommands("/usr/bin/systemctl", UNIT)
    user = ["/usr/bin/systemctl", "--user"]
    shown = "--property=LoadState,FragmentPath,DropInPaths,ExecStart,MainPID"
    assert commands.base() == user
    assert commands.inspect() == [*user, "show", UNIT, shown]
    assert commands.load(PLIST) == [[*user, "daemon-reload"], [*user, "enable", "--now", UNIT]]
    assert commands.start(PLIST) == [*user, "start", UNIT]
    assert commands.unload() == [*user, "disable", "--now", UNIT]
    assert commands.reload() == [*user, "daemon-reload"]
    assert commands.stop_state() == [*user, "show", UNIT, "--property=ActiveState,MainPID"]


def test_a_linux_login_without_systemctl_has_no_service_manager(tmp_path: Path) -> None:
    def absent(_name: str) -> str | None:
        return None

    web = WebService(tmp_path, tmp_path, tmp_path / "hz", platform="linux", which=absent, uid=7)
    with pytest.raises(HeinzelError, match="launchd or Linux systemd"):
        web.service_manager()
