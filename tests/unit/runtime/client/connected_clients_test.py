"""The workspace records which desktop app connected and shows unknown for clients that send nothing."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from heinzel.heads.web._workspace_version import add_connected_clients
from heinzel.modules.system.client import ConnectedClients, client_header, parse_client

pytestmark = pytest.mark.unit
NOW = datetime(2026, 11, 22, 12, tzinfo=UTC)


def test_header_round_trips_with_a_machine_name_that_needs_quoting():
    value = client_header("electron", "0.9.54", "0.9.54", "Helmut's MacBook Air")
    client = parse_client({"x-heinzel-client": value}, NOW)
    assert (client.flavor, client.app_version, client.backend_version) == ("electron", "0.9.54", "0.9.54")
    assert client.machine == "Helmut's MacBook Air"


def test_user_agent_token_serves_the_native_app():
    agent = f"Mozilla/5.0 AppleWebKit HeinzelClient({client_header('native', '1.4', None, 'Air')})"
    client = parse_client({"user-agent": agent}, NOW)
    assert (client.flavor, client.app_version, client.backend_version, client.machine) == (
        "native",
        "1.4",
        "unknown",
        "Air",
    )


@pytest.mark.parametrize(
    "headers", [{}, {"x-heinzel-client": "flavor=rocket;app=" + "x" * 100}, {"user-agent": "curl"}]
)
def test_clients_without_usable_headers_are_unknown(headers):
    client = parse_client(headers, NOW)
    assert (client.flavor, client.app_version, client.backend_version, client.machine) == ("unknown",) * 4


def test_record_keeps_one_entry_per_machine_newest_first_and_throttles_touches(tmp_path: Path):
    clients = ConnectedClients(tmp_path)
    air = parse_client({"x-heinzel-client": client_header("native", "1", "2", "Air")}, NOW)
    clients.touch(air)
    clients.touch(air.model_copy(update={"last_seen": NOW + timedelta(seconds=5), "app_version": "9"}))
    assert [c.app_version for c in clients.recorded()] == ["1"]  # throttled
    clients.touch(air.model_copy(update={"last_seen": NOW + timedelta(minutes=2), "app_version": "3"}))
    other = parse_client({}, NOW + timedelta(minutes=3))
    clients.touch(other)
    assert [(c.machine, c.app_version) for c in clients.recorded()] == [("unknown", "unknown"), ("Air", "3")]


def test_unreadable_file_is_empty_and_unwritable_directory_does_not_raise(tmp_path: Path):
    (tmp_path / "clients.json").write_text("{not json", encoding="utf-8")
    clients = ConnectedClients(tmp_path)
    assert clients.recorded() == []
    blocker = tmp_path / "file"
    blocker.write_text("", encoding="utf-8")
    ConnectedClients(blocker / "sub").record(parse_client({}, NOW))


def test_route_lists_the_recorded_clients(tmp_path: Path):
    clients = ConnectedClients(tmp_path)
    clients.record(parse_client({"x-heinzel-client": client_header("electron", "1", "2", "Air")}, NOW))
    app = FastAPI()
    add_connected_clients(app, clients)
    body = TestClient(app).get("/connected-clients").json()
    assert body[0]["flavor"] == "electron"
    assert body[0]["machine"] == "Air"


def test_web_records_the_authenticated_client_and_lists_it(make_kernel):
    from heinzel.heads import web

    token = "t" * 43
    kernel = make_kernel(builtins=True)
    client = TestClient(web.build(kernel, port=8000, token=token, static=None), base_url="http://127.0.0.1:8000")
    auth = {"Authorization": f"Bearer {token}"}
    client.get("/api/connected-clients", headers={"x-heinzel-client": client_header("native", "1", "2", "Air")})
    assert client.get("/api/connected-clients").status_code == 401  # refused calls record nothing
    client.get(
        "/api/workspace-version", headers=auth | {"x-heinzel-client": client_header("electron", "3", "4", "Air")}
    )
    listed = client.get("/api/connected-clients", headers=auth).json()
    assert [(c["flavor"], c["app_version"], c["backend_version"], c["machine"]) for c in listed] == [
        ("electron", "3", "4", "Air")
    ]
