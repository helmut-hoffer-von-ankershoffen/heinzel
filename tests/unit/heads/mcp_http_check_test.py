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


@pytest.mark.parametrize("mode", ["redirect", "invalid-json"])
def test_reflected_token_never_reaches_cli_or_sdk_logs(make_kernel, caplog, capfd, mode):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    secret = "hzmcp_fictional_reflection_test"
    requests: list[str] = []

    class Reflect(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(self.path)
            if mode == "redirect":
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/mcp?reflected={secret}")
                body = b""
            else:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                body = secret.encode()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):  # noqa: A002 - standard library callback signature
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Reflect)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    caplog.set_level(logging.DEBUG)
    try:
        url = f"http://127.0.0.1:{server.server_port}/mcp"
        result = CliRunner().invoke(
            build(make_kernel(builtins=True)),
            ["system", "serve", "mcp-check", "--url", url, "--token-stdin"],
            input=secret,
        )
        assert result.exit_code != 0
        assert requests
        assert secret not in result.output
        assert secret not in caplog.text
        captured = capfd.readouterr()
        assert secret not in captured.out + captured.err
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()
