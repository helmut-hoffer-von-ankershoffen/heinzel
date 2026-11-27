"""Exercise the Mac app's foreground service without opening a window."""

import os
import platform
import signal
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
with open("argv", "a") as calls:
    calls.write(" ".join(sys.argv[1:]) + "\\n")
if sys.argv[1:] != ["attach"] or mode == "outdated":
    exit(2)  # anything but `hz ui attach`, such as `serve --port 0`, is refused; an old hz has no attach
if mode == "eof":
    os.write(1, b"unfinished line")
    exit(0)
if mode == "oversized":
    os.write(1, b"x" * 4097)
    exit(0)

token = "x" * 43
if mode == "backoff" and os.path.exists("listener.port"):
    # A later explicit attach supplies a new launch token for the existing listener.
    with open("listener.port") as listener:
        port = listener.read().strip()
    with open("rotation", "w"):
        pass
    print(f"http://127.0.0.1:{port}/#token=" + "y" * 43)
    exit(0)

answered = []
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        accepted = "y" * 43 if mode == "backoff" and os.path.exists("rotation") else token
        if self.headers.get("Authorization") != "Bearer " + accepted:
            self.send_error(401)
            return
        answered.append(self.path)
        if mode == "prepare":
            with open("probes", "a") as record:
                record.write("checked\\n")
        if mode == "flaky" and self.path == "/api/lifecycle" and len(answered) in {2, 3}:
            self.send_error(503)  # two failed probes during a restart, then it answers again
            return
        if self.path == "/api/lifecycle":
            # The first response attaches. Then exercise the four failure cases and recover.
            if mode == "lifecycle-errors" and 2 <= len(answered) <= 5:
                status, body = [(401, b""), (403, b""), (503, b""), (200, b"{}")][len(answered) - 2]
                self.send_response(status)
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"instance":"fixture","version":"1.0","restartable":true}')
        else:
            self.send_error(404)

# The owned service: not a child of the app. The attach command forks it off, prints its link and exits.
server = LoopbackThreadingHTTPServer(("127.0.0.1", 0), Handler)
if mode == "backoff":
    with open("listener.port", "w") as listener:
        listener.write(str(server.server_port))
service = os.fork()
if service == 0:
    os.setsid()
    signal.alarm(20)
    server.serve_forever()
    os._exit(0)
with open("service.pid", "w") as record:
    record.write(str(service))
os.write(1, f"http://127.0.0.1:{server.server_port}/#token={token}\\n".encode())
exit(1 if mode == "fails" else 0)
"""


PROBES = {
    "lost",
    "flaky",
    "overlap",
    "backoff",
    "outdated",
    "prepare",
    "lifecycle-errors",
    "continuity",
    "page-authorization",
}


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
@pytest.mark.parametrize(
    "mode",
    [
        "attach",
        "lost",
        "flaky",
        "overlap",
        "backoff",
        "outdated",
        "prepare",
        "continuity",
        "fails",
        "eof",
        "oversized",
        "lifecycle-errors",
        "page-authorization",
    ],
)
def test_mac_app_attaches_to_the_owned_service_and_never_spawns_or_stops_one(
    local_service_probe: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    # LocalService passes "ui" as the first argument, so Python runs this fixture file.
    (tmp_path / "ui").write_text(CHILD, encoding="utf-8")
    monkeypatch.setenv("HZ_SPIKE_CLI", sys.executable)
    monkeypatch.setenv("HEINZEL_PROBE_MODE", mode)
    monkeypatch.setenv("HEINZEL_PROBE_REPO", str(ROOT))
    try:
        subprocess.run(
            [str(local_service_probe), {"attach": "open"}.get(mode, mode if mode in PROBES else "refuse")],
            cwd=tmp_path,
            check=True,
            timeout=15,
        )
        # Loss and explicit reattachment after rotation need a second attach. Concurrent starts share one.
        expected_attaches = 2 if mode in {"lost", "backoff"} else 1
        assert (tmp_path / "argv").read_text(encoding="utf-8") == "attach\n" * expected_attaches
    finally:
        record = tmp_path / "service.pid"
        if record.exists():
            os.kill(int(record.read_text(encoding="utf-8")), signal.SIGTERM)  # the fixture's own service only
