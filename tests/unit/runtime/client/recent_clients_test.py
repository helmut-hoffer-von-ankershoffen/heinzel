"""Authenticated use is coarse evidence, independent of paired credentials."""

from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from heinzel.heads import web
from heinzel.kernel.recent_clients import (
    MAX_HEADER,
    MAX_OBSERVATIONS,
    ClientLabels,
    RecentClient,
    RecentUse,
    parse_client_labels,
    recent_clients,
)
from heinzel.modules.system.client import Devices
from heinzel.modules.system.client import RecentClient as ClientRecentClient
from heinzel.modules.system.client import RecentUse as ClientRecentUse
from heinzel.modules.system.client import recent_clients as client_recent_clients

pytestmark = pytest.mark.unit


def test_labels_are_bounded_untrusted_and_do_not_reflect_credentials():
    labels = parse_client_labels({"x-heinzel-client": "flavor=native;machine=Example%20Mac;app=1.2;backend=0.57"})
    assert labels == ClientLabels(flavor="native", machine="Example Mac", app_version="1.2", backend_version="0.57")
    for text in ("", "x" * (MAX_HEADER + 1), "flavor=browser", "flavor=native;flavor=electron"):
        assert parse_client_labels({"x-heinzel-client": text}) is None
    assert parse_client_labels({"user-agent": "HeinzelClient(flavor=native)"}) is None
    for value in ("hzd_" + "x" * 40, "Bearer secret", "%0Ahello", "<script>", "x" * 65):
        result = parse_client_labels({"x-heinzel-client": f"flavor=native;machine={value};app={value}"})
        assert result is not None
        assert result.machine == result.app_version == "unknown"


def test_observation_identity_collisions_bounds_and_expiry():
    now = datetime(2026, 11, 27, tzinfo=UTC)
    evidence = RecentUse()
    labels = ClientLabels(flavor="native", machine="Example Mac", app_version="1")
    evidence.observe_labels(now, labels, "device-one")
    evidence.observe_labels(now, labels, "device-two")
    evidence.observe_labels(now, labels)
    evidence.observe_labels(now, labels.model_copy(update={"app_version": "2"}))
    rows = evidence.observations(now + timedelta(seconds=3))
    assert len(rows) == 3
    assert {row.device_id for row in rows} == {None, "device-one", "device-two"}
    assert all(row.age_seconds == 3 and row.platform == "unknown" for row in rows)
    assert rows[-1].identity_provenance == "session-observation"
    assert rows[-1].labels.app_version == "2"
    evidence.observe_labels(now, labels.model_copy(update={"machine": "Renamed"}), "device-one")
    assert len(evidence.observations(now)) == 3
    assert all(row.presence == "stale" for row in evidence.observations(now + timedelta(hours=3)))
    assert evidence.observations(now + timedelta(days=30)) == []
    assert evidence.observations(now) == []  # expired entries were deleted
    for index in range(MAX_OBSERVATIONS + 1):
        evidence.observe_labels(now, labels, str(index))
    assert len(evidence.observations(now)) == MAX_OBSERVATIONS
    assert evidence.observations(now - timedelta(seconds=1)) == []


def test_labelled_route_uses_authenticated_identity_and_excludes_polling(make_kernel, tmp_path):
    devices = Devices(tmp_path / "web")
    device, credential = devices.pair("Example workstation")
    token = "t" * 43
    client = TestClient(
        web.build(make_kernel(builtins=True), port=8000, token=token, static=None, devices=devices),
        base_url="http://127.0.0.1:8000",
    )
    session = {"Authorization": f"Bearer {token}"}
    claimed = {"x-heinzel-client": "flavor=electron;machine=Example;app=2.0"}
    assert client.get("/api/openapi.json", headers=claimed).status_code == 401
    assert client.get("/api/client-observations", headers=session).json() == []
    headers = {**claimed, "Authorization": f"Bearer {credential}"}
    assert client.get("/api/openapi.json", headers={**headers, "Origin": "https://example.invalid"}).status_code == 403
    assert client.get("/api/client-observations", headers=session).json() == []
    assert client.get("/api/openapi.json", headers=headers).status_code == 200
    result = client.get("/api/client-observations", headers=session)
    assert result.headers["cache-control"] == "no-store"
    row = result.json()[0]
    assert row["device_id"] == device.id
    assert row["identity_provenance"] == "device-credential"
    assert row["label_provenance"] == "self-reported"
    assert row["labels"]["app_version"] == "2.0"
    assert credential not in result.text
    assert device.sha256 not in result.text
    for path in ("/api/client-observations", "/api/recent-clients"):
        assert client.get(path, headers={**session, **claimed}).status_code == 200
    assert client.get("/api/client-observations", headers=session).json()[0]["last_seen"] == row["last_seen"]
    assert len(client.get("/api/client-observations", headers=session).json()) == 1


def test_public_model_preserves_pydantic_serialization_and_client_exports():
    assert ClientRecentClient is RecentClient
    assert ClientRecentUse is RecentUse
    assert client_recent_clients is recent_clients
    observation = RecentClient(presence="fresh")
    assert observation.model_dump() == {"kind": "unknown", "platform": "unknown", "presence": "fresh"}
    assert observation.model_dump_json() == '{"kind":"unknown","platform":"unknown","presence":"fresh"}'


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


@pytest.mark.timeout(12)  # Builds an OpenAPI schema for each of two independent app instances.
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


@pytest.mark.parametrize(
    ("wire", "machine"),
    [
        ("flavor=native;app=0.9.54;backend=unknown;machine=Demo%27s%20Air%20%282%29", "Demo's Air (2)"),
        ("flavor=electron;app=0.9.54;backend=0.9.54;machine=Demo's%20Air", "Demo's Air"),
    ],
)
def test_actual_reporter_wire_preserves_ordinary_labels(wire, machine):
    labels = parse_client_labels({"x-heinzel-client": wire})
    assert labels is not None
    assert labels.machine == machine
    assert labels.app_version == "0.9.54"
    assert labels.backend_version == ("unknown" if labels.flavor == "native" else "0.9.54")
    evidence = RecentUse()
    now = datetime(2026, 11, 27, tzinfo=UTC)
    evidence.observe_labels(now, labels)
    assert evidence.observations(now)[0].labels == labels


@pytest.mark.parametrize("machine", ["Demo\u2019s Air", "é " * 32, "𐐀 " * 32])
def test_encoded_unicode_labels_fit_the_bounded_wire(machine):
    wire = "flavor=native;app=1;backend=1;machine=" + quote(machine, safe="")
    labels = parse_client_labels({"x-heinzel-client": wire})
    assert labels is not None
    assert labels.machine == machine


def test_invalid_duplicate_fields_cannot_be_replaced():
    assert parse_client_labels({"x-heinzel-client": "flavor=native;machine=%0A;machine=Demo"}) is None
