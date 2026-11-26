"""Paired devices (ADR 0046): the session token pairs once, the device token reopens the app, revoking ends it."""

import json
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from heinzel import HeinzelError
from heinzel.heads import web
from heinzel.heads.cli import build as cli
from heinzel.modules.system.client._devices import (
    MAX_DEVICES,
    MAX_STORE_BYTES,
    PREFIX,
    Device,
    Devices,
    WidgetCredential,
    WidgetFence,
    WidgetReceipt,
    digest,
)

pytestmark = pytest.mark.integration

TOKEN = "s3ssion-token-for-tests"
BASE = "http://127.0.0.1:8000"
SESSION = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def devices(tmp_path) -> Devices:
    return Devices(tmp_path / "web")


@pytest.fixture
def client(make_kernel, devices):
    return TestClient(
        web.build(make_kernel(builtins=True), port=8000, token=TOKEN, static=None, devices=devices), base_url=BASE
    )


def pair(client, name: str = "Chrome on macOS (installed)") -> dict:
    response = client.post("/api/_web/device", json={"name": name}, headers=SESSION)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_session_token_pairs_a_device_whose_token_then_opens_the_app(client, devices):
    paired = pair(client)
    assert paired["token"].startswith(PREFIX)
    assert len(paired["token"]) > 40
    device = {"Authorization": f"Bearer {paired['token']}"}
    assert client.get("/api/system/notify/list", headers=device).status_code == 200
    stored = devices.path.read_text(encoding="utf-8")
    assert paired["token"] not in stored  # only its digest is kept
    assert digest(paired["token"]) in stored
    assert stat.S_IMODE(devices.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(devices.directory.stat().st_mode) == 0o700


def test_a_device_token_never_pairs_another_one(client):
    device = {"Authorization": f"Bearer {pair(client)['token']}"}
    refused = client.post("/api/_web/device", json={"name": "x"}, headers=device)
    assert refused.status_code == 403
    assert "pair with the link" in refused.json()["error"]


def test_unknown_or_malformed_tokens_are_refused(client):
    for header in (f"Bearer {PREFIX}not-a-real-one", "Bearer ", f"Basic {TOKEN}", PREFIX, ""):
        assert client.get("/api/system/notify/list", headers={"Authorization": header}).status_code == 401


def test_the_rest_heads_refusals_still_apply_to_device_tokens(client):
    device = {"Authorization": f"Bearer {pair(client)['token']}"}
    assert (
        client.get("/api/system/notify/list", headers={**device, "Origin": "http://evil.example.test"}).status_code
        == 403
    )
    assert client.get("/api/system/notify/list", headers={**device, "Sec-Fetch-Site": "cross-site"}).status_code == 403


def test_the_app_forgets_its_own_device_and_the_token_stops_working(client, devices):
    token = pair(client)["token"]
    device = {"Authorization": f"Bearer {token}"}
    paired_id = devices.paired()[0].id
    assert client.delete("/api/_web/device", headers=device).json()["revoked"] == [paired_id]
    assert devices.paired() == []
    assert client.get("/api/system/notify/list", headers=device).status_code == 401


def test_hz_ui_lists_and_revokes_devices(make_kernel, devices, monkeypatch):
    monkeypatch.setattr(web, "devices_for_user", lambda: devices)
    first, _ = devices.pair("Safari on macOS")
    devices.pair("Chrome on macOS")
    runner = CliRunner()
    app = cli(make_kernel(builtins=True))
    listed = runner.invoke(app, ["ui", "serve", "--devices"])
    assert listed.exit_code == 0
    assert "Safari on macOS" in listed.output
    assert first.id in listed.output
    revoked = runner.invoke(app, ["ui", "serve", "--revoke", first.id])
    assert revoked.exit_code == 0
    assert [d.name for d in devices.paired()] == ["Chrome on macOS"]
    assert runner.invoke(app, ["ui", "serve", "--revoke", "nope"]).exit_code == 1
    assert runner.invoke(app, ["ui", "serve", "--revoke", "all"]).exit_code == 0
    assert devices.paired() == []


def test_pairing_is_bounded_and_names_are_cleaned(devices):
    device, _ = devices.pair("a" + chr(0x202E) + "bad" + chr(0) + " name " + "x" * 200)
    assert device.name.startswith("abad name")
    assert len(device.name) == 60
    with devices._locked() as folder:
        devices._write(
            folder, [device.model_copy(update={"id": f"{i:08x}", "sha256": f"{i:064x}"}) for i in range(MAX_DEVICES)]
        )
    with pytest.raises(HeinzelError, match="devices are paired already"):
        devices.pair("one too many")


def test_a_damaged_file_pairs_nothing_and_accepts_nothing(devices):
    devices.directory.mkdir(parents=True)
    devices.path.write_text("{broken", encoding="utf-8")
    assert devices.paired() == []
    assert devices.check(PREFIX + "x") is None


def test_a_device_unused_for_a_month_stops_working(tmp_path):
    now = [datetime(2026, 10, 1, tzinfo=UTC)]
    devices = Devices(tmp_path / "web", clock=lambda: now[0])
    _, token = devices.pair("Chrome on macOS")
    now[0] += timedelta(days=29)
    assert devices.check(token) is not None  # and noted as used
    now[0] += timedelta(days=29)
    assert devices.check(token) is not None
    now[0] += timedelta(days=31)
    assert devices.check(token) is None
    assert devices.paired() == []


def test_pairing_reclaims_expired_pool_but_not_the_exact_ttl_boundary(tmp_path):
    now = [datetime(2026, 10, 1, tzinfo=UTC)]
    devices = Devices(tmp_path / "web", clock=lambda: now[0])
    first, token = devices.pair("first")
    with devices._locked() as folder:
        devices._write(
            folder,
            [
                first,
                *[
                    first.model_copy(update={"id": f"{i:08x}", "sha256": f"{i:064x}"})
                    for i in range(MAX_DEVICES)
                    if f"{i:08x}" != first.id
                ][: MAX_DEVICES - 1],
            ],
        )
    now[0] += timedelta(days=30)
    with pytest.raises(HeinzelError, match="devices are paired already"):
        devices.pair("at boundary")
    assert len(devices.paired()) == MAX_DEVICES
    assert any(device.sha256 == digest(token) for device in devices.paired())
    now[0] += timedelta(microseconds=1)
    new, new_token = devices.pair("new")
    assert [device.id for device in devices.paired()] == [new.id]
    assert devices.check(token) is None
    assert devices.check(new_token) is not None


def test_full_widget_pool_remains_readable_by_the_previous_writer(tmp_path):
    now = datetime(2026, 10, 1, tzinfo=UTC)
    owner = "a" * 128
    workspace, revision, attempt = "a" * 64, "b" * 32, "c" * 32
    widget = WidgetCredential(sha256="d" * 64, owner=owner, workspace=workspace, expires=now)
    receipt = WidgetReceipt(
        attempt=attempt,
        operation="issue",
        previous_revision="e" * 32,
        commitment=widget.sha256,
        revision=revision,
        expires=now,
    )
    fence = WidgetFence(
        epoch="f" * 32, revision=revision, initial_revision="e" * 32, owner=owner, workspace=workspace, receipt=receipt
    )
    devices = Devices(tmp_path / "web", clock=lambda: now)
    records = [
        Device(
            id=f"{i:08x}",
            name="a" * 60,
            created=now,
            last_used=now,
            sha256=f"{i:064x}",
            widget=widget,
            widget_fence=fence,
        )
        for i in range(MAX_DEVICES)
    ]
    with devices._locked() as folder:
        devices._write(folder, records)
    assert len(devices.path.read_bytes()) <= MAX_STORE_BYTES
    assert len(devices.paired()) == MAX_DEVICES
    assert len(json.loads(devices.path.read_text(encoding="utf-8"))) == MAX_DEVICES
    before = devices.path.read_bytes()
    assert before == (json.dumps(json.loads(before), indent=1) + "\n").encode()
    escaped_owner = "😀" * 128
    oversized = [
        Device.model_validate(
            record.model_dump()
            | {
                "widget": record.widget.model_dump() | {"owner": escaped_owner},
                "widget_fence": record.widget_fence.model_dump() | {"owner": escaped_owner},
            }
        )
        for record in records
    ]
    with devices._locked() as folder, pytest.raises(HeinzelError, match="exceeds its safe size"):
        devices._write(folder, oversized)
    assert devices.path.read_bytes() == before


def test_widget_parent_remains_valid_at_ttl_boundary_and_expires_afterward(tmp_path):
    now = [datetime(2026, 10, 1, tzinfo=UTC)]
    devices = Devices(tmp_path / "web", clock=lambda: now[0])
    device, token = devices.pair("parent")
    now[0] += timedelta(days=30)
    child, workspace, _ = devices.widget_issue(device.id, "owner", token)
    assert devices.widget_check(child, "owner", workspace) == device.id
    now[0] += timedelta(microseconds=1)
    assert devices.widget_check(child, "owner", workspace) is None
    assert devices.check(token) is None


def test_concurrent_pairing_cannot_exceed_capacity(tmp_path):
    devices = Devices(tmp_path / "web")
    first, _ = devices.pair("first")
    with devices._locked() as folder:
        devices._write(
            folder, [first.model_copy(update={"id": f"{i:08x}", "sha256": f"{i:064x}"}) for i in range(MAX_DEVICES - 1)]
        )

    def pair_once():
        try:
            devices.pair("concurrent")
        except HeinzelError:
            return False
        else:
            return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: pair_once(), range(2)))
    assert sorted(results) == [False, True]
    assert len(devices.paired()) == MAX_DEVICES
