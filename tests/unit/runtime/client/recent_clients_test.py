"""Authenticated use is coarse evidence, independent of paired credentials."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from heinzel.heads import web
from heinzel.modules.system.client import Devices, RecentUse

pytestmark = pytest.mark.unit


def test_missing_fresh_stale_future_and_expired():
    now = datetime(2026, 11, 27, 12, tzinfo=UTC)
    evidence = RecentUse()
    assert evidence.recent(now) == []
    evidence.observe(now)
    assert evidence.recent(now - timedelta(seconds=1)) == []
    assert evidence.recent(now + timedelta(hours=2))[0].presence == "fresh"
    assert evidence.recent(now + timedelta(hours=2, seconds=1))[0].presence == "stale"
    assert evidence.recent(now + timedelta(days=30)) == []
    assert RecentUse().recent(now) == []
    for _ in range(10):
        evidence.observe(now)
    assert [item.model_dump() for item in evidence.recent(now)] == [
        {"kind": "unknown", "platform": "unknown", "presence": "fresh"}
    ]


def test_route_requires_use_not_pairing_and_is_scoped_to_server(make_kernel, tmp_path):
    devices = Devices(tmp_path / "web")
    _, device_token = devices.pair("Fictional workstation")
    token = "t" * 43
    kernel = make_kernel(builtins=True)
    app = web.build(kernel, port=8000, token=token, static=None, devices=devices)
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/recent-clients").status_code == 401
    for _ in range(2):
        response = client.get("/api/recent-clients", headers=headers)
        assert response.json() == []
        assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/openapi.json").status_code == 401
    assert (
        client.get(
            "/api/openapi.json",
            headers={"Authorization": "Bearer invalid", "User-Agent": "Example native macOS 99"},
        ).status_code
        == 401
    )
    assert client.get("/api/recent-clients", headers=headers).json() == []
    assert client.get("/api/openapi.json", headers={"Authorization": f"Bearer {device_token}"}).status_code == 200
    assert client.get("/api/recent-clients", headers=headers).json() == [
        {"kind": "unknown", "platform": "unknown", "presence": "fresh"}
    ]
    other = TestClient(
        web.build(kernel, port=8000, token=token, static=None, devices=devices),
        base_url="http://127.0.0.1:8000",
    )
    assert other.get("/api/recent-clients", headers=headers).json() == []
    assert other.get("/api/openapi.json", headers=headers).status_code == 200
    assert other.get("/api/recent-clients", headers=headers).json()[0]["presence"] == "fresh"
