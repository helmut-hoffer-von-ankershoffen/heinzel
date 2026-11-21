"""Several servers, one installation: the two bugs of 2026-11-21 as permanent scenarios (no processes, no waiting).

- v2oga (#886): a desktop-spawned `hz ui serve` ran 0.9.51 for days while the installation moved to 0.9.52 under it. It
  applied, was refused, and its six-hour failure in the shared state held every server back. It must restart into the
  installed hz and record nothing.
- 5fmt6 (#884): `hz ui restart` failed on a cleaned control folder and left the launchd web job on its old process. It
  must restart the job through its service manager.
"""

import time

import pytest

from heinzel.modules.system.lifecycle._service import LABEL
from heinzel.testing import installation
from tests.unit.heads.web_auto_update_test import advance, installed_under, updater
from tests.unit.runtime.lifecycle.ui_refresh_test import _restartable

pytestmark = [pytest.mark.unit, pytest.mark.timeout(10)]


def test_v2oga_servers_older_than_the_installation_restart_into_it_and_hold_nobody_back(make_kernel, tmp_path):
    began = time.monotonic()
    world = installation(
        tmp_path / "world",
        servers=[
            {"kind": "launchd", "version": "0.9.51"},
            {"kind": "desktop", "version": "0.9.51", "started_days_ago": 4},
        ],
    )
    world.upgrade("0.9.52")  # the daemon handed over to 0.9.52 by another path
    runs = []
    for server in world.servers:
        made, clock, calls, kernel = updater(make_kernel, tmp_path)  # one state folder for all of them
        installed_under(kernel, tmp_path, "9.9.9")  # what is installed under every server now
        advance(made, clock, 5 * 60)
        runs.append((server.kind, calls, made.status()))
    for kind, calls, status in runs:
        assert calls == {"restart": 1, "update": 0}, kind  # restarts into the installed hz; never applies
        assert (status.message, status.core) == (None, None), kind  # no failure for the others to wait on
    assert time.monotonic() - began < 1


def test_5fmt6_a_restart_without_the_control_channel_restarts_the_job_through_its_manager(tmp_path):
    began = time.monotonic()
    world = installation(
        tmp_path / "world",
        servers=[
            {"kind": "launchd", "version": "0.9.51", "started_days_ago": 5},
            {"kind": "desktop", "version": "0.9.52"},
        ],
        web_label=LABEL,
    )
    world.upgrade("0.9.52")
    web_server, desktop_server = world.servers
    assert web_server.job is not None
    stale = world.manager.status(web_server.job)
    assert (stale.loaded, stale.version) == (True, "0.9.51")

    web, _marker, _controls = _restartable(tmp_path)
    web._manager_port = world.manager

    def gone(_record, _pid, _verb):
        cleaned = "/private/tmp/control-folder-501"  # macOS cleaned the control folder
        raise FileNotFoundError(cleaned)

    web._control = gone  # type: ignore[method-assign]
    assert web.restart().requested
    fresh = world.manager.status(web_server.job)
    assert fresh.pid not in {None, stale.pid}
    assert world.running(web_server) == "0.9.52"
    assert world.running(desktop_server) == "0.9.52"  # another server on the state folder is not touched
    assert time.monotonic() - began < 1
