"""The widget credential is confined to one paired device and one read-only route."""

import json
import os
import select
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from heinzel import Config, HeinzelError
from heinzel.heads import web
from heinzel.heads.web import _browser_bridge
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
    before = datetime.now(UTC)
    response = client.get("/api/_web/widget-snapshot", headers=widget)
    after = datetime.now(UTC)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert (body["workspace_id"], body["version"], body["fresh_for_seconds"]) == (credential["workspace_id"], 1, 300)
    generated = datetime.fromisoformat(body["generated_at"])
    assert generated.utcoffset() == timedelta(0)
    assert before <= generated <= after
    assert generated + timedelta(seconds=body["fresh_for_seconds"]) == generated + timedelta(minutes=5)
    assert (body["awake"], body["waiting"], body["roster"]) == (0, 0, [])
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
    sessions.append(
        SimpleNamespace(session_id="retired", name="retired", state="retired", activity="idle", waiting_count=1)
    )
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
    assert body["waiting"] == 45
    assert len(body["roster"]) == 40
    assert len(body["roster"][0]["name"]) == 128
    assert body["roster"][0]["asks"] == 9999
    assert "secret" not in body["roster"][0]
    assert client.delete("/api/_web/widget-credential", headers=device).status_code == 200
    assert client.get("/api/_web/widget-snapshot", headers=widget).status_code == 401
    assert client.get("/api/system/notify/list", headers=device).status_code == 200


