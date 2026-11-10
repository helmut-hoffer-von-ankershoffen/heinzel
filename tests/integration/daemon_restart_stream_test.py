"""An isolated daemon-manager failure is reported by the real web operation stream."""

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from heinzel.heads import web
from heinzel.modules.system.daemon import DaemonService
from tests.fixtures.daemon_manager import RecordedManager
from tests.unit.modules.daemon_service_test import bin_dir, make

__all__ = ["bin_dir", "make"]
pytestmark = pytest.mark.integration


def test_restart_stream_reports_real_completed_and_failed_steps(make, bin_dir, make_kernel):
    manager = RecordedManager(bin_dir)
    service = make(runner=manager, install_notifier=lambda: None)
    service.install(timedelta(minutes=5))
    kernel = make_kernel(builtins=True)
    service._kernel = kernel
    kernel.bind(DaemonService, service)
    manager.codes["bootstrap"] = 5
    token = "fixture-token-that-is-long-enough-for-web"
    client = TestClient(web.build(kernel, port=8000, token=token, static=None), base_url="http://127.0.0.1:8000")

    response = client.post("/api/stream/system/daemon/restart", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    events = [
        (block.split("\n")[0].removeprefix("event: "), json.loads(block.split("\n")[1].removeprefix("data: ")))
        for block in response.text.strip().split("\n\n")
    ]
    phases = [(data["id"], data["state"]) for kind, data in events if kind == "phase"]
    assert ("session", "done") in phases
    assert ("unit", "done") in phases
    assert ("load", "failed") in phases
    assert events[-1][0] == "error"
    assert not any(kind == "result" for kind, _ in events)
