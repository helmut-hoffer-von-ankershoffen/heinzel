"""An additional fictional 401 control; this never replaces the real-server cases."""

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from heinzel import HeinzelError
from heinzel.heads.mcp._http_check import check_http

pytestmark = pytest.mark.unit


def test_fake_auth_rejection() -> None:
    """Observe a constant 401 and require the ordinary sanitized failure."""

    class Reject(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()
            self.wfile.flush()
            path = Path(os.environ["VNXD_PHASE_DIR"]) / "fake.events"
            with path.open("ab") as stream:
                stream.write(f"fake.server.401_sent {time.monotonic_ns()}\n".encode("ascii"))

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Reject)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/mcp"
        with pytest.raises(HeinzelError, match="could not be authenticated"):
            check_http(endpoint, "hzmcp_synthetic_wrong")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()  # noqa: S101 - test assertion