def test_snapshot_freshness_is_renewed_on_each_fetch(make_kernel, tmp_path, monkeypatch):
    now = [datetime(2026, 1, 1, tzinfo=UTC)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now[0]

    monkeypatch.setattr("heinzel.heads.web._widget.datetime", Clock)
    devices = Devices(tmp_path / "devices", clock=lambda: now[0])
    client = TestClient(
        web.build(make_kernel(builtins=True), port=8000, token=TOKEN, devices=devices, static=None),
        base_url="http://127.0.0.1:8000",
    )
    paired = client.post("/api/_web/device", headers={"Authorization": f"Bearer {TOKEN}"}, json={}).json()
    issued = client.post("/api/_web/widget-credential", headers={"Authorization": f"Bearer {paired['token']}"}).json()
    widget = {"Authorization": "Bearer " + issued["token"], "X-Hz-Workspace-Id": issued["workspace_id"]}
    first = client.get("/api/_web/widget-snapshot", headers=widget).json()
    first_at = datetime.fromisoformat(first["generated_at"])
    assert first["version"] == 1
    assert first["fresh_for_seconds"] == 300
    assert first_at.utcoffset() == timedelta(0)
    now[0] += timedelta(seconds=301)
    assert now[0] >= first_at + timedelta(seconds=first["fresh_for_seconds"])
    refreshed = client.get("/api/_web/widget-snapshot", headers=widget).json()
    assert datetime.fromisoformat(refreshed["generated_at"]) == now[0]
    assert refreshed["fresh_for_seconds"] == 300


def test_widget_identity_fails_closed_when_damaged(tmp_path):
    devices = Devices(tmp_path / "devices")
    device, _ = devices.pair("phone")
    token, workspace, _ = devices.widget_issue(device.id, "owner")
    assert devices.widget_check(token, "other-owner", workspace) is None
    assert Devices(devices.directory).widget_check(token, "owner", workspace) == device.id
    (devices.directory / "widget-workspace").write_text("damaged")
    with pytest.raises(HeinzelError, match="identity is damaged"):
        devices.widget_check(token, "owner", workspace)


def test_persisted_check_in_proposal_counts_as_attention(make_kernel, tmp_path):
    devices = Devices(tmp_path / "devices")
    client = TestClient(
        web.build(make_kernel(builtins=True), port=8000, token=TOKEN, devices=devices, static=None),
        base_url="http://127.0.0.1:8000",
    )
    session = {"Authorization": f"Bearer {TOKEN}"}
    paired = client.post("/api/_web/device", headers=session, json={"name": "phone"}).json()
    device = {"Authorization": f"Bearer {paired['token']}"}
    created = client.post("/api/heinzel/new-chat", headers=device, json={"request_id": "widget-create"})
    assert created.status_code == 200, created.text
    session_id = created.json()["session_id"]
    proposal = client.post(
        "/api/heinzel/propose-check-in",
        headers=device,
        json={"session_id": session_id, "request_id": "widget-propose", "timezone": "UTC"},
    )
    assert proposal.status_code == 200, proposal.text
    issued = client.post("/api/_web/widget-credential", headers=device).json()
    widget = {"Authorization": "Bearer " + issued["token"], "X-Hz-Workspace-Id": issued["workspace_id"]}
    snapshot = client.get("/api/_web/widget-snapshot", headers=widget).json()
    assert snapshot["waiting"] == 1
    assert snapshot["roster"][0]["asks"] == 1


def test_widget_bearer_is_rejected_by_other_transports(make_kernel, tmp_path, monkeypatch):
    config_home = tmp_path / "config"
    config_home.mkdir()
    (config_home / "config.toml").write_text("[hz]\nui_port = 8000\n")
    monkeypatch.setenv("HZ_CONFIG_HOME", str(config_home))
    devices = Devices(tmp_path / "devices")
    client = TestClient(
        web.build(
            make_kernel(builtins=True, config=Config.load(tmp_path / "project")),
            port=8000,
            token=TOKEN,
            devices=devices,
            static=None,
            bridge_token="bridge-secret",
        ),
        base_url="http://127.0.0.1:8000",
    )
    paired = client.post("/api/_web/device", headers={"Authorization": f"Bearer {TOKEN}"}, json={}).json()
    device = {"Authorization": f"Bearer {paired['token']}"}
    issued = client.post("/api/_web/widget-credential", headers=device).json()
    widget = {"Authorization": "Bearer " + issued["token"], "X-Hz-Workspace-Id": issued["workspace_id"]}
    assert client.put("/api/_web/mcp", headers=device, json={"enabled": True}).json()["enabled"] is True
    for method, path in [
        ("get", "/api/system/notify/stream?once=1"),
        ("post", "/api/heinzel/new-chat"),
        ("post", "/api/_web/device"),
        ("delete", "/api/_web/device"),
        ("delete", "/api/_web/widget-credential"),
    ]:
        assert getattr(client, method)(path, headers=widget).status_code == 401
    assert client.post("/mcp", headers=widget).status_code == 401
    for extra in ({"Host": "evil.test"}, {"Origin": "https://evil.test"}, {"Sec-Fetch-Site": "cross-site"}):
        assert client.get("/api/_web/widget-snapshot", headers={**widget, **extra}).status_code == 403
    for value in ("é" * 32, "x" * 64):
        assert devices.widget_check(issued["token"], "owner", value) is None
    ws_headers = {"Origin": "http://127.0.0.1:8000", "Host": "127.0.0.1:8000"}
    with client.websocket_connect("/api/terminal", headers=ws_headers) as socket:
        socket.send_json({"token": issued["token"]})
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()
    with client.websocket_connect(_browser_bridge.PATH, headers=ws_headers) as socket:
        socket.send_json({"hello": "hz-browser-bridge", "version": 1, "token": issued["token"]})
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()


@pytest.mark.parametrize("name", ["devices.json", "devices.lock", "widget-workspace"])
@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "mode"])
def test_widget_rejects_unsafe_storage(tmp_path, name, kind):
    devices = Devices(tmp_path / "devices")
    device, _ = devices.pair("phone")
    token, workspace, _ = devices.widget_issue(device.id, "owner")
    path = devices.directory / name
    if kind == "mode":
        path.chmod(0o644)
    elif kind == "hardlink":
        os.link(path, tmp_path / "link")
    else:
        path.unlink()
        if kind == "fifo":
            os.mkfifo(path, 0o600)
        else:
            path.symlink_to(tmp_path / "target")
    with pytest.raises((OSError, HeinzelError)):
        devices.widget_check(token, "owner", workspace)


def test_widget_rejects_other_writable_directory(tmp_path):
    devices = Devices(tmp_path / "devices")
    device, _ = devices.pair("phone")
    token, workspace, _ = devices.widget_issue(device.id, "owner")
    devices.directory.chmod(0o777)
    with pytest.raises(HeinzelError):
        devices.widget_check(token, "owner", workspace)


