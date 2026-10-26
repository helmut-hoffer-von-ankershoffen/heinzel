"""Explicit HTTPS reverse-proxy origins share the web head's existing authentication and terminal guard."""

import json
import os

import pytest
from fastapi.testclient import TestClient
from httpx import Headers
from starlette.websockets import WebSocketDisconnect

from heinzel import Module, Settings
from heinzel.heads import api, web
from heinzel.heads.web._devices import Devices

pytestmark = pytest.mark.integration
TOKEN = "session-for-proxy-origin-tests"
ORIGIN = "https://workstation.tail-example.ts.net"
TERMINAL = ORIGIN.replace("https:", "wss:") + "/api/terminal"
AUTH = {"Authorization": f"Bearer {TOKEN}", "Origin": ORIGIN, "Sec-Fetch-Site": "same-origin"}


@pytest.fixture
def app(make_kernel, monkeypatch, tmp_path):
    monkeypatch.setenv("HZ_UI_ALLOWED_ORIGINS", ORIGIN + ",https://workstation.tail-example.ts.net:9443")
    module = Module("example", "Test-only private web operation.")

    @module.operation(private=True, read_only=False)
    def write(value: str = "changed") -> str:
        """Return the requested private value."""
        return value

    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html><title>Fixture app</title>")
    return web.build(
        make_kernel(builtins=True, example=module, settings_=Settings()),
        port=8737,
        token=TOKEN,
        static=static,
        devices=Devices(tmp_path / "devices"),
    )


@pytest.mark.parametrize("authority", ["workstation.tail-example.ts.net", "workstation.tail-example.ts.net:443"])
def test_external_https_authority_uses_its_own_port_and_keeps_bearer_auth(app, authority):
    client = TestClient(app, base_url=ORIGIN, headers={"Host": authority})
    page = client.get("/")
    assert page.status_code == 200
    assert "connect-src 'self'" in page.headers["content-security-policy"]
    assert client.post("/api/example/write", headers=AUTH, json={"value": "private"}).json() == "private"
    assert client.post("/api/example/write").status_code == 401
    assert client.post("/api/example/write", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_nondefault_external_port_is_exact(app):
    client = TestClient(app, base_url=ORIGIN + ":9443")
    assert client.get("/api/system/info", headers={**AUTH, "Origin": ORIGIN + ":9443"}).status_code == 200
    assert client.get("/api/system/info", headers=AUTH).status_code == 403


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "http://workstation.tail-example.ts.net"},
        {"Origin": ORIGIN + ":9443"},
        {"Origin": "null"},
        {"Origin": ORIGIN + "/"},
        {"Origin": "https://other.example.test"},
        {"Host": "other.example.test"},
        {"Host": "workstation.tail-example.ts.net:8737"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Sec-Fetch-Site": "same-origin, same-origin"},
        {
            "Host": "attacker.example.test",
            "X-Forwarded-Host": "workstation.tail-example.ts.net",
            "X-Forwarded-Proto": "https",
        },
    ],
)
def test_external_requests_reject_wrong_host_origin_and_cross_site_metadata(app, headers):
    client = TestClient(app, base_url=ORIGIN)
    assert client.get("/api/system/info", headers=AUTH | headers).status_code == 403


def test_pairing_and_revocation_work_from_the_explicit_origin(app):
    client = TestClient(app, base_url=ORIGIN)
    paired = client.post("/api/_web/device", headers=AUTH, json={"name": "Remote test browser"})
    assert paired.status_code == 200
    device = {**AUTH, "Authorization": f"Bearer {paired.json()['token']}"}
    assert client.get("/api/system/info", headers=device).status_code == 200
    assert client.post("/api/_web/device", headers=device, json={"name": "Forbidden child"}).status_code == 403
    assert client.delete("/api/_web/device", headers=device).json()["revoked"] == [paired.json()["id"]]
    assert client.get("/api/system/info", headers=device).status_code == 401


