"""Exercise the native prompt JavaScript against the real owner chat operations."""

import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest

from heinzel import CurrentUser
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._user_chat import UserChatService
from heinzel.modules.system.runner._snapshot import RunnerSnapshotService
from tests.fixtures.local_http import LoopbackThreadingHTTPServer
from tests.integration.chat_test import AUTH, web_client
from tests.unit.modules.user_chat_test import FixtureConfig, fake_claude

pytestmark = pytest.mark.integration


def test_native_prompt_dispatch_and_lost_answer_recovery(make_kernel, tmp_path):
    kernel = make_kernel(builtins=True)
    crud = kernel.resolve(SessionCrud)
    executable = fake_claude(tmp_path)
    kernel.bind(
        UserChatService,
        UserChatService(
            crud, kernel.resolve(CurrentUser), RunnerSnapshotService(), load=lambda: FixtureConfig(executable)
        ),
    )
    client = web_client(kernel, tmp_path)
    created = client.post("/api/heinzel/new-chat", headers=AUTH, json={"request_id": "new"})
    assert created.status_code == 200
    session = created.json()["session_id"]
    source = "\n".join(
        Path(f"apple/App/{n}.swift").read_text(encoding="utf-8")
        for n in ["WorkspaceView", "NativePrompt", "TitleBarDragLayout", "Navigation"]
    )
    body = source.split("let result = try await inspectWorkspace(in: workspace, arguments: [", 1)[1]
    body = body.split('], body: """', 1)[1].split('""") as? String', 1)[0]
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            data = self.rfile.read(int(self.headers["Content-Length"]))
            calls.append((self.path, json.loads(data)))
            response = client.post(self.path, headers={**AUTH, "Content-Type": "application/json"}, content=data)
            self.send_response(response.status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(response.content)

        def log_message(self, *_args):
            pass

    server = LoopbackThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    script = """
const [body, base, sessionID, requestID, loseReply] = JSON.parse(process.argv[1]);
const realFetch = globalThis.fetch;
const fetch = async (path, options) => {
  const response = await realFetch(base + path, options);
  if (loseReply && path.endsWith(loseReply)) throw new Error('lost response');
  return response;
};
const run = new (Object.getPrototypeOf(async function(){}).constructor)(
  'sessionID', 'requestID', 'prompt', 'options', 'fetch', body);
run(sessionID, requestID, 'Hello', {headers: {}}, fetch)
  .then(result => console.log(result)).catch(() => console.log('unconfirmed'));
"""

    def run(request_id, lose_reply=""):
        args = [body, f"http://127.0.0.1:{server.server_port}", session, request_id, lose_reply]
        return subprocess.run(
            ["node", "-e", script, json.dumps(args)], check=True, capture_output=True, text=True, timeout=30
        ).stdout.strip()

    try:
        assert run("first") == "accepted"
        assert [path for path, _ in calls] == ["/api/heinzel/chat", "/api/heinzel/answer-chat"]
        assert run("second", lose_reply="/answer-chat") == "unconfirmed"
        assert run("second") == "accepted"
        assert all(call["request_id"] == "second" for _, call in calls[2:])
        assert run("third", lose_reply="/chat") == "unconfirmed"
        assert run("third") == "accepted"
        assert (tmp_path / "model-calls").read_text() == "xxxxxx"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
