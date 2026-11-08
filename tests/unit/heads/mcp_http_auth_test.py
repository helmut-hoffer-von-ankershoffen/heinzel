"""MCP over streamable HTTP (ADR 0142): a bearer token per agent, read and write scopes, an audited actor per call."""

import logging
import stat
from datetime import UTC, datetime, timedelta

import anyio
import httpx2
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types.version import LATEST_PROTOCOL_VERSION

from heinzel import HeinzelError, Module
from heinzel.heads import mcp
from heinzel.heads._exposure import MCP_HTTP, exposed
from heinzel.heads._mcp_tokens import McpTokens, require_reachable_only_as_asked
from heinzel.kernel._logging import record_fields

pytestmark = pytest.mark.unit

BASE = "http://127.0.0.1:8765"


def notes(ran: list[str]) -> Module:
    module = Module("notes", "Test-only operations.")

    @module.operation
    def show() -> str:
        """Show the notes."""
        ran.append("show")
        return "the notes"

    @module.operation(read_only=False)
    def publish(title: str) -> str:
        """Publish the notes."""
        ran.append("publish")
        return f"published {title}"

    return module


@pytest.fixture
def ran() -> list[str]:
    return []


@pytest.fixture
def kernel(make_kernel, ran):
    return make_kernel(builtins=True, notes=notes(ran))


@pytest.fixture
def tokens(tmp_path) -> McpTokens:
    return McpTokens(tmp_path / "mcp")


class Calls(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def audit():
    handler = Calls()
    logger = logging.getLogger("heinzel.mcp.http")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    yield handler
    logger.removeHandler(handler)


def call(kernel, tokens, token: str | None, tool: str, arguments: dict | None = None) -> tuple[bool, str]:
    """Call one tool through the real streamable-HTTP app in process: (is_error, text)."""
    server, security = mcp.http_server(kernel, "127.0.0.1", tokens=tokens)
    app = server.streamable_http_app(transport_security=security)
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}

    async def run() -> tuple[bool, str]:
        async with app.router.lifespan_context(app):
            client = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=BASE, headers=headers)
            async with (
                streamable_http_client(f"{BASE}/mcp", http_client=client) as (read, write, *_),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                result = await session.call_tool(tool, arguments or {})
                return result.is_error, " ".join(getattr(part, "text", "") for part in result.content)

    return anyio.run(run)


async def _status(kernel, tokens, headers: dict[str, str], **serving) -> int:
    server, security = mcp.http_server(kernel, serving.pop("host", "127.0.0.1"), tokens=tokens, **serving)
    app = server.streamable_http_app(transport_security=security)
    async with app.router.lifespan_context(app):
        client = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=BASE)
        hello = {
            "protocolVersion": LATEST_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "1"},
        }
        body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": hello}
        response = await client.post(
            "/mcp", json=body, headers={"Accept": "application/json, text/event-stream", **headers}
        )
        return response.status_code


def status(kernel, tokens, headers: dict[str, str], **serving) -> int:
    return anyio.run(lambda: _status(kernel, tokens, headers, **serving))


def test_a_request_without_a_token_or_with_a_wrong_one_is_refused_with_401(kernel, tokens):
    tokens.create("codex", "write")
    assert status(kernel, tokens, {}) == 401
    assert status(kernel, tokens, {"Authorization": "Bearer hzmcp_not-a-token"}) == 401
    assert status(kernel, tokens, {"Authorization": "Basic Zm9vOmJhcg=="}) == 401


def test_a_read_token_runs_a_read_only_tool(kernel, tokens, ran):
    _, token = tokens.create("codex", "read")
    assert call(kernel, tokens, token, "notes_show") == (False, "the notes")
    assert ran == ["show"]


def test_a_read_token_is_refused_a_tool_that_changes_things_and_it_does_not_run(kernel, tokens, ran):
    _, token = tokens.create("codex", "read")
    is_error, text = call(kernel, tokens, token, "notes_publish", {"title": "w43"})
    assert is_error
    assert "may only read" in text
    assert ran == []


def test_a_write_token_runs_a_tool_that_changes_things(kernel, tokens, ran):
    _, token = tokens.create("claude", "write")
    assert call(kernel, tokens, token, "notes_publish", {"title": "w43"}) == (False, "published w43")
    assert ran == ["publish"]


def test_a_revoked_or_expired_token_is_refused(kernel, tokens):
    record, token = tokens.create("codex", "write")
    tokens.revoke(record.id)
    assert tokens.check(token) is None
    later = McpTokens(tokens.directory, clock=lambda: datetime.now(UTC) + timedelta(days=2))
    _, short = tokens.create("codex", "write", timedelta(days=1))
    assert tokens.check(short) is not None
    assert later.check(short) is None


