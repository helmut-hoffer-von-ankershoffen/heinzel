"""The widget credential is confined to one paired device and one read-only route."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from heinzel import HeinzelError
from heinzel.heads import web
from heinzel.heads.web._devices import Devices
from heinzel.modules.heinzel._session_crud import SessionCrud

pytestmark = pytest.mark.integration
TOKEN = "widget-test-session"


def test_widget_scope_expiry_and_revocation(make_kernel, tmp_path):
    now = [datetime(2026, 1, 1, tzinfo=UTC)]
    devices = Devices(tmp_path / "devices", clock=lambda: now[0])
    client = TestClient(
        web.build(make_kernel(builtins=True), port=8000, token=TOKEN, devices=devices, static=None),
        base_url="http://127.0.0.1:8000",
    )
    session = {"Authorization": f"Bearer {TOKEN}"}
    assert client.post("/api/_web/widget-credential", headers=session).status_code == 403
    paired = client.post("/api/_web/device", headers=session, json={"name": "phone"}).json()
    device = {"Authorization": f"Bearer {paired['token']}"}
    issued = client.post("/api/_web/widget-credential", headers=device)
    assert issued.status_code == 200
    credential = issued.json()
    widget = {"Authorization": "Bearer " + credential["token"], "X-Hz-Workspace-Id": credential["workspace_id"]}
    response = client.get("/api/_web/widget-snapshot", headers=widget)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"workspace_id": credential["workspace_id"], "awake": 0, "waiting": 0, "roster": []}
    assert client.get("/api/system/notify/list", headers=widget).status_code == 401
    assert client.post("/api/_web/widget-credential", headers=widget).status_code == 401
    assert client.get("/api/_web/widget-snapshot", headers={**widget, "X-Hz-Workspace-Id": "wrong"}).status_code == 401
    assert credential["token"] not in devices.path.read_text()
    now[0] += timedelta(hours=24)
    assert client.get("/api/_web/widget-snapshot", headers=widget).status_code == 401
    replacement = client.post("/api/_web/widget-credential", headers=device).json()
    widget["Authorization"] = "Bearer " + replacement["token"]
    devices.revoke(paired["id"])
    assert client.get("/api/_web/widget-snapshot", headers=widget).status_code == 401


def test_widget_projection_is_bounded_and_credential_revokes(make_kernel, tmp_path, monkeypatch):
    sessions = [
        SimpleNamespace(
            session_id=str(i), name="a" * 200, state="awake", activity="working", waiting_count=10000, secret="private"
        )
        for i in range(45)
    ]
    monkeypatch.setattr(SessionCrud, "listing", lambda self: SimpleNamespace(sessions=sessions))
    devices = Devices(tmp_path / "devices")
    client = TestClient(
        web.build(make_kernel(builtins=True), port=8000, token=TOKEN, devices=devices, static=None),
        base_url="http://127.0.0.1:8000",
    )
    session = {"Authorization": f"Bearer {TOKEN}"}
    paired = client.post("/api/_web/device", headers=session, json={"name": "phone"}).json()
    device = {"Authorization": f"Bearer {paired['token']}"}
    issued = client.post("/api/_web/widget-credential", headers=device).json()
    widget = {"Authorization": "Bearer " + issued["token"], "X-Hz-Workspace-Id": issued["workspace_id"]}
    body = client.get("/api/_web/widget-snapshot", headers=widget).json()
    assert body["awake"] == 45
    assert len(body["roster"]) == 40
    assert len(body["roster"][0]["name"]) == 128
    assert body["roster"][0]["asks"] == 9999
    assert "secret" not in body["roster"][0]
    assert client.delete("/api/_web/widget-credential", headers=device).status_code == 200
    assert client.get("/api/_web/widget-snapshot", headers=widget).status_code == 401
    assert client.get("/api/system/notify/list", headers=device).status_code == 200


def test_widget_identity_fails_closed_when_damaged(tmp_path):
    devices = Devices(tmp_path / "devices")
    device, _ = devices.pair("phone")
    token, workspace, _ = devices.widget_issue(device.id, "owner")
    assert devices.widget_check(token, "other-owner", workspace) is None
    assert Devices(devices.directory).widget_check(token, "owner", workspace) == device.id
    (devices.directory / "widget-workspace").write_text("damaged")
    with pytest.raises(HeinzelError, match="identity is damaged"):
        devices.widget_check(token, "owner", workspace)
