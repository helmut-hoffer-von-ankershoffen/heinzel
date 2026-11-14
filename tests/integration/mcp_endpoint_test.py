"""MCP at `/mcp` on the web server (ADR 0162): shut until `[hz] mcp_http` is on, bearer tokens only, tokens in the app.

The configuration is a real one in a temporary directory, so the switch is the setting the CLI and the page write.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from mcp_types.version import LATEST_PROTOCOL_VERSION
from typer.testing import CliRunner

from heinzel.heads import web
from heinzel.heads._exposure import MCP_HTTP, exposed
from heinzel.heads._mcp_tokens import McpTokens
from heinzel.heads.cli import build
from heinzel.heads.web._devices import Devices
from heinzel.kernel import Config

pytestmark = pytest.mark.integration

SESSION = "s3ssion-token-for-tests-that-is-long-enough"
PORT = 8737
BASE = f"http://127.0.0.1:{PORT}"
APP = {"Authorization": f"Bearer {SESSION}"}
HELLO = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": LATEST_PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "1"},
    },
}
ACCEPT = {"Accept": "application/json, text/event-stream"}


@pytest.fixture
def kernel(make_kernel, tmp_path, monkeypatch):
    monkeypatch.setenv("HZ_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("HZ_MCP_HTTP", raising=False)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.toml").write_text("[hz]\nui_port = 8737\n")
    return make_kernel(builtins=True, config=Config.load(tmp_path / "project"))


@pytest.fixture
def now() -> list[datetime]:
    return [datetime.now(UTC)]


@pytest.fixture
def tokens(tmp_path, now) -> McpTokens:
    return McpTokens(tmp_path / "mcp", clock=lambda: now[0])


@pytest.fixture
def devices(tmp_path) -> Devices:
    return Devices(tmp_path / "web")


@pytest.fixture
def client(kernel, tokens, devices):
    app = web.build(kernel, port=PORT, token=SESSION, static=None, mcp_tokens=tokens, devices=devices)
    with TestClient(app, base_url=BASE) as client:
        yield client


def switch(client, *, on: bool) -> dict:
    answer = client.put("/api/_web/mcp", json={"enabled": on}, headers=APP)
    assert answer.status_code == 200, answer.text
    return answer.json()


def initialize(client, token: str | None):
    headers = {**ACCEPT, **({} if token is None else {"Authorization": f"Bearer {token}"})}
    return client.post("/mcp", json=HELLO, headers=headers)


def test_mcp_is_not_found_while_the_switch_is_off(client, tokens):
    _, token = tokens.create("codex", "write")
    assert initialize(client, token).status_code == 404
    assert client.get("/mcp").status_code == 404
    assert client.get("/mcp/anything").status_code == 404
    assert client.get("/api/_web/mcp", headers=APP).json()["enabled"] is False


def test_switched_on_it_needs_an_mcp_token_and_nothing_else_opens_it(client, tokens):
    assert switch(client, on=True)["enabled"] is True
    _, token = tokens.create("codex", "write")
    assert initialize(client, None).status_code == 401
    assert initialize(client, SESSION).status_code == 401
    assert initialize(client, "hzmcp_not-a-token").status_code == 401
    assert initialize(client, token).status_code == 200


def test_an_mcp_token_does_not_open_the_api(client, tokens):
    _, token = tokens.create("codex", "write")
    assert client.get("/api/_web/mcp", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_switching_off_closes_it_again(client, tokens):
    switch(client, on=True)
    _, token = tokens.create("codex", "read")
    assert initialize(client, token).status_code == 200
    switch(client, on=False)
    assert initialize(client, token).status_code == 404


def test_the_app_makes_lists_and_revokes_tokens_and_shows_one_once(client, tokens):
    made = client.post("/api/_web/mcp/tokens", json={"actor": "claude-code", "scope": "write"}, headers=APP)
    assert made.status_code == 200
    body = made.json()
    assert body["token"].startswith("hzmcp_")
    listed = client.get("/api/_web/mcp", headers=APP).json()["tokens"]
    assert [(t["id"], t["actor"], t["scope"]) for t in listed] == [(body["id"], "claude-code", "write")]
    assert "token" not in listed[0]
    assert "sha256" not in listed[0]
    assert tokens.check(body["token"]) is not None
    assert client.delete(f"/api/_web/mcp/tokens/{body['id']}", headers=APP).status_code == 200
    assert tokens.check(body["token"]) is None
    assert client.delete(f"/api/_web/mcp/tokens/{body['id']}", headers=APP).status_code == 404


def test_the_token_routes_need_the_apps_own_token(client):
    assert client.get("/api/_web/mcp").status_code == 401
    assert client.post("/api/_web/mcp/tokens", json={"actor": "x"}).status_code == 401
    assert client.put("/api/_web/mcp", json={"enabled": True}).status_code == 401


def test_no_mcp_head_offers_a_token_operation(kernel):
    names = {item.name for item in exposed(kernel, MCP_HTTP)}
    assert not {name for name in names if "token" in name and "mcp" in name}


def test_the_cli_switch_is_the_same_setting_the_page_shows(kernel, client):
    runner = CliRunner()
    started = runner.invoke(build(kernel), ["system", "serve", "mcp-start", "--json"])
    assert started.exit_code == 0, started.output
    assert json.loads(started.output)["enabled"] is True
    assert client.get("/api/_web/mcp", headers=APP).json()["enabled"] is True
    status = runner.invoke(build(kernel), ["system", "serve", "mcp-status"])
    assert "on" in status.output
    stopped = runner.invoke(build(kernel), ["system", "serve", "mcp-stop", "--json"])
    assert json.loads(stopped.output)["enabled"] is False
    assert client.get("/api/_web/mcp", headers=APP).json()["enabled"] is False


def sse(answer) -> dict:
    """The JSON-RPC message of a streamable-HTTP answer, plain or as one event."""
    text = answer.text
    data = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
    return json.loads(data[-1] if data else text)


def session(client, token: str) -> dict[str, str]:
    """Initialize and return the headers of the open MCP session."""
    answer = initialize(client, token)
    assert answer.status_code == 200
    headers = {
        **ACCEPT,
        "Authorization": f"Bearer {token}",
        "Mcp-Session-Id": answer.headers["mcp-session-id"],
        "MCP-Protocol-Version": sse(answer)["result"]["protocolVersion"],
    }
    client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers)
    return headers


def call(client, headers: dict[str, str], tool: str, arguments: dict) -> dict:
    body = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
    return sse(client.post("/mcp", json=body, headers=headers))["result"]


def test_a_read_token_is_refused_a_tool_that_changes_things(client, tokens):
    switch(client, on=True)
    _, token = tokens.create("codex", "read")
    headers = session(client, token)
    result = call(client, headers, "system_config_set", {"key": "hz.mcp_http", "value": "false"})
    assert result["isError"] is True
    assert "may only read" in json.dumps(result)
    assert client.get("/api/_web/mcp", headers=APP).json()["enabled"] is True


def test_expired_revoked_and_device_tokens_are_refused(client, tokens, devices, now):
    switch(client, on=True)
    record, revoked = tokens.create("codex", "write")
    tokens.revoke(record.id)
    _, shortlived = tokens.create("codex", "write", timedelta(days=1))
    _, paired = devices.pair("a browser")
    assert initialize(client, shortlived).status_code == 200
    now[0] += timedelta(days=2)
    assert initialize(client, shortlived).status_code == 401
    assert initialize(client, revoked).status_code == 401
    assert initialize(client, paired).status_code == 401


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_a_foreign_host_origin_or_site_is_refused_even_with_a_valid_token(client, tokens, method):
    switch(client, on=True)
    _, token = tokens.create("codex", "write")
    good = {**ACCEPT, "Authorization": f"Bearer {token}"}
    for extra in (
        {"Host": "evil.example"},
        {"Origin": "https://evil.example"},
        {"Sec-Fetch-Site": "cross-site"},
    ):
        answer = client.request(method, "/mcp", json=HELLO if method == "POST" else None, headers={**good, **extra})
        assert answer.status_code == 403, (method, extra, answer.status_code)


def test_an_unauthenticated_request_below_mcp_never_ends_an_open_session(client, tokens):
    switch(client, on=True)
    _, token = tokens.create("codex", "write")
    headers = session(client, token)
    assert client.get("/mcp/x").status_code == 404
    assert client.post("/mcp/x", json={}).status_code == 404
    result = call(client, headers, "system_info", {})
    assert not result.get("isError")
