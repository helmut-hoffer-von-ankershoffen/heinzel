"""Local test servers must not resolve loopback display names through DNS."""

import re
import socket
from http.server import BaseHTTPRequestHandler
from pathlib import Path

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


def test_no_test_server_binds_through_a_dns_lookup() -> None:
    """A plain `HTTPServer` asks DNS for its display name on bind, which stalls for seconds on macOS runners.

    Every test server binds through a loopback subclass that skips it, or binds its own socket.
    """
    tests = Path(__file__).resolve().parents[1]
    construction = re.compile(r"\b(\w*HTTPServer)\(")
    offenders = [
        f"{path.relative_to(tests)}:{number}"
        for path in sorted(tests.rglob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        for match in construction.finditer(line)
        if not match.group(1).startswith("Loopback") and "bind_and_activate=False" not in line
    ]
    assert offenders == []
