"""The ordinary HTTP check proves MCP initialization without claiming process ownership."""

import json
import logging
import socket
import threading
import time

import pytest
import uvicorn
from typer.testing import CliRunner

from heinzel import HeinzelError
from heinzel.heads import mcp
from heinzel.heads._mcp_tokens import McpTokens
from heinzel.heads.cli import build
from heinzel.heads.mcp._http_check import check_http
from heinzel.kernel._logging import record_fields

pytestmark = pytest.mark.unit


@pytest.fixture
def endpoint(make_kernel, tmp_path):
    kernel = make_kernel(builtins=True)
    tokens = McpTokens(tmp_path / "tokens")
    _, token = tokens.create("fictional-check", "read")
    head, security = mcp.http_server(kernel, "127.0.0.1", tokens=tokens)
    app = head.streamable_http_app(transport_security=security)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    runner = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
    thread = threading.Thread(target=runner.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not runner.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runner.started
    try:
        yield kernel, f"http://127.0.0.1:{port}/mcp", token
    finally:
        runner.should_exit = True
        thread.join(5)
        listener.close()
        assert not thread.is_alive()


def test_check_initializes_and_lists_actual_authenticated_server(endpoint, caplog):
    _, url, token = endpoint
    caplog.set_level(logging.INFO, logger="heinzel.mcp.http.check")
    assert check_http(url, token) > 0
    with pytest.raises(HeinzelError, match="could not be authenticated"):
        check_http(url, "hzmcp_wrong")
    events = [record_fields(record) for record in caplog.records if record.getMessage() == "mcp.http.check"]
    assert [(event["phase"], event.get("outcome")) for event in events] == [
        ("start", None),
        ("end", "ok"),
        ("start", None),
        ("end", "error"),
    ]
    assert token not in str(events)
    assert "hzmcp_wrong" not in str(events)


@pytest.mark.parametrize(
    "url",
    ["http://example.com:123/mcp", "http://127.0.0.1:123/mcp?token=x", "http://user@127.0.0.1:123/mcp"],
)
def test_check_refuses_nonlocal_or_ambiguous_urls(url):
    with pytest.raises(HeinzelError, match="loopback"):
        check_http(url, "hzmcp_fictional")


def test_cli_reads_token_only_from_stdin_and_reports_ownership_unverified(endpoint):
    kernel, url, token = endpoint
    runner = CliRunner()
    result = runner.invoke(build(kernel), ["system", "serve", "mcp-check", "--url", url, "--token-stdin"], input=token)
    assert result.exit_code == 0, result.output
    assert token not in result.output
    assert json.loads(result.stdout) == {
        "mcp_readiness": "initialized",
        "tools": check_http(url, token),
        "worker_ownership": "unverified",
    }
    refused = runner.invoke(build(kernel), ["system", "serve", "mcp-check", "--url", url], input=token)
    assert refused.exit_code != 0
    assert token not in refused.output
    bad = runner.invoke(
        build(kernel), ["system", "serve", "mcp-check", "--url", url, "--token-stdin"], input="hzmcp_wrong"
    )
    assert bad.exit_code != 0
    assert "hzmcp_wrong" not in bad.output
