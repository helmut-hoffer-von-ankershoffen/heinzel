"""Fenced issuance is idempotent, pairing-bound and never stores raw widget tokens."""

import json
import os
import stat
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from heinzel import HeinzelError
from heinzel.heads import web
from heinzel.heads.web._devices import Devices, digest
from heinzel.heads.web._widget_fence import FenceConflictError, Request

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


@pytest.mark.parametrize("winner", ["issue", "revoke"])
def test_recovery_revoke_fences_delayed_issue_and_replays_exactly(tmp_path, winner):
    devices = Devices(tmp_path / "devices")
    device, token = devices.pair("phone")
    fence = devices.widget_fence_negotiate(device.id, token, "owner")
    assert fence is not None
    issue = Request(fence.epoch, fence.revision, "a" * 32, "issue", "b" * 64)
    revoke = Request(fence.epoch, fence.revision, "c" * 32, "revoke", None)
    first, delayed = (issue, revoke) if winner == "issue" else (revoke, issue)
    result = devices.widget_fence_mutate(device.id, token, "owner", first)
    assert result is not None
    with pytest.raises(FenceConflictError):
        devices.widget_fence_mutate(device.id, token, "owner", delayed)
    restarted = Devices(devices.directory)
    assert restarted.widget_fence_mutate(device.id, token, "owner", first) == result
    status = restarted.widget_fence_negotiate(device.id, token, "owner")
    assert status is not None
    assert status.revision != fence.revision


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


def test_retry_does_not_acknowledge_until_directory_sync_succeeds(tmp_path, monkeypatch):
    devices = Devices(tmp_path / "devices")
    device, token = devices.pair("phone")
    fence = devices.widget_fence_negotiate(device.id, token, "owner")
    assert fence is not None
    request = Request(fence.epoch, fence.revision, "a" * 32, "issue", "b" * 64)
    real_fsync = os.fsync

    def fail_directory(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            msg = "directory sync failed"
            raise OSError(msg)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_directory)
    with pytest.raises(OSError, match="directory sync failed"):
        devices.widget_fence_mutate(device.id, token, "owner", request)
    with pytest.raises(OSError, match="directory sync failed"):
        devices.widget_fence_mutate(device.id, token, "owner", request)
    with pytest.raises(OSError, match="directory sync failed"):
        devices.widget_fence_negotiate(device.id, token, "owner")
    monkeypatch.setattr(os, "fsync", real_fsync)
    committed = devices.widget_fence_mutate(device.id, token, "owner", request)
    assert committed is not None
    assert Devices(devices.directory).widget_fence_mutate(device.id, token, "owner", request) == committed


def test_pairing_ids_are_unique_and_duplicate_storage_is_rejected(tmp_path, monkeypatch):
    devices = Devices(tmp_path / "devices")
    first, _ = devices.pair("first")
    real_token_hex = __import__("secrets").token_hex
    unique = "01234567" if first.id != "01234567" else "89abcdef"
    candidates = iter([first.id, unique])

    def collision_then_unique(size):
        return next(candidates) if size == 4 else real_token_hex(size)

    monkeypatch.setattr("heinzel.heads.web._devices.secrets.token_hex", collision_then_unique)
    second, _ = devices.pair("second")
    assert second.id == unique
    assert second.id != first.id
    with devices._locked() as folder:
        devices._write(folder, [first, second.model_copy(update={"id": first.id})])
    with pytest.raises(HeinzelError, match="ambiguous"):
        devices.widget_issue(first.id, "owner")


@pytest.mark.parametrize("partial_first", [True, False])
def test_partial_fence_does_not_block_another_device(tmp_path, partial_first):
    devices = Devices(tmp_path / "devices")
    first, _ = devices.pair("first")
    second, _ = devices.pair("second")
    partial, healthy = (first, second) if partial_first else (second, first)
    a, workspace, _ = devices.widget_issue(partial.id, "owner")
    b, _, _ = devices.widget_issue(healthy.id, "owner")
    with devices._locked() as folder:
        assert devices._fence_marker(folder, partial, create=True)
    with pytest.raises(HeinzelError, match="fence is damaged"):
        devices.widget_check(a, "owner", workspace)
    assert devices.widget_check(b, "owner", workspace) == healthy.id