def test_each_call_logs_its_actor_and_outcome_but_never_the_token(kernel, tokens, audit):
    record, token = tokens.create("codex", "read")
    call(kernel, tokens, token, "notes_show")
    call(kernel, tokens, token, "notes_publish", {"title": "w43"})
    events = [record_fields(r) for r in audit.records if r.getMessage() == "mcp.http.call"]
    ends = [e for e in events if e["phase"] == "end"]
    expected = {"actor": "codex", "key_id": record.id, "scope": "read"}
    assert [(e["tool"], e["outcome"], e["read_only"]) for e in ends] == [
        ("notes_show", "ok", True),
        ("notes_publish", "error", False),
    ]
    assert all(expected.items() <= e.items() for e in events)
    assert ends[1]["error_type"] == "ToolError"
    logged = repr([r.__dict__ for r in audit.records])
    assert token not in logged
    assert record.sha256 not in logged


def test_only_the_digest_is_stored_in_an_owner_only_file(tokens):
    record, token = tokens.create("codex", "read")
    text = tokens.path.read_text(encoding="utf-8")
    assert token not in text
    assert record.sha256 in text
    assert stat.S_IMODE(tokens.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(tokens.directory.stat().st_mode) == 0o700


def test_an_actor_name_must_be_plain(tokens):
    with pytest.raises(HeinzelError, match="actor"):
        tokens.create("Codex Desktop\n", "read")


def test_serving_without_any_token_is_refused(kernel, tokens):
    with pytest.raises(HeinzelError, match="needs a token"):
        mcp.http_server(kernel, "127.0.0.1", tokens=tokens)


def test_a_non_loopback_interface_needs_an_explicit_expose(kernel, tokens):
    tokens.create("codex", "read")
    with pytest.raises(HeinzelError, match="--expose"):
        mcp.http_server(kernel, "0.0.0.0", tokens=tokens)  # noqa: S104 - the refused case
    require_reachable_only_as_asked("0.0.0.0", expose=True, allowed_hosts=("mini:*",))  # noqa: S104 - asked for
    require_reachable_only_as_asked("::1", expose=False)


def test_a_tailscale_serve_name_is_accepted_only_when_allowed(kernel, tokens):
    tokens.create("codex", "read")
    _, security = mcp.http_server(kernel, "127.0.0.1", tokens=tokens, allowed_hosts=("mini.tail1.ts.net",))
    assert security.enable_dns_rebinding_protection
    assert security.allowed_hosts == ["127.0.0.1:*", "localhost:*", "[::1]:*", "mini.tail1.ts.net"]
    assert security.allowed_origins == ["https://mini.tail1.ts.net"]


def test_a_browser_page_on_another_origin_is_refused_even_with_a_token(kernel, tokens):
    _, token = tokens.create("codex", "write")
    headers = {"Authorization": f"Bearer {token}", "Origin": "http://localhost:3000"}
    assert status(kernel, tokens, headers) == 403


def test_http_is_never_built_without_tokens(kernel):
    with pytest.raises(HeinzelError, match="tokens"):
        mcp.build(kernel, "streamable-http")


def test_an_http_tool_without_a_verified_token_does_not_run(kernel, ran):
    [item] = [item for item in exposed(kernel, MCP_HTTP) if item.name == "notes_show"]
    with pytest.raises(ToolError, match="bearer token"):
        mcp.tool(kernel, item, local=False)()
    assert ran == []


def test_expose_needs_the_names_other_devices_use(kernel, tokens):
    _, token = tokens.create("codex", "read")
    with pytest.raises(HeinzelError, match="--allowed-host"):
        mcp.http_server(kernel, "0.0.0.0", tokens=tokens, expose=True)  # noqa: S104 - the refused case
    lan = {"Authorization": f"Bearer {token}", "Host": "192.168.1.5:8765"}
    assert status(kernel, tokens, lan, host="0.0.0.0", expose=True, allowed_hosts=("192.168.1.5:*",)) == 200  # noqa: S104
    assert status(kernel, tokens, lan, host="0.0.0.0", expose=True, allowed_hosts=("10.0.0.2:*",)) == 421  # noqa: S104


def test_a_refused_token_is_logged_with_its_reason_but_never_the_token(kernel, tokens, audit):
    record, token = tokens.create("codex", "read", timedelta(days=1))
    tokens.create("claude", "read")  # still valid later, so the server starts
    wrong = "hzmcp_" + "x" * 43
    assert status(kernel, tokens, {"Authorization": f"Bearer {wrong}"}) == 401
    later = McpTokens(tokens.directory, clock=lambda: datetime.now(UTC) + timedelta(days=2))
    assert status(kernel, later, {"Authorization": f"Bearer {token}"}) == 401
    events = [record_fields(r) for r in audit.records if r.getMessage() == "mcp.http.auth"]
    assert [(e["outcome"], e["reason"]) for e in events] == [("refused", "unknown"), ("refused", "expired")]
    logged = repr([r.__dict__ for r in audit.records])
    assert wrong not in logged
    assert token not in logged
    assert record.sha256 not in logged
