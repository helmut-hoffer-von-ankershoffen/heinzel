"""Exercise the shared iPhone and Mac Information reader in a real, isolated WKWebView."""

import subprocess
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread

import pytest

from heinzel.testing import scaled
from tests.fixtures.local_http import LoopbackThreadingHTTPServer


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "darwin", reason="Requires WebKit")
def test_open_information_drops_facts_when_document_authority_changes(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    probe = tmp_path / "workspace-information-authority"
    subprocess.run(
        [
            "xcrun",
            "swiftc",
            "-swift-version",
            "6",
            "-parse-as-library",
            "apple/App/Endpoint.swift",
            "apple/App/BrowserNetwork.swift",
            "apple/App/WorkspaceDirectory.swift",
            "apple/App/WorkspaceDocumentFacts.swift",
            "apple/tests/WorkspaceDocumentFactsTests.swift",
            "-o",
            str(probe),
        ],
        cwd=root,
        check=True,
        timeout=scaled(90),
    )

    class Workspace(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            if self.path == "/manifest.webmanifest":
                self.send_header("Content-Type", "application/manifest+json")
                body = rb'{"name":"Heinzelm\u00e4nnchen","short_name":"hz"}'
            else:
                self.send_header("Content-Type", "text/html")
                body = b"<main>Workspace</main>"
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *args: object) -> None:
            pass

    with LoopbackThreadingHTTPServer(("127.0.0.1", 0), Workspace) as server:
        Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        try:
            subprocess.run([str(probe), f"http://127.0.0.1:{server.server_port}"], check=True, timeout=scaled(35))
        finally:
            server.shutdown()