def test_widget_rejects_linked_directory_and_corrupt_device_store(tmp_path):
    devices = Devices(tmp_path / "devices")
    device, _ = devices.pair("phone")
    token, workspace, _ = devices.widget_issue(device.id, "owner")
    devices.path.write_text("{broken")
    with pytest.raises(HeinzelError, match="device store is damaged"):
        devices.widget_check(token, "owner", workspace)
    devices.directory.rename(tmp_path / "moved")
    devices.directory.symlink_to(tmp_path / "moved", target_is_directory=True)
    with pytest.raises(OSError, match=r"directory|symbolic"):
        devices.widget_check(token, "owner", workspace)


def test_two_devices_rotation_inactivity_and_cross_process_revocation(tmp_path):
    now = [datetime.now(UTC)]
    devices = Devices(tmp_path / "devices", clock=lambda: now[0])
    first, _ = devices.pair("first")
    second, _ = devices.pair("second")
    old, workspace, _ = devices.widget_issue(first.id, "owner")
    new, _, _ = devices.widget_issue(first.id, "owner")
    other, _, _ = devices.widget_issue(second.id, "owner")
    assert devices.widget_check(old, "owner", workspace) is None
    script = (
        "import json,sys; from pathlib import Path; from heinzel.heads.web._devices import Devices; "
        "d=Devices(Path(sys.argv[1])); print(json.dumps(d.widget_issue(sys.argv[2], 'owner'), default=str))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(devices.directory), first.id],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    cross_process_token = json.loads(result.stdout)[0]
    assert devices.widget_check(cross_process_token, "owner", workspace) == first.id
    revoke_script = (
        "import sys; from pathlib import Path; from heinzel.heads.web._devices import Devices; "
        "Devices(Path(sys.argv[1])).revoke(sys.argv[2])"
    )
    subprocess.run(
        [sys.executable, "-c", revoke_script, str(devices.directory), first.id],
        check=True,
        capture_output=True,
        timeout=10,
    )
    assert devices.widget_check(new, "owner", workspace) is None
    assert devices.widget_check(cross_process_token, "owner", workspace) is None
    assert devices.widget_check(other, "owner", workspace) == second.id
    with devices._locked() as folder:
        records = devices._read(folder)
        devices._write(
            folder,
            [
                record.model_copy(update={"last_used": now[0] - timedelta(days=31)})
                if record.id == second.id
                else record
                for record in records
            ],
        )
    assert devices.widget_check(other, "owner", workspace) is None


def _concurrent_widget_calls(devices, device_id, actions):
    """Start children together while the parent holds the store lock, then release all contenders."""
    script = (
        "import json,sys; from pathlib import Path; from heinzel.heads.web._devices import Devices; "
        "d=Devices(Path(sys.argv[1])); print('ready',flush=True); input(); print('attempt',flush=True); "
        "r=d.widget_issue(sys.argv[2], 'owner') if sys.argv[3]=='issue' else d.revoke(sys.argv[2]); "
        "print(json.dumps(r,default=str),flush=True)"
    )
    children = []
    try:
        with devices._locked():
            for action in actions:
                child = subprocess.Popen(
                    [sys.executable, "-c", script, str(devices.directory), device_id, action],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                children.append(child)
            for child in children:
                assert select.select([child.stdout], [], [], 10)[0]
                assert child.stdout.readline().strip() == "ready"
            for child in children:
                child.stdin.write("go\n")
                child.stdin.flush()
            for child in children:
                assert select.select([child.stdout], [], [], 10)[0]
                assert child.stdout.readline().strip() == "attempt"
        results = []
        for child in children:
            output, error = child.communicate(timeout=10)
            assert child.returncode == 0, error
            results.append(json.loads(output))
        return results
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)


def test_concurrent_widget_rotation_and_parent_revocation(tmp_path):
    devices = Devices(tmp_path / "devices")
    device, _ = devices.pair("phone")
    original, workspace, _ = devices.widget_issue(device.id, "owner")
    rotations = _concurrent_widget_calls(devices, device.id, ["issue", "issue"])
    assert all(result is not None for result in rotations)
    assert sum(devices.widget_check(result[0], "owner", workspace) is not None for result in rotations) == 1
    assert devices.widget_check(original, "owner", workspace) is None
    racing = _concurrent_widget_calls(devices, device.id, ["issue", "issue", "revoke"])
    assert devices.paired() == []
    for result in [*rotations, *racing[:2]]:
        if result is not None:
            assert devices.widget_check(result[0], "owner", workspace) is None
    assert devices.widget_issue(device.id, "owner") is None