def test_notice_and_operation_streams_keep_the_same_origin_and_bearer_guard(app):
    client = TestClient(app, base_url=ORIGIN)
    notice = client.get("/api/system/notify/stream?once=true", headers=AUTH)
    assert notice.status_code == 200
    assert notice.headers["content-type"].startswith("text/event-stream")
    result = client.post("/api/stream/example/write", headers=AUTH, json={"value": "streamed"})
    assert result.status_code == 200
    assert '"data": "streamed"' in result.text
    for path in ("/api/system/notify/stream?once=true", "/api/stream/example/write"):
        method = client.get if "notify" in path else client.post
        assert method(path).status_code == 401
        assert method(path, headers=AUTH | {"Origin": "https://evil.example.test"}).status_code == 403


@pytest.mark.skipif(os.name != "posix", reason="the terminal needs a POSIX pseudo-terminal")
def test_external_terminal_runs_only_after_origin_and_first_frame_token(app, monkeypatch, tmp_path):
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.setenv("HOME", str(tmp_path))
    client = TestClient(app, base_url=ORIGIN)
    with client.websocket_connect(TERMINAL, headers={"Origin": ORIGIN}) as socket:
        socket.send_text(json.dumps({"token": TOKEN}))
        socket.send_bytes(b"printf 'proxy-%s\\n' 42; exit\n")
        output = b""
        while b"proxy-42" not in output:
            output += socket.receive_bytes()
        assert b"proxy-42" in output
    with client.websocket_connect(TERMINAL, headers={"Origin": ORIGIN}) as socket:
        socket.send_text(json.dumps({"token": "wrong"}))
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_bytes()
        assert closed.value.code == 1008


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "null"},
        {"Origin": "https://evil.example.test"},
        {"Origin": ORIGIN, "Host": "workstation.tail-example.ts.net:8737"},
        {"Origin": ORIGIN, "Sec-Fetch-Site": "cross-site"},
        {"Origin": ORIGIN, "Sec-Fetch-Site": "same-site"},
        {"Origin": ORIGIN, "Host": "evil.example.test", "X-Forwarded-Host": "workstation.tail-example.ts.net"},
    ],
)
def test_terminal_rejects_unapproved_origin_or_host_before_authentication(app, headers):
    client = TestClient(app, base_url=ORIGIN)
    with pytest.raises(WebSocketDisconnect) as refused, client.websocket_connect(TERMINAL, headers=Headers(headers)):
        pass
    assert refused.value.code == 1008


def test_external_origins_do_not_widen_the_public_rest_head(make_kernel, monkeypatch):
    monkeypatch.setenv("HZ_UI_ALLOWED_ORIGINS", ORIGIN)
    rest = TestClient(api.build(make_kernel(builtins=True), port=8737), base_url=ORIGIN)
    assert rest.get("/system/info").status_code == 403


@pytest.mark.parametrize("name", ["host", "origin", "sec-fetch-site"])
def test_duplicate_authority_headers_are_refused_for_http_and_websockets(app, name):
    headers = list(AUTH.items())
    values = {"host": "workstation.tail-example.ts.net", "origin": ORIGIN, "sec-fetch-site": "same-origin"}
    if name == "host":
        headers.extend([("host", values[name]), ("host", values[name])])
    else:
        headers.append((name, values[name]))
    client = TestClient(app, base_url=ORIGIN)
    assert client.get("/api/system/info", headers=headers).status_code == 403
    with pytest.raises(WebSocketDisconnect) as refused, client.websocket_connect(TERMINAL, headers=Headers(headers)):
        pass
    assert refused.value.code == 1008


def test_a_project_cannot_widen_the_web_origin_policy(tmp_path, monkeypatch):
    from heinzel import Config

    monkeypatch.chdir(tmp_path)
    (tmp_path / "hz.toml").write_text('[hz]\nui_allowed_origins = ["https://untrusted.example"]\n')
    assert Config.load(tmp_path).settings(Settings, "hz").ui_allowed_origins == frozenset()


def test_unconfigured_proxy_names_the_web_setting_instead_of_api_hosts(make_kernel):
    app = web.build(make_kernel(builtins=True), port=8737, token=TOKEN, static=None)
    response = TestClient(app, base_url=ORIGIN).get("/api/system/info", headers=AUTH)
    assert response.status_code == 403
    assert "ui_allowed_origins" in response.json()["error"]
    assert "REST head only" in response.json()["error"]
