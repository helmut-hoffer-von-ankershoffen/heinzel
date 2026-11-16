"""Fenced issuance is idempotent, pairing-bound and never stores raw widget tokens."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from heinzel import HeinzelError
from heinzel.heads import web
from heinzel.heads.web._devices import Devices, digest
from heinzel.heads.web._widget_fence import Request

pytestmark = pytest.mark.integration
SESSION = "fixture-session-token"


def test_fenced_api_replay_and_legacy_rejection(make_kernel, tmp_path):
    devices = Devices(tmp_path / "devices")
    client = TestClient(
        web.build(make_kernel(builtins=True), port=8000, token=SESSION, devices=devices, static=None),
        base_url="http://127.0.0.1:8000",
    )
    session = {"Authorization": f"Bearer {SESSION}"}
    assert client.post("/api/_web/widget-fence", headers=session).status_code == 403
    paired = client.post("/api/_web/device", headers=session, json={}).json()
    headers = {"Authorization": f"Bearer {paired['token']}"}
    old = client.post("/api/_web/widget-credential", headers=headers).json()
    fence_response = client.post("/api/_web/widget-fence", headers=headers)
    assert fence_response.status_code == 200
    fence = fence_response.json()
    assert client.post("/api/_web/widget-credential", headers=headers).status_code == 403
    assert client.delete("/api/_web/widget-credential", headers=headers).status_code == 409
    old_headers = {"Authorization": f"Bearer {old['token']}", "X-Hz-Workspace-Id": old["workspace_id"]}
    assert client.get("/api/_web/widget-snapshot", headers=old_headers).status_code == 401
    secret = "hzw_" + "x" * 43
    payload = {
        "epoch": fence["epoch"],
        "revision": fence["revision"],
        "attempt": "a" * 32,
        "operation": "issue",
        "commitment": digest(secret),
    }
    issued = client.post("/api/_web/widget-fenced-credential", headers=headers, json=payload)
    assert issued.status_code == 200
    assert issued.headers["cache-control"] == "no-store"
    assert client.post("/api/_web/widget-fenced-credential", headers=headers, json=payload).json() == issued.json()
    assert (
        client.post(
            "/api/_web/widget-fenced-credential", headers=headers, json={**payload, "commitment": "b" * 64}
        ).status_code
        == 409
    )
    assert secret not in devices.path.read_text()
    widget = {"Authorization": f"Bearer {secret}", "X-Hz-Workspace-Id": fence["workspace_id"]}
    assert client.get("/api/_web/widget-snapshot", headers=widget).status_code == 200
    assert client.post("/api/_web/widget-fenced-credential", headers=widget, json=payload).status_code == 401
    assert client.post("/api/_web/widget-fenced-credential", headers=headers, content=b"x" * 4097).status_code == 413
    revoke = {
        "epoch": fence["epoch"],
        "revision": issued.json()["revision"],
        "attempt": "c" * 32,
        "operation": "revoke",
        "commitment": None,
    }
    revoked = client.post("/api/_web/widget-fenced-credential", headers=headers, json=revoke)
    assert revoked.status_code == 200
    assert client.post("/api/_web/widget-fenced-credential", headers=headers, json=revoke).json() == revoked.json()
    assert client.get("/api/_web/widget-snapshot", headers=widget).status_code == 401
    assert client.post("/api/_web/widget-fenced-credential", headers=headers, json=payload).status_code == 409


def test_competing_mutations_have_one_winner_and_corrupt_fence_fails_closed(tmp_path):
    devices = Devices(tmp_path / "devices")
    device, paired_token = devices.pair("phone")
    fence = devices.widget_fence_negotiate(device.id, paired_token, "owner")
    assert fence is not None
    operations = [Request(fence.epoch, fence.revision, value * 32, "issue", value * 64) for value in ("a", "b")]

    def run(request):
        try:
            return devices.widget_fence_mutate(device.id, paired_token, "owner", request)
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(run, operations))
    assert sum(result is not None for result in outcomes) == 1
    data = json.loads(devices.path.read_text())
    del data[0]["widget_fence"]
    devices.path.write_text(json.dumps(data))
    with pytest.raises(HeinzelError):
        devices.widget_fence_negotiate(device.id, paired_token, "owner")
    assert devices.widget_issue(device.id, "owner") is None


def test_lost_reply_and_failed_write_preserve_fenced_outcome(tmp_path, monkeypatch):
    devices = Devices(tmp_path / "devices")
    device, paired_token = devices.pair("phone")
    fence = devices.widget_fence_negotiate(device.id, paired_token, "owner")
    assert fence is not None
    request = Request(fence.epoch, fence.revision, "a" * 32, "issue", "b" * 64)
    original_write = devices._write

    def fail_write(*_args):
        msg = "fictional storage failure"
        raise OSError(msg)

    monkeypatch.setattr(devices, "_write", fail_write)
    with pytest.raises(OSError, match="fictional storage failure"):
        devices.widget_fence_mutate(device.id, paired_token, "owner", request)
    monkeypatch.setattr(devices, "_write", original_write)
    assert devices.widget_fence_negotiate(device.id, paired_token, "owner") == fence
    committed = devices.widget_fence_mutate(device.id, paired_token, "owner", request)
    restarted = Devices(devices.directory)
    assert restarted.widget_fence_mutate(device.id, paired_token, "owner", request) == committed


def test_pairing_replacement_with_same_short_id_cannot_use_old_parent(tmp_path):
    devices = Devices(tmp_path / "devices")
    old, old_token = devices.pair("old")
    assert devices.widget_fence_negotiate(old.id, old_token, "owner") is not None
    devices.revoke(old.id)
    replacement, replacement_token = devices.pair("replacement")
    with devices._locked() as folder:
        devices._write(folder, [replacement.model_copy(update={"id": old.id})])
    assert devices.widget_fence_negotiate(old.id, old_token, "owner") is None
    assert devices.widget_issue(old.id, "owner", old_token) is None
    assert not devices.widget_revoke(old.id, old_token)
    assert devices.widget_fence_negotiate(old.id, replacement_token, "owner") is not None


def test_missing_fence_marker_cannot_authorize_a_snapshot(tmp_path):
    devices = Devices(tmp_path / "devices")
    device, paired_token = devices.pair("phone")
    fence = devices.widget_fence_negotiate(device.id, paired_token, "owner")
    assert fence is not None
    secret = "hzw_" + "x" * 43
    request = Request(fence.epoch, fence.revision, "a" * 32, "issue", digest(secret))
    assert devices.widget_fence_mutate(device.id, paired_token, "owner", request) is not None
    (devices.directory / ("widget-fenced-" + device.sha256)).unlink()
    with pytest.raises(HeinzelError):
        devices.widget_check(secret, "owner", fence.workspace)
    assert devices.widget_issue(device.id, "owner") is None
