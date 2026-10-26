"""Small HTTP server helpers for local test doubles."""

from http.server import ThreadingHTTPServer
from socketserver import TCPServer


class LoopbackThreadingHTTPServer(ThreadingHTTPServer):
    """Bind a local test server without resolving its display name through DNS."""

    def server_bind(self) -> None:
        TCPServer.server_bind(self)
        self.server_name = self.server_address[0]
        self.server_port = self.server_address[1]
