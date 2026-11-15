"""Exercise the Mac app's foreground service without opening a window."""

import platform
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.macos_specific,
    pytest.mark.skipif(sys.platform != "darwin", reason="The Swift service uses macOS AppKit"),
]
ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "apple" / "App"

CHILD = """\
import os
import signal
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.environ["HEINZEL_PROBE_REPO"])
from tests.fixtures.local_http import LoopbackThreadingHTTPServer

mode = os.environ["HEINZEL_PROBE_MODE"]
signal.alarm(20)
signal.signal(signal.SIGINT, lambda *_: exit(0))

if mode == "eof":
    os.write(1, b"unfinished line")
    exit(0)
if mode == "oversized":
    os.write(1, b"x" * 4097)
    signal.pause()
    exit(1)

token = "x" * 43
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer " + token:
            self.send_error(401)
            return
        if self.path == "/api/lifecycle":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"instance":"fixture","version":"1.0","restartable":true}')
        elif self.path == "/noise":
            print("x" * 1048576, flush=True)
            self.send_response(204)
            self.end_headers()
        else:
            self.send_error(404)

server = LoopbackThreadingHTTPServer(("127.0.0.1", 0), Handler)
address = f"http://127.0.0.1:{server.server_port}/#token={token}"
os.write(1, (address + "\\ntrailing output\\n").encode())
server.serve_forever()
"""


@pytest.fixture(scope="module")
def local_service_probe(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if int(platform.mac_ver()[0].partition(".")[0]) < 15:
        pytest.skip("The Swift desktop app requires macOS 15 or later")
    probe = tmp_path_factory.mktemp("apple-local-service") / "probe"
    subprocess.run(
        [
            "xcrun",
            "swiftc",
            "-swift-version",
            "6",
            "-D",
            "DEBUG",
            "-target",
            f"{platform.machine()}-apple-macosx15.0",
            *(str(APP / f"{name}.swift") for name in ("Endpoint", "BrowserNetwork", "LocalService")),
            str(ROOT / "tests" / "fixtures" / "apple-local-service.swift"),
            "-o",
            str(probe),
        ],
        check=True,
        timeout=55,
    )
    return probe


@pytest.mark.timeout(75)  # Includes a cold Swift compiler and SDK module cache.
@pytest.mark.parametrize("mode", ["drain", "eof", "oversized"])
def test_mac_local_service_reads_only_one_bounded_startup_line_and_drains(
    local_service_probe: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    # LocalService passes "ui" as the first argument, so Python runs this fixture file.
    (tmp_path / "ui").write_text(CHILD, encoding="utf-8")
    monkeypatch.setenv("HZ_SPIKE_CLI", sys.executable)
    monkeypatch.setenv("HEINZEL_PROBE_MODE", mode)
    monkeypatch.setenv("HEINZEL_PROBE_REPO", str(ROOT))
    subprocess.run(
        [str(local_service_probe), "open" if mode == "drain" else "refuse"], cwd=tmp_path, check=True, timeout=15
    )
