"""Paired devices (ADR 0046): the session token pairs once, the device token reopens the app, revoking ends it."""

import stat

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from heinzel import HeinzelError
from heinzel.heads import web
from heinzel.heads.cli import build as cli
from heinzel.heads.web._devices import MAX_DEVICES, PREFIX, Devices, digest

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
    listed = runner.invoke(app, ["ui", "--devices"])
    assert listed.exit_code == 0
    assert "Safari on macOS" in listed.output
    assert first.id in listed.output
    revoked = runner.invoke(app, ["ui", "--revoke", first.id])
    assert revoked.exit_code == 0
    assert [d.name for d in devices.paired()] == ["Chrome on macOS"]
    assert runner.invoke(app, ["ui", "--revoke", "nope"]).exit_code == 1
    assert runner.invoke(app, ["ui", "--revoke", "all"]).exit_code == 0
    assert devices.paired() == []


def test_pairing_is_bounded_and_names_are_cleaned(devices):
    device, _ = devices.pair("a" + chr(0x202E) + "bad" + chr(0) + " name " + "x" * 200)
    assert device.name.startswith("abad name")
    assert len(device.name) == 60
    for index in range(MAX_DEVICES - 1):
        devices.pair(str(index))
    with pytest.raises(HeinzelError, match="devices are paired already"):
        devices.pair("one too many")


def test_a_damaged_file_pairs_nothing_and_accepts_nothing(devices):
    devices.directory.mkdir(parents=True)
    devices.path.write_text("{broken", encoding="utf-8")
    assert devices.paired() == []
    assert devices.check(PREFIX + "x") is None


def test_a_device_unused_for_a_month_stops_working(tmp_path):
    from datetime import UTC, datetime, timedelta

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
