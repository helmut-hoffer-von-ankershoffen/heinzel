"""The real Transport, urllib and all, against a local HTTP server: redirects never carry the token elsewhere.

Offline: the server listens on 127.0.0.1 only. It stands in for a site whose redirects point at another origin
(`localhost` on the same port is another origin to urllib and to the Transport) or within its own.
"""

import socketserver
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from heinzel_atlassian import Transport

pytestmark = pytest.mark.integration

AUTH = {"Authorization": "Basic ZmFrZS10b2tlbg=="}


class Site(ThreadingHTTPServer):
    seen: list[tuple[str, str, str | None]]

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)  # HTTPServer's would look up the host name, which can hang offline
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


class Handler(BaseHTTPRequestHandler):
    server: Site

    def do_GET(self) -> None:
        host = self.headers.get("Host", "")
        self.server.seen.append((host, self.path, self.headers.get("Authorization")))
        port = self.server.server_address[1]
        targets = {"/elsewhere": f"http://localhost:{port}/leak", "/within": f"http://127.0.0.1:{port}/ok"}
        if self.path in targets:
            self.send_response(302)
            self.send_header("Location", targets[self.path])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - the base class names it so
        pass


@pytest.fixture
def site(monkeypatch) -> Iterator[Site]:
    for name in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("no_proxy", "*")
    server = Site(("127.0.0.1", 0), Handler)
    server.seen = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_a_redirect_to_another_origin_is_refused_and_nothing_is_sent_there(site):
    port = site.server_address[1]
    response = Transport().get(f"http://127.0.0.1:{port}/elsewhere", AUTH, 5.0)
    assert response.status == 302
    assert response.headers["location"] == f"http://localhost:{port}/leak"
    assert [path for _, path, _ in site.seen] == ["/elsewhere"]


def test_a_redirect_within_the_origin_is_followed_with_the_token(site):
    port = site.server_address[1]
    response = Transport().get(f"http://127.0.0.1:{port}/within", AUTH, 5.0)
    assert (response.status, response.body) == (200, b'{"ok": true}')
    assert site.seen == [
        (f"127.0.0.1:{port}", "/within", AUTH["Authorization"]),
        (f"127.0.0.1:{port}", "/ok", AUTH["Authorization"]),
    ]
