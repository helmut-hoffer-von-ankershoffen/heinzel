"""Opt-in real macOS WebKit fixture against isolated synthetic Core and loopback TLS."""

import hashlib
import json
import os
import socket
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi.testclient import TestClient

from heinzel.heads import web
from heinzel.heads.web._devices import Devices

pytestmark = pytest.mark.integration


class BoundedTLSServer(ThreadingHTTPServer):
    daemon_threads = True

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(3)
        return connection, address


def test_real_webkit_cancelled_lost_issue_reply(make_kernel, tmp_path):
    harness = os.environ.get("HZ_WIDGET_WEBKIT_HARNESS")
    if not harness:
        pytest.skip("Set HZ_WIDGET_WEBKIT_HARNESS to the separately built signed test fixture")
    with TemporaryDirectory(prefix="widget-webkit-", dir=tmp_path) as fixture_dir:
        run_webkit_fixture(make_kernel, Path(fixture_dir), harness)


def assert_webkit_exchange(harness, fixture_input, runtime, state):
    port, paired, client, devices = runtime
    bad_pin = {**fixture_input, "certificateSHA256": "0" * 64}
    rejected = subprocess.run(
        [harness], check=False, input=json.dumps(bad_pin), text=True, capture_output=True, timeout=45
    )
    assert rejected.returncode != 0
    assert "certificate-pin=false" in rejected.stderr
    assert state["bootstrap_requests"] == 0
    assert state["operations"] == []
    bad_host = {**fixture_input, "origin": f"https://localhost:{port}"}
    rejected_host = subprocess.run(
        [harness], check=False, input=json.dumps(bad_host), text=True, capture_output=True, timeout=45
    )
    assert rejected_host.returncode != 0
    assert "fixture failed: origin" in rejected_host.stderr
    assert state["bootstrap_requests"] == 0
    assert state["operations"] == []
    completed = subprocess.run(
        [harness], check=False, input=json.dumps(fixture_input), text=True, capture_output=True, timeout=45
    )
    assert completed.returncode == 0, (
        f"{completed.stderr}; bootstrap={state['bootstrap_requests']}; operations={state['operations']}"
    )
    assert "PASS: real WKWebView lost-reply cancellation and revoke" in completed.stdout
    assert paired["token"] not in completed.stdout
    assert paired["token"] not in completed.stderr
    assert state["dropped"]
    assert state["operations"] == ["issue", "revoke"]
    assert state["negotiations"] == 2
    status = client.post("/api/_web/widget-fence", headers={"Authorization": f"Bearer {paired['token']}"})
    assert status.status_code == 200
    assert status.json()["last_attempt"] is not None
    assert json.loads(devices.path.read_text())[0]["widget"] is None


def make_bridge(client, paired, state):
    class Bridge(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_GET(self):
            if self.path != "/":
                self.send_error(404)
                return
            state["bootstrap_requests"] += 1
            script = f"localStorage.setItem('hz.device',{json.dumps(paired['token'])});"
            body = f"<!doctype html><script>{script}</script>".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path not in {"/api/_web/widget-fence", "/api/_web/widget-fenced-credential"}:
                self.send_error(404)
                return
            size = int(self.headers.get("Content-Length", "0"))
            if size > 4096:
                self.send_error(413)
                return
            body = self.rfile.read(size)
            if self.path.endswith("widget-fence"):
                state["negotiations"] += 1
            response = client.post(
                self.path,
                content=body,
                headers={"Authorization": self.headers.get("Authorization", ""), "Content-Type": "application/json"},
            )
            if self.path.endswith("widget-fenced-credential"):
                operation = json.loads(body)["operation"]
                state["operations"].append(operation)
                if operation == "issue" and response.status_code == 200 and not state["dropped"]:
                    state["dropped"] = True
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
            content = response.content
            self.send_response(response.status_code)
            self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    return Bridge


def run_webkit_fixture(make_kernel, tmp_path, harness):
    Path(tmp_path).chmod(0o700)
    cert = tmp_path / "fixture-cert.pem"
    key = tmp_path / "fixture-key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-sha256",
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
        check=True,
        capture_output=True,
        timeout=20,
    )
    Path(key).chmod(0o600)
    fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest()
    devices = Devices(tmp_path / "synthetic-devices")
    client = TestClient(
        web.build(make_kernel(builtins=True), port=8000, token="fixture-webkit-session", devices=devices, static=None),
        base_url="http://127.0.0.1:8000",
    )
    session = {"Authorization": "Bearer fixture-webkit-session"}
    paired = client.post("/api/_web/device", headers=session, json={}).json()
    state = {"dropped": False, "operations": [], "bootstrap_requests": 0, "negotiations": 0}

    bridge_handler = make_bridge(client, paired, state)
    bridge = BoundedTLSServer(("127.0.0.1", 0), bridge_handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    bridge.socket = context.wrap_socket(bridge.socket, server_side=True, do_handshake_on_connect=False)
    thread = threading.Thread(target=bridge.serve_forever, daemon=True)
    thread.start()
    stalled = None
    try:
        stalled = socket.create_connection(("127.0.0.1", bridge.server_port), timeout=3)
        fixture_input = {
            "origin": f"https://127.0.0.1:{bridge.server_port}",
            "certificateSHA256": fingerprint,
            "directory": str(tmp_path),
        }
        assert_webkit_exchange(harness, fixture_input, (bridge.server_port, paired, client, devices), state)
    finally:
        if stalled is not None:
            stalled.close()
        bridge.shutdown()
        thread.join(timeout=5)
        bridge.server_close()
