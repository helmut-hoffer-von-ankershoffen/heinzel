"""Local test servers must not resolve loopback display names through DNS."""

import socket
from http.server import BaseHTTPRequestHandler

import pytest

from tests.fixtures.local_http import LoopbackThreadingHTTPServer

pytestmark = pytest.mark.unit


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        pass


def test_loopback_server_binds_without_dns_or_global_socket_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    original_getfqdn = socket.getfqdn

    def unexpected_dns(_name: str = "") -> str:
        pytest.fail("loopback server bind must not resolve a display name")

    with monkeypatch.context() as patch:
        patch.setattr(socket, "getfqdn", unexpected_dns)
        server = LoopbackThreadingHTTPServer(("127.0.0.1", 0), Handler)
        try:
            assert server.server_address[0] == "127.0.0.1"
            assert server.server_port > 0
            assert socket.getfqdn is unexpected_dns
        finally:
            server.server_close()
    assert socket.getfqdn is original_getfqdn
