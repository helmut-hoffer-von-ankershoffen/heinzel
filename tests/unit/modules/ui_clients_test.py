"""Additive status data remains optional across server generations."""

import json
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import Mock

import pytest

from heinzel.modules.system.lifecycle._service import WebStatus
from heinzel.modules.ui import _clients

pytestmark = pytest.mark.unit


@pytest.fixture
def local_endpoint():
    calls = []
    reply = {"code": 200, "body": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.path, dict(self.headers)))
            body = json.dumps(reply["body"]).encode()
            self.send_response(reply["code"])
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Location", "/must-not-follow")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        service = Mock()
        service.status.return_value = WebStatus(installed=True, loaded=True, ready=True, port=server.server_port)
        service.open_local.return_value = f"http://127.0.0.1:{server.server_port}/ignored?ignored#token=example"
        try:
            yield service, reply, calls
        finally:
            server.shutdown()
            thread.join()


@pytest.mark.parametrize(
    ("code", "body", "state"),
    [
        (200, [], "available"),
        (404, {}, "unsupported"),
        (405, {}, "unsupported"),
        (503, {}, "unavailable"),
        (302, {}, "unavailable"),
        (200, [{"labels": {"machine": "x" * 65}}], "unavailable"),
        (200, [{"labels": {"flavor": "browser"}}], "unavailable"),
        (200, [{"platform": "macOS"}], "unavailable"),
        (200, [{"age_seconds": 1.5}], "unavailable"),
        (200, [{"identity_provenance": "unrecognized"}], "unavailable"),
        (200, [{"last_seen": "2026-11-27T12:00:00"}], "unavailable"),
        (200, {}, "unavailable"),
        (
            200,
            [
                {
                    "labels": {"flavor": "native", "machine": "Example Mac", "app_version": "0.9.57"},
                    "presence": "stale",
                    "last_seen": "2026-11-27T12:00:00Z",
                }
            ],
            "available",
        ),
    ],
)
def test_authenticated_status(monkeypatch, local_endpoint, code, body, state):
    service, reply, calls = local_endpoint
    reply.update(code=code, body=body)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    result = _clients.read_status(service)
    assert result.ready
    assert result.client_observations_status == state
    assert result.installed
    assert result.loaded
    assert result.port == service.status.return_value.port
    if state == "unavailable":
        assert result.client_observations == []
        assert "Desktop observations: unavailable" in _clients.render_status(result).plain
    assert calls == [
        (
            "/api/client-observations",
            {
                "Host": f"127.0.0.1:{result.port}",
                "Accept-Encoding": "identity",
                "Authorization": "Bearer example",
                "Cache-Control": "no-store",
            },
        )
    ]
    service.open_local.assert_called_once_with()
    if body == [] and state == "available":
        assert "No retained desktop observations" in _clients.render_status(result).plain
    assert "example" not in result.model_dump_json()
    text = _clients.render_status(result).plain
    assert "not open/live connections" in text
    if body and isinstance(body, list) and state == "available":
        assert result.model_dump(mode="json")["client_observations"][0]["labels"]["machine"] == "Example Mac"
        assert "stale" in text
        assert "identity: unknown" in text
        assert result.client_observations[0].identity_provenance == "unknown"


@pytest.mark.parametrize("count", [50, 51])
def test_response_row_bound_preserves_local_status(local_endpoint, count):
    service, reply, _ = local_endpoint
    reply["body"] = [{} for _ in range(count)]
    result = _clients.read_status(service)
    assert (
        result.model_dump(exclude={"client_observations", "client_observations_status"})
        == service.status.return_value.model_dump()
    )
    assert result.client_observations_status == ("available" if count == 50 else "unavailable")
    assert len(result.client_observations) == (50 if count == 50 else 0)
    text = _clients.render_status(result).plain
    assert "ready: True" in text
    if count == 51:
        assert "Desktop observations: unavailable" in text
        assert "No retained desktop observations" not in text


def test_stopped_server_does_not_retrieve_session():
    service = Mock()
    service.status.return_value = WebStatus(installed=False, loaded=False, ready=False)
    assert _clients.read_status(service).client_observations_status == "unavailable"
    service.open_local.assert_not_called()


def test_connection_error_preserves_service_status(monkeypatch):
    service = Mock()
    service.status.return_value = WebStatus(installed=True, loaded=True, ready=True, port=8737)
    service.open_local.side_effect = OSError("private details")
    result = _clients.read_status(service)
    assert result.ready
    assert result.client_observations_status == "unavailable"
    assert "private details" not in result.model_dump_json()


@pytest.mark.parametrize(
    "link",
    [
        "https://127.0.0.1:8737/#token=example",
        "http://127.0.0.1:8738/#token=example",
        "http://localhost:8737/#token=example",
        "http://user@127.0.0.1:8737/#token=example",
        "http://127.0.0.1:8737.example.invalid/#token=example",
        "http://127.0.0.1:8737/",
    ],
)
def test_status_refuses_other_origins_and_missing_credentials(monkeypatch, link):
    service = Mock()
    service.status.return_value = WebStatus(installed=True, loaded=True, ready=True, port=8737)
    service.open_local.return_value = link
    client = Mock()
    monkeypatch.setattr(_clients, "HTTPConnection", client)
    assert _clients.read_status(service).client_observations_status == "unavailable"
    client.assert_not_called()


@pytest.mark.parametrize("error", [OSError("private details"), HTTPException("private details")])
def test_transport_failure_closes_connection_and_preserves_status(monkeypatch, error):
    service = Mock()
    service.status.return_value = WebStatus(installed=True, loaded=True, ready=True, port=8737)
    service.open_local.return_value = "http://127.0.0.1:8737/#token=example"
    client = Mock()
    client.getresponse.side_effect = error
    factory = Mock(return_value=client)
    monkeypatch.setattr(_clients, "HTTPConnection", factory)
    result = _clients.read_status(service)
    factory.assert_called_once()
    assert factory.call_args.args == ("127.0.0.1", 8737)
    assert factory.call_args.kwargs == {"timeout": 2}
    client.close.assert_called_once_with()
    assert result.client_observations_status == "unavailable"
    assert result.model_dump(exclude={"client_observations", "client_observations_status"}) == (
        service.status.return_value.model_dump()
    )
    assert "private details" not in result.model_dump_json()


@pytest.mark.parametrize("provenance", ["device-credential", "session-observation"])
def test_explicit_identity_is_preserved_in_status_text_and_json(provenance):
    row = _clients.ClientObservation.model_validate({"identity_provenance": provenance})
    result = _clients.UiStatus(
        client_observations=[row], client_observations_status="available", installed=True, loaded=True, ready=True
    )
    assert f"identity: {provenance}" in _clients.render_status(result).plain
    assert result.model_dump(mode="json")["client_observations"][0]["identity_provenance"] == provenance
