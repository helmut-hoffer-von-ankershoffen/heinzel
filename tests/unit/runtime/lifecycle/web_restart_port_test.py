"""Restarting the web job goes through the `ServiceManager` port: a fake manager replaces launchd and systemd."""

import pytest

from heinzel._adapter_service import Job
from heinzel.modules.ui._service import LABEL, UNIT
from heinzel.testing import FakeServiceManager
from tests.unit.modules.ui_refresh_test import _managed

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
