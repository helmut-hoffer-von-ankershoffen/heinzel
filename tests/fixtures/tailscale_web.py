"""A real restartable web head with an isolated, loopback-only fake Tailscale local API.

The fake client never finds or starts a vendor executable. Only its documented read commands and local credential
discovery are emulated; ServeStore still performs real authenticated HTTP and conditional writes. The execute seam
restarts this fixture so the replacement has the same fake bindings and the production socket/token handover.
"""

import base64
import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from local_http import LoopbackThreadingHTTPServer

ROOT = Path(sys.argv[1])
LIFECYCLE = ROOT / "fixture-lifecycle.jsonl"


def record_lifecycle(event, **details):
    """Record process startup without copying the announced URL or session token."""
    with LIFECYCLE.open("a", encoding="utf-8") as events:
        events.write(json.dumps({"event": event, "pid": os.getpid(), **details}) + "\n")


def resume_socket_state():
    """Record whether the restart descriptor is listening; never log its fd, port or token."""
    descriptor, _, _ = os.environ.get("HEINZEL_UI_RESUME", "").partition(":")
    if not descriptor.isdigit():
        return {"resume_present": False, "resume_socket": False, "resume_listening": False}
    try:
        sock = socket.fromfd(int(descriptor), socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        return {"resume_present": True, "resume_socket": False, "resume_listening": False}
    try:
        try:
            listening = bool(sock.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN))
        except OSError:
            try:
                sock.getpeername()
            except OSError:
                listening = True
            else:
                listening = False
        return {"resume_present": True, "resume_socket": True, "resume_listening": listening}
    finally:
        sock.close()


record_lifecycle("boot", **resume_socket_state())

from heinzel_tailscale import module  # noqa: E402 - log a boot event before app imports to find startup hangs
from heinzel_tailscale._cli import Result, TailscaleCli  # noqa: E402

from heinzel import Config, Kernel, ModuleRecord, Settings  # noqa: E402
from heinzel.heads import web  # noqa: E402
from heinzel.kernel import Origin, discover  # noqa: E402

record_lifecycle("imports-ready")

DNS = "workstation.tail-example.ts.net"
PASSWORD = "isolated-fake-local-api"
STATE = ROOT / "vendor.json"


def state():
    return json.loads(STATE.read_text(encoding="utf-8"))


class LocalAPI(BaseHTTPRequestHandler):
    """An exact-version Serve endpoint with no API for launching or configuring real OS networking."""

    def log_message(self, *_arguments):
        pass

    def answer(self, status, body, etag=None):
        content = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        if etag is not None:
            self.send_header("ETag", str(etag))
        self.end_headers()
        self.wfile.write(content)

    def accepted(self):
        expected = "Basic " + base64.b64encode(f":{PASSWORD}".encode()).decode()
        return self.path == "/localapi/v0/serve-config" and self.headers.get("Authorization") == expected

    def do_GET(self):
        if not self.accepted():
            self.answer(403, {})
            return
        current = state()
        self.answer(200, current["config"], current["version"])

    def do_POST(self):
        if not self.accepted():
            self.answer(403, {})
            return
        current = state()
        if self.headers.get("If-Match") != str(current["version"]):
            self.answer(412, {})
            return
        size = int(self.headers.get("Content-Length", "0"))
        assert 0 < size <= 256 * 1024
        current["config"] = json.loads(self.rfile.read(size))
        current["version"] += 1
        current["writes"] += 1
        temporary = STATE.with_suffix(".next")
        with temporary.open("x", encoding="utf-8") as target:
            Path(temporary).chmod(0o600)
            json.dump(current, target)
        temporary.replace(STATE)
        self.answer(200, {})


class FakeCli(TailscaleCli):
    def executable(self):
        return "/fixture/never-executed-tailscale"

    def run(self, arguments):
        if tuple(arguments) == ("status", "--json", "--peers=false"):
            payload = {"BackendState": "Running", "Self": {"DNSName": DNS + "."}, "CertDomains": [DNS]}
        elif tuple(arguments) == ("serve", "status", "--json"):
            payload = state()["config"]
        else:
            assert tuple(arguments) == ("debug", "local-creds")
            command = f"curl -u:{PASSWORD} http://localhost:{local.server_port}/localapi/v0/status"
            return Result(0, command.encode(), b"")
        return Result(0, json.dumps(payload).encode(), b"")


def execute(executable, _command, environment):
    local.shutdown()
    local.server_close()
    record_lifecycle("exec")
    os.execve(  # noqa: S606 - the current interpreter and this fixture; no vendor executable or shell
        executable, [executable, str(Path(__file__).resolve()), str(ROOT)], environment
    )


def announce(url):
    record_lifecycle("ready")
    sys.stdout.write(json.dumps({"url": url}) + "\n")
    sys.stdout.flush()


local = LoopbackThreadingHTTPServer(("127.0.0.1", 0), LocalAPI)
threading.Thread(target=local.serve_forever, daemon=True).start()
record_lifecycle("local-api-started")
configuration = Config.load(ROOT)
records = discover(plugins=False)[0]
records.append(ModuleRecord("tailscale", module, Origin("heinzel-tailscale", "0.0.0", builtin=False)))
kernel = Kernel(configuration.settings(Settings, "hz"), records, config=configuration)
kernel.bind(TailscaleCli, FakeCli())
record_lifecycle("kernel-ready")
web.serve(kernel, browser=False, announce=announce, execute=execute)
