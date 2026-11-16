"""Opt-in cross-check of native fenced recovery against the actual Core routes."""

import base64
import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from heinzel.heads import web
from heinzel.heads.web._devices import Devices, digest

pytestmark = pytest.mark.integration


def test_native_fenced_retry_after_lost_core_reply(make_kernel, tmp_path):
    harness = os.environ.get("HZ_WIDGET_FENCED_HARNESS")
    if not harness:
        pytest.skip("Set HZ_WIDGET_FENCED_HARNESS to the separately built native test harness")
    root = tmp_path / "native"
    root.mkdir()
    devices = Devices(tmp_path / "devices")
    client = TestClient(
        web.build(make_kernel(builtins=True), port=8000, token="widget-contract-session", devices=devices, static=None),
        base_url="http://127.0.0.1:8000",
    )
    session = {"Authorization": "Bearer widget-contract-session"}
    paired = client.post("/api/_web/device", headers=session, json={}).json()
    headers = {"Authorization": f"Bearer {paired['token']}"}

    def native(operation, *arguments):
        result = subprocess.run([harness, str(root), operation, *arguments], check=True, capture_output=True, text=True)
        return result.stdout.strip()

    assert native("prepare") == "prepared"
    first_status = client.post("/api/_web/widget-fence", headers=headers)
    assert first_status.status_code == 200
    (root / "status.json").write_text(first_status.text)
    request = json.loads(base64.b64decode(native("bind")))
    committed = client.post("/api/_web/widget-fenced-credential", headers=headers, json=request)
    assert committed.status_code == 200
    # The first committed reply is lost; a fresh process must replay the exact request.
    (root / "status.json").write_text(client.post("/api/_web/widget-fence", headers=headers).text)
    replay_request = json.loads(base64.b64decode(native("bind")))
    assert replay_request == request
    replay = client.post("/api/_web/widget-fenced-credential", headers=headers, json=replay_request)
    assert replay.status_code == 200
    assert replay.json() == committed.json()
    (root / "receipt.json").write_text(replay.text)
    assert native("complete") == "installed"
    assert native("verify", request["commitment"]) == "verified"
    fixture_credential = json.loads((root / "active").read_bytes())
    widget_token = fixture_credential["token"]
    assert digest(widget_token) == request["commitment"]
    assert widget_token not in devices.path.read_text()
    widget_headers = {
        "Authorization": f"Bearer {widget_token}",
        "X-Hz-Workspace-Id": first_status.json()["workspace_id"],
    }
    assert client.get("/api/_web/widget-snapshot", headers=widget_headers).status_code == 200


def test_native_script_and_core_recover_lost_reply(make_kernel, tmp_path):
    harness = os.environ.get("HZ_WIDGET_FENCED_HARNESS")
    source = os.environ.get("HZ_WIDGET_ENROLLMENT_SOURCE")
    node = shutil.which("node")
    if not harness or not source or not node:
        pytest.skip("Native harness, enrollment source and Node are required for the cross-check")
    root = tmp_path / "native"
    root.mkdir()
    devices = Devices(tmp_path / "devices")
    client = TestClient(
        web.build(make_kernel(builtins=True), port=8000, token="widget-script-session", devices=devices, static=None),
        base_url="http://127.0.0.1:8000",
    )
    paired = client.post("/api/_web/device", headers={"Authorization": "Bearer widget-script-session"}, json={}).json()
    swift = Path(source).read_text(encoding="utf-8")
    script = swift.split('static let script = """', 1)[1].split('        """', 1)[0]

    class Bridge(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            response = client.post(
                self.path,
                content=self.rfile.read(length),
                headers={
                    "Authorization": self.headers.get("Authorization", ""),
                    "Content-Type": self.headers.get("Content-Type", "application/json"),
                },
            )
            self.send_response(response.status_code)
            self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
            self.end_headers()
            self.wfile.write(response.content)

        def log_message(self, *_args):
            return

    bridge = ThreadingHTTPServer(("127.0.0.1", 0), Bridge)
    thread = threading.Thread(target=bridge.serve_forever, daemon=True)
    thread.start()
    runner = """
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
const script = Buffer.from(process.argv[1], 'base64').toString('utf8');
const [device, bridge, operation, payload] = process.argv.slice(2);
globalThis.location = {origin:'https://example.test'};
globalThis.localStorage = {getItem: key => key === 'hz.device' ? device : null};
const realFetch = globalThis.fetch;
globalThis.fetch = (path, options) => realFetch(bridge + path, options);
new AsyncFunction('origin','operation','payload',script)('https://example.test',operation,payload)
  .then(value => process.stdout.write(JSON.stringify(value))).catch(() => process.exit(1));
"""

    def native(operation, *arguments):
        return subprocess.run(
            [harness, str(root), operation, *arguments], check=True, capture_output=True, text=True
        ).stdout.strip()

    def webkit(operation, payload=""):
        address = f"http://127.0.0.1:{bridge.server_port}"
        result = subprocess.run(
            [
                node,
                "-e",
                runner,
                base64.b64encode(script.encode()).decode(),
                paired["token"],
                address,
                operation,
                payload,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        value = json.loads(result.stdout)
        assert value["state"] == "ok"
        assert paired["token"] not in result.stdout
        return value["body"]

    try:
        assert native("prepare") == "prepared"
        (root / "status.json").write_text(webkit("negotiate"))
        first = base64.b64decode(native("bind")).decode()
        committed = webkit("mutate", first)  # Deliberately lose the first reply.
        (root / "status.json").write_text(webkit("negotiate"))
        replay = base64.b64decode(native("bind")).decode()
        assert replay == first
        receipt = webkit("mutate", replay)
        assert json.loads(receipt) == json.loads(committed)
        (root / "receipt.json").write_text(receipt)
        assert native("complete") == "installed"
        assert native("verify", json.loads(first)["commitment"]) == "verified"
    finally:
        bridge.shutdown()
        thread.join(timeout=5)
        bridge.server_close()
