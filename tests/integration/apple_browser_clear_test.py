"""Exercise the native Browser's guest lifecycle in an isolated WebKit session."""

import platform
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest

from heinzel.testing import scaled
from tests.fixtures.local_http import LoopbackThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [
    pytest.mark.integration,
    pytest.mark.macos_specific,
    pytest.mark.skipif(sys.platform != "darwin", reason="The browser uses macOS WebKit"),
    pytest.mark.timeout(75),
]


@pytest.fixture(scope="module")
def browser_probe(tmp_path_factory: pytest.TempPathFactory) -> Path:
    probe = tmp_path_factory.mktemp("apple-browser") / "probe"
    app = ROOT / "apple/App"
    subprocess.run(
        [
            "xcrun",
            "swiftc",
            "-swift-version",
            "6",
            "-target",
            f"{platform.machine()}-apple-macosx15.0",
            *(
                str(app / f"{name}.swift")
                for name in ("Endpoint", "BrowserNetwork", "NavigationIdentity", "IntegratedBrowser")
            ),
            str(ROOT / "tests/fixtures/apple-browser-clear.swift"),
            "-o",
            str(probe),
        ],
        check=True,
        timeout=60,
    )
    return probe


def test_guest_clear_serializes_detach_failure_and_retry(browser_probe):
    subprocess.run([str(browser_probe)], check=True, timeout=10)


def test_guest_open_requires_workspace_authentication_without_preview_flag(browser_probe):
    class Workspace(BaseHTTPRequestHandler):
        def do_GET(self):
            token = self.headers.get("Authorization")
            if self.path == "/" or (self.path == "/api/system/config/show" and token == "Bearer fallback"):
                self.respond(200, "text/html", b"<!doctype html><title>Workspace</title>")
            elif self.path == "/api/system/config/show":
                if token != "Bearer fixture":
                    self.send_error(401)
                    return
                self.respond(200, "application/json", b'{"settings":[{"section":"hz","key":"features","value":[]}]}')
            else:
                self.send_error(404)

        def respond(self, status, content_type, body):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    with LoopbackThreadingHTTPServer(("127.0.0.1", 0), Workspace) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            origin = f"http://127.0.0.1:{server.server_port}"
            subprocess.run(
                [
                    str(browser_probe),
                    f"{origin}/#token={'x' * 32}",
                    "https://example.com/",
                    "https://workspace.example.com",
                    "https://127.0.0.1/",
                ],
                check=True,
                timeout=scaled(10),
            )
        finally:
            server.shutdown()
            thread.join()
