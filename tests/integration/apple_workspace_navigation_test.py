"""Exercise the actual Mac workspace view, WebKit policy delegate and injected Profile bridge."""

import os
import platform
import secrets
import ssl
import subprocess
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread

import pytest

from heinzel.testing import scaled
from tests.fixtures.local_http import LoopbackThreadingHTTPServer

pytestmark = [
    pytest.mark.integration,
    pytest.mark.macos_specific,
    pytest.mark.timeout(func_only=True),  # The Xcode build and compiler have their own bounded deadlines.
    pytest.mark.skipif(sys.platform != "darwin", reason="Requires native SwiftUI and WebKit"),
]
ROOT = Path(__file__).resolve().parents[2]
PROFILE = b"""<main id="root"><section data-testid="app-shell"><textarea></textarea>
<button id="trigger" aria-haspopup="menu" aria-expanded="false">Profile</button></section></main>
<script>
sessionStorage.setItem('hz.token', new URLSearchParams(location.hash.slice(1)).get('token'));
const trigger = document.querySelector('#trigger');
trigger.onclick = () => {
    const menu = document.querySelector('[role=menu]');
    trigger.setAttribute('aria-expanded', String(!menu));
    if (menu) { menu.remove(); return; }
    trigger.insertAdjacentHTML('afterend', '<div role="menu"><a role="menuitem" href="/?settings=">Settings</a>'
        + (window.modern ? '<button role="menuitem">Current menu</button>' : '')
        + '<a role="menuitem" href="/profile">Profile</a></div>');
    if (window.modern) document.querySelector('[role=menu] button').onclick = async () => {
        if (await window.heinzelDesktop.workspaces.open()) trigger.click();
    };
};
</script>"""


@pytest.fixture(scope="module")
def navigation_probe(tmp_path_factory: pytest.TempPathFactory) -> Path:
    build = tmp_path_factory.mktemp("mac-workspace-build")
    subprocess.run(
        [
            "xcodebuild",
            "-project",
            "apple/Heinzel.xcodeproj",
            "-scheme",
            "Heinzel",
            "-configuration",
            "Debug",
            "-destination",
            f"platform=macOS,arch={platform.machine()}",
            "-derivedDataPath",
            str(build),
            "CODE_SIGNING_ALLOWED=NO",
            "ENABLE_TESTABILITY=YES",
            "ONLY_ACTIVE_ARCH=YES",
            "build",
        ],
        cwd=ROOT,
        check=True,
        timeout=scaled(240),
    )
    products = build / "Build/Products/Debug"
    libs = products / "Heinzel.app/Contents/MacOS"
    probe = build / "navigation-probe"
    subprocess.run(
        [
            "xcrun",
            "swiftc",
            "-swift-version",
            "6",
            "-parse-as-library",
            "-target",
            f"{platform.machine()}-apple-macos15.0",
            "-I",
            str(products),
            str(libs / "Heinzel.debug.dylib"),
            "-Xlinker",
            "-rpath",
            "-Xlinker",
            str(libs),
            "apple/tests/WorkspaceNavigationTests.swift",
            "-o",
            str(probe),
        ],
        cwd=ROOT,
        check=True,
        timeout=scaled(60),
    )
    return probe


@pytest.mark.parametrize("scheme", ["http", "https"], ids=["local", "remote"])
def test_established_workspace_keeps_draft_and_opens_mac_profile_chooser(
    navigation_probe: Path, tmp_path: Path, scheme: str
) -> None:
    token = secrets.token_urlsafe(32)
    marker = tmp_path / "verified-after-resource"
    resources: list[int] = []
    writes: list[str] = []

    class Fixture(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            code, mime, body = 404, "application/json", b"{}"
            if self.path == "/":
                code, mime, body = 200, "text/html;charset=utf-8", PROFILE
            elif self.path.startswith("/resource/"):
                code = int(self.path.rsplit("/", 1)[1])
                resources.append(code)
            elif self.path == "/denied":
                code = 503
                resources.append(0)
            elif self.path == "/api/lifecycle" and self.headers.get("Authorization") == "Bearer " + token:
                code, body = (403 if resources and resources[-1] == 0 else 200), b'{"instance":"fixture","version":"1"}'
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            if self.path == "/api/lifecycle" and code in {200, 403} and resources:
                marker.write_text(str(resources[-1]))

        def do_POST(self) -> None:
            writes.append(self.path)
            self.send_error(405)

    with LoopbackThreadingHTTPServer(("127.0.0.1", 0), Fixture) as server:
        pin = tmp_path / "leaf.der"
        pin.touch()
        if scheme == "https":
            key, cert = tmp_path / "key", tmp_path / "cert"
            subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-days",
                    "1",
                    "-subj",
                    "/CN=127.0.0.1",
                    "-addext",
                    "subjectAltName=IP:127.0.0.1",
                    "-addext",
                    "extendedKeyUsage=serverAuth",
                    "-addext",
                    "keyUsage=digitalSignature,keyEncipherment",
                    "-keyout",
                    str(key),
                    "-out",
                    str(cert),
                ],
                capture_output=True,
                check=True,
                timeout=scaled(20),
            )
            pin.write_bytes(ssl.PEM_cert_to_DER_cert(cert.read_text()))
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert, key)
            server.socket = context.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
        Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        try:
            subprocess.run(
                [
                    str(navigation_probe),
                    f"{scheme}://127.0.0.1:{server.server_port}",
                    str(marker),
                    str(pin),
                ],
                env={**os.environ, "FIXTURE_SESSION": token, "HOME": str(tmp_path), "CFFIXED_USER_HOME": str(tmp_path)},
                check=True,
                timeout=scaled(60),
            )
        finally:
            server.shutdown()
    assert resources == [503, 404, 410, 401, 403, 0]
    assert writes == []
