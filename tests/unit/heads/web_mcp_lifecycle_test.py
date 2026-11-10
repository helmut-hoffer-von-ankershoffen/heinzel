"""Browser authentication can read and prepare MCP plans only."""

import time

import pytest
from fastapi.testclient import TestClient

from heinzel.heads import web
from heinzel.heads.web._devices import Devices
from heinzel.kernel import Kernel, McpLifecycleObservation, McpLifecyclePlanning, Settings
from heinzel.kernel._mcp_handoff import mcp_handoff, require_mcp_handoff

pytestmark = pytest.mark.unit
_STATUS = "/api/mcp/lifecycle/fixture"
_SESSION = {"Authorization": "Bearer fixture-session"}


class _Planner:
    def __init__(self):
        self.revision = 1
        self.calls = []

    def status(self, registration):
        assert registration == "fixture"
        return McpLifecycleObservation(host="observed", worker="running", transport="reachable", actions=("install",))

    def prepare(self, action, registration, expires_at):
        self.calls.append((action, registration))
        return mcp_handoff(self._exact(action, registration), expires_at), "terminal"

    def validate_install(self, registration, handoff):
        require_mcp_handoff(handoff, self._exact("install", registration))

    def _exact(self, action, registration):
        return {
            "action": action,
            "registration": registration,
            "grant_revision": self.revision,
            "sdk_fingerprint": "private-fixture-sdk-fingerprint",
            "config_preimage": "private-fixture-config-digest",
        }


@pytest.fixture
def ui(tmp_path):
    kernel = Kernel(Settings(state_dir=tmp_path / "state"), [])
    devices = Devices(tmp_path / "devices")
    app = web.build(kernel, port=8000, token="fixture-session", static=None, devices=devices)
    with TestClient(app, base_url="http://localhost:8000") as client:
        yield client, kernel, devices


def test_web_and_forged_native_headers_cannot_activate_unavailable_host(ui, caplog):
    client, _, _ = ui
    assert client.get(_STATUS).status_code == 401
    assert client.post(_STATUS + "/prepare?action=start").status_code == 401
    assert client.get(_STATUS, headers=_SESSION).json()["host"] == "unavailable"
    assert client.get("/api/mcp/lifecycle", headers=_SESSION).json() == {"planning_available": False}
    caplog.set_level("INFO", logger="heinzel.mcp")
    forged = _SESSION | {"Origin": "http://localhost:8000", "X-Hz-Native": "true", "X-Forwarded-For": "127.0.0.1"}
    assert client.post(_STATUS + "/prepare?action=start", headers=forged).status_code == 409
    for endpoint in ("start", "stop", "check", "install", "approve", "apply", "review"):
        assert client.post(f"{_STATUS}/{endpoint}", headers=forged).status_code == 404
    events = [record for record in caplog.records if record.message == "mcp.ui.prepare_plan"]
    assert [record.phase for record in events] == ["start", "end"]
    assert events[-1].outcome == "error"


def test_device_prepares_install_handoff_and_revision_or_expiry_refuses(ui, monkeypatch, caplog):
    client, kernel, devices = ui
    planner = _Planner()
    kernel.bind(
        McpLifecyclePlanning,
        McpLifecyclePlanning(planner.status, planner.prepare, validate_install=planner.validate_install),
    )
    _, device = devices.pair("Isolated fixture")
    headers = {"Authorization": f"Bearer {device}"}
    status = client.get(_STATUS, headers=headers)
    assert status.headers["cache-control"] == "no-store"
    assert status.json()["transport"] == "reachable"
    assert status.json()["mcp_readiness"] == status.json()["client"] == "unverified"
    caplog.set_level("INFO", logger="heinzel.mcp")
    prepared = client.post(_STATUS + "/prepare?action=install", headers=headers)
    assert prepared.status_code == 200, prepared.text
    assert "private-fixture" not in prepared.text
    assert prepared.headers["cache-control"] == "no-store"
    plan = prepared.json()
    assert plan["action"] == "install"
    endpoint = "/api/mcp/handoffs/" + plan["id"]
    assert client.get(endpoint).status_code == 401
    assert client.get(endpoint, headers=_SESSION).json() == plan
    planner.revision += 1
    assert client.get(endpoint, headers=_SESSION).status_code == 409
    assert client.get(endpoint + "%0A", headers=_SESSION).status_code == 404
    planner.revision -= 1
    deadline = int(plan["handoff"].split(".")[0])
    with monkeypatch.context() as patcher:
        patcher.setattr(time, "time", lambda: deadline)
        assert client.get(endpoint, headers=_SESSION).status_code == 409
    assert planner.calls == [("install", "fixture")]
    events = [record for record in caplog.records if record.message == "mcp.ui.prepare_plan"]
    assert [record.phase for record in events] == ["start", "end"]
    assert events[-1].outcome == "ok"


def test_install_lookup_without_validator_does_not_prepare_again(ui):
    client, kernel, _ = ui
    planner = _Planner()
    kernel.bind(McpLifecyclePlanning, McpLifecyclePlanning(planner.status, planner.prepare))
    prepared = client.post(_STATUS + "/prepare?action=install", headers=_SESSION)
    assert prepared.status_code == 200
    assert client.get("/api/mcp/handoffs/" + prepared.json()["id"], headers=_SESSION).status_code == 409
    assert planner.calls == [("install", "fixture")]


def test_check_lookup_preserves_stateless_revalidation(ui):
    client, kernel, _ = ui
    planner = _Planner()
    kernel.bind(McpLifecyclePlanning, McpLifecyclePlanning(planner.status, planner.prepare))
    prepared = client.post(_STATUS + "/prepare?action=check", headers=_SESSION)
    assert prepared.status_code == 200
    plan = prepared.json()
    endpoint = "/api/mcp/handoffs/" + plan["id"]
    assert client.get(endpoint, headers=_SESSION).json() == plan
    planner.revision += 1
    assert client.get(endpoint, headers=_SESSION).status_code == 409
    assert planner.calls == [("check", "fixture")] * 3
