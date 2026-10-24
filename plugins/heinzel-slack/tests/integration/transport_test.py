"""The real Transport, urllib and all, against a local HTTP server: redirects never carry the token elsewhere.

Offline: the server listens on 127.0.0.1 only. It stands in for an API whose redirects point at another origin
(`localhost` on the same port is another origin to urllib and to the Transport) or within its own.
"""

import socketserver
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from heinzel_slack import Transport

pytestmark = pytest.mark.integration

AUTH = {"Authorization": "Bearer xoxp-fake-token"}


class Site(ThreadingHTTPServer):
    seen: list[tuple[str, str, str, str | None, bytes]]

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)  # HTTPServer's would look up the host name, which can hang offline
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


class Handler(BaseHTTPRequestHandler):
    server: Site

    def _answer(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self.server.seen.append(
            (self.command, self.headers.get("Host", ""), self.path, self.headers.get("Authorization"), body)
        )
        port = self.server.server_address[1]
        targets = {"/elsewhere": f"http://localhost:{port}/leak", "/within": f"http://127.0.0.1:{port}/ok"}
        if self.path in targets:
            self.send_response(302)
            self.send_header("Location", targets[self.path])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        answer = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(answer)))
        self.end_headers()
        self.wfile.write(answer)

    do_GET = _answer  # noqa: N815 - http.server's names
    do_POST = _answer  # noqa: N815 - http.server's names

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
    response = Transport().send(f"http://127.0.0.1:{port}/elsewhere", AUTH, None, 5.0)
    assert response.status == 302
    assert response.headers["location"] == f"http://localhost:{port}/leak"
    assert [path for _, _, path, _, _ in site.seen] == ["/elsewhere"]


def test_a_redirect_within_the_origin_is_followed_with_the_token(site):
    port = site.server_address[1]
    response = Transport().send(f"http://127.0.0.1:{port}/within", AUTH, None, 5.0)
    assert (response.status, response.body) == (200, b'{"ok": true}')
    assert [(method, path, auth) for method, _, path, auth, _ in site.seen] == [
        ("GET", "/within", AUTH["Authorization"]),
        ("GET", "/ok", AUTH["Authorization"]),
    ]


def test_a_post_sends_its_body(site):
    port = site.server_address[1]
    response = Transport().send(f"http://127.0.0.1:{port}/api/chat.postMessage", AUTH, b'{"text": "hi"}', 5.0)
    assert response.status == 200
    assert site.seen[0][0] == "POST"
    assert site.seen[0][4] == b'{"text": "hi"}'
