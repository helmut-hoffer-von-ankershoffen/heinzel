"""Restarting the web job goes through the `ServiceManager` port: a fake manager replaces launchd and systemd.

The job's command lines live in the launchd and systemd adapters, spelled as the service ran them before.
"""

import pytest

from heinzel._adapter_service import Job
from heinzel.modules.system.lifecycle._adapter_launchd import LaunchdWebCommands
from heinzel.modules.system.lifecycle._adapter_systemd import SystemdWebCommands
from heinzel.modules.system.lifecycle._service import LABEL, UNIT, WebService
from heinzel.testing import FakeServiceManager
from tests.unit.runtime.lifecycle.ui_refresh_test import _managed

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(("platform", "label"), [("darwin", LABEL), ("linux", UNIT)])
def test_a_restart_asks_the_manager_for_a_new_process_and_runs_no_command(tmp_path, platform, label):
    web, commands = _managed(tmp_path, platform)
    manager = FakeServiceManager("1.0.0")
    manager.load(Job(label))
    before = manager.status(Job(label)).pid
    web._manager_port = manager

    def hung(_record, _pid, _verb):
        raise TimeoutError

    web._control = hung  # type: ignore[method-assign]
    assert web.restart().requested
    assert manager.status(Job(label)).pid not in {None, before}
    assert manager.calls[-1] == ("kickstart", label, True)
    assert commands == []


def test_a_manager_that_refuses_is_reported_in_the_services_words(tmp_path):
    web, _commands = _managed(tmp_path, "darwin", returncode=113)
    with pytest.raises(Exception, match=r"could not restart the web service \(no such job\)"):
        web.restart_job({"service": "x"})


PLIST = "/Users/a/Library/LaunchAgents/job.plist"


def test_launchd_spells_every_command_of_the_web_job():
    commands = LaunchdWebCommands("/bin/launchctl", 501, LABEL)
    assert commands.base() == ["/bin/launchctl"]
    assert commands.inspect() == ["/bin/launchctl", "print", f"gui/501/{LABEL}"]
    assert commands.listing() == ["/bin/launchctl", "list"]
    assert commands.session() == (["/bin/launchctl", "managername"], ["/bin/launchctl", "manageruid"])
    assert commands.load(PLIST) == [["/bin/launchctl", "bootstrap", "gui/501", PLIST]]
    assert commands.start(PLIST) == ["/bin/launchctl", "bootstrap", "gui/501", PLIST]
    assert commands.unload() == ["/bin/launchctl", "bootout", f"gui/501/{LABEL}"]
    assert (commands.reload(), commands.stop_state()) == (None, None)


def test_systemd_spells_every_command_of_the_web_job():
    commands = SystemdWebCommands("/usr/bin/systemctl", UNIT)
    user = ["/usr/bin/systemctl", "--user"]
    assert commands.base() == user
    assert commands.inspect() == [
        *user,
        "show",
        UNIT,
        "--property=LoadState,FragmentPath,DropInPaths,ExecStart,MainPID",
    ]
    assert commands.load(PLIST) == [[*user, "daemon-reload"], [*user, "enable", "--now", UNIT]]
    assert commands.start(PLIST) == [*user, "start", UNIT]
    assert commands.unload() == [*user, "disable", "--now", UNIT]
    assert commands.reload() == [*user, "daemon-reload"]
    assert commands.stop_state() == [*user, "show", UNIT, "--property=ActiveState,MainPID"]


@pytest.mark.parametrize(("platform", "kind"), [("darwin", LaunchdWebCommands), ("linux", SystemdWebCommands)])
def test_the_service_takes_its_commands_from_the_platforms_adapter(tmp_path, platform, kind):
    web = WebService(tmp_path, tmp_path, tmp_path / "hz", platform=platform, which=lambda name: f"/x/{name}", uid=7)
    assert isinstance(web._commands(), kind)


def test_an_unsupported_platform_or_a_missing_manager_is_refused(tmp_path):
    for platform, which in (("win32", lambda name: f"/x/{name}"), ("linux", lambda _name: None)):
        web = WebService(tmp_path, tmp_path, tmp_path / "hz", platform=platform, which=which, uid=7)
        with pytest.raises(Exception, match="requires macOS launchd or Linux systemd"):
            web._commands()
