"""Actual TLS handshakes protect each managed probe connection and its credentials."""

import hashlib
import json
import os
import signal
import ssl
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from heinzel.modules.system.mcp import _service
from tests.fixtures import mcp_managed
from tests.integration.mcp_shared_lifecycle_test import cli

managed = mcp_managed.managed
pytestmark = pytest.mark.integration
TOKEN = b"hzmcp_" + b"f" * 43 + b"\n"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        self.server.requests.append(
            (self.path, bool(self.headers.get("authorization") or self.headers.get("X-Heinzel-Stop")))
        )
        if self.server.redirect:
            self.send_response(307)
            self.send_header("Location", self.server.redirect)
            self.end_headers()
            return
        request = json.loads(self.rfile.read(int(self.headers["content-length"])))
        response = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": {
                    "protocolVersion": request.get("params", {}).get("protocolVersion"),
                    "capabilities": {},
                    "serverInfo": {"name": "Fictional listener", "version": "1"},
                },
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


class Listener(HTTPServer):
    def __init__(self, port, contexts, redirect):
        self.contexts = contexts
        self.redirect = redirect
        self.attempts = 0
        self.requests = []
        super().__init__(("127.0.0.1", port), Handler)

    def get_request(self):
        connection, address = super().get_request()
        index = self.attempts
        self.attempts += 1
        if not self.contexts:
            return connection, address
        try:
            context = self.contexts[min(index, len(self.contexts) - 1)]
            return context.wrap_socket(connection, server_side=True), address
        except OSError:
            connection.close()
            raise


@contextmanager
def listening(port=0, contexts=(), redirect=None):
    with Listener(port, contexts, redirect) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(timeout=2)
            assert not thread.is_alive()


def identity(directory):
    directory.mkdir(mode=0o700)
    pin, _ = _service.create_tls_identity(directory)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(directory / "tls-cert.pem", directory / "tls-key.pem")
    return pin, context


def probe(directory, port, pin, *, action="check", token=TOKEN):
    return subprocess.run(
        [sys.executable, "-I", "-m", "heinzel.heads.mcp._http_probe", f"https://127.0.0.1:{port}/mcp", pin, action],
        cwd=directory,
        env={"PATH": os.defpath},
        input=token,
        capture_output=True,
        timeout=10,
        check=False,
    )


def assert_sanitized_failure(result):
    assert result.returncode == 1
    assert result.stdout == result.stderr == b""


def test_mcp_read_grant_cannot_shut_down_worker(managed):
    installed, manager, _, kernel = managed
    selected = kernel()
    assert cli(selected, "start").exit_code == 0
    service = _service.Service(selected)
    generation = service.authority().existing()
    directory = generation.directory(installed.prefix)
    assert_sanitized_failure(
        probe(
            directory,
            generation.port,
            generation.cert_sha256,
            action="stop",
            token=(directory / "probe-token").read_bytes(),
        )
    )
    assert manager.process.poll() is None
    assert service.stop().manager_cleanup == "pending"
    assert manager.process.poll() == 0


@pytest.mark.parametrize("operation", ["check", "stop"])
def test_listener_replaced_after_observation_never_receives_credentials(managed, monkeypatch, tmp_path, operation):
    installed, manager, _, kernel = managed
    selected = kernel()
    assert cli(selected, "start").exit_code == 0
    authority = _service.Service(selected).authority()
    generation = authority.existing()
    record = generation.path(installed.prefix, ".json")
    before = record.read_bytes()
    _, foreign_context = identity(tmp_path / "other-generation")
    real_request = _service.Service._request
    observations = []

    def replace_after_verification(service, authority, generation, *, stop=False):
        assert stop == (operation == "stop")
        manager.process.terminate()
        assert manager.process.wait(timeout=5) in {0, -signal.SIGTERM}
        manager.changed_program = True  # same label and unchanged retained files, different manager registration
        with listening(generation.port, (foreign_context,) if stop else ()) as foreign:
            try:
                return real_request(service, authority, generation, stop=stop)
            finally:
                observations.append((foreign.attempts, tuple(foreign.requests), foreign.socket.fileno() >= 0))

    monkeypatch.setattr(_service.Service, "_request", replace_after_verification)
    result = cli(selected, operation)
    if operation == "stop":
        assert result.exit_code != 0
        assert "owned_server" not in result.output
    else:
        assert json.loads(result.output)["state"] == "unverified"
    assert observations == [(1, (), True)]
    assert record.read_bytes() == before
    assert authority.existing().phase == "prepared"
    assert not any(args[1] == "bootout" for args in manager.calls)
    assert "hzmcp_" not in result.output


def test_pinned_identity_is_rechecked_after_an_authenticated_connection(tmp_path):
    directory = tmp_path / "owned"
    pin, owned = identity(directory)
    _, replacement = identity(tmp_path / "replacement")
    with listening(contexts=(owned, replacement)) as server:
        assert_sanitized_failure(probe(directory, server.server_port, pin))
        assert server.attempts >= 2
        assert server.requests == [("/mcp", True)]


def test_managed_probe_never_follows_redirect_or_changes_trust(tmp_path):
    directory = tmp_path / "owned"
    pin, owned = identity(directory)
    _, foreign = identity(tmp_path / "foreign")
    with listening(contexts=(foreign,)) as target:
        with listening(contexts=(owned,), redirect=f"https://127.0.0.1:{target.server_port}/mcp") as origin:
            assert_sanitized_failure(probe(directory, origin.server_port, pin, action="stop"))
            assert origin.requests == [("/_managed-stop", True)]
        assert_sanitized_failure(probe(directory, target.server_port, pin))
        assert target.attempts == 1
        assert target.requests == []
        (directory / "tls-cert.pem").write_text("fictional malformed trust material")
        malformed_pin = hashlib.sha256((directory / "tls-cert.pem").read_bytes()).hexdigest()
        for expected in (pin, malformed_pin):
            assert_sanitized_failure(probe(directory, target.server_port, expected))
        assert target.attempts == 1
        assert target.requests == []