@pytest.mark.parametrize("damage", ["numeric_expiry", "wrong_revision", "missing_receipt"])
def test_damaged_persisted_fence_does_not_become_a_fresh_pairing(tmp_path, damage):
    devices = Devices(tmp_path / "devices")
    device, paired_token = devices.pair("phone")
    fence = devices.widget_fence_negotiate(device.id, paired_token, "owner")
    assert fence is not None
    request = Request(fence.epoch, fence.revision, "a" * 32, "issue", "b" * 64)
    assert devices.widget_fence_mutate(device.id, paired_token, "owner", request) is not None
    data = json.loads(devices.path.read_text())
    persisted = data[0]["widget_fence"]
    if damage == "numeric_expiry":
        persisted["receipt"]["expires"] = 1800000000
        data[0]["widget"]["expires"] = 1800000000
    elif damage == "wrong_revision":
        persisted["revision"] = "f" * 32
    else:
        persisted["receipt"] = None
    devices.path.write_text(json.dumps(data))
    with pytest.raises(HeinzelError, match="damaged"):
        devices.widget_fence_negotiate(device.id, paired_token, "owner")


def test_separate_processes_cannot_both_rotate_same_revision(tmp_path):
    devices = Devices(tmp_path / "devices")
    device, paired_token = devices.pair("phone")
    fence = devices.widget_fence_negotiate(device.id, paired_token, "owner")
    assert fence is not None
    script = (
        "import sys,time; from pathlib import Path; from heinzel.heads.web._devices import Devices; "
        "from heinzel.heads.web._widget_fence import Request, FenceConflictError; "
        "d=Devices(Path(sys.argv[1])); r=Request(sys.argv[4],sys.argv[5],sys.argv[6]*32,'issue',sys.argv[6]*64); "
        "Path(sys.argv[7],sys.argv[6]).touch(); "
        "exec(\"while not Path(sys.argv[7],'release').exists():\\n time.sleep(0.01)\"); "
        "exec(\"try:\\n d.widget_fence_mutate(sys.argv[2],sys.argv[3],'owner',r); print('won')\\n"
        "except FenceConflictError:\\n print('lost')\")"
    )
    args = [sys.executable, "-c", script, str(devices.directory), device.id, paired_token, fence.epoch, fence.revision]
    processes = [
        subprocess.Popen([*args, value, str(tmp_path)], stdout=subprocess.PIPE, text=True) for value in ("a", "b")
    ]
    deadline = time.monotonic() + 5
    while not all((tmp_path / value).exists() for value in ("a", "b")) and time.monotonic() < deadline:
        time.sleep(0.01)
    ready = all((tmp_path / value).exists() for value in ("a", "b"))
    (tmp_path / "release").touch()
    outcomes = [process.communicate(timeout=10)[0].strip() for process in processes]
    assert ready
    assert all(process.returncode == 0 for process in processes)
    assert sorted(outcomes) == ["lost", "won"]


def test_legacy_rotation_racing_fence_negotiation_cannot_survive_it(tmp_path):
    devices = Devices(tmp_path / "devices")
    device, token = devices.pair("phone")
    with ThreadPoolExecutor(max_workers=2) as pool:
        issue = pool.submit(devices.widget_issue, device.id, "owner", token)
        negotiate = pool.submit(devices.widget_fence_negotiate, device.id, token, "owner")
        legacy = issue.result()
        fence = negotiate.result()
    assert fence is not None
    if legacy:
        assert devices.widget_check(legacy[0], "owner", legacy[1]) is None
    assert devices.widget_issue(device.id, "owner", token) is None


@pytest.mark.parametrize("legacy_first", [True, False])
def test_legacy_rotation_and_negotiation_in_both_orders(tmp_path, legacy_first):
    devices = Devices(tmp_path / "devices")
    device, token = devices.pair("phone")
    before = devices.widget_issue(device.id, "owner", token) if legacy_first else None
    assert devices.widget_fence_negotiate(device.id, token, "owner") is not None
    if before:
        assert devices.widget_check(before[0], "owner", before[1]) is None
    assert devices.widget_issue(device.id, "owner", token) is None
