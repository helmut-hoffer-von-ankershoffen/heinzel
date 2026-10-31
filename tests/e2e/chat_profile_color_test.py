"""Mounted fake chat proposes a look; owner save persists the actual rendered color."""

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import ClassVar
from urllib.parse import parse_qs, urlsplit

import pytest
import uvicorn

from heinzel import CurrentUser, Notifications
from heinzel.heads import web
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._user_chat import UserChatService
from heinzel.modules.system.runner._snapshot import RunnerSnapshotService
from tests.e2e.template_scheduled_test import MountedNotices
from tests.integration.chat_profile_color_test import _model
from tests.integration.chat_test import AUTH, web_client
from tests.unit.modules.user_chat_test import FixtureConfig

pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.timeout(600)
def test_mounted_chat_profile_color_save_and_reload(make_kernel, tmp_path, monkeypatch):
    kernel = make_kernel(builtins=True)
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    look = {"age": "old", "beard": "long", "hat": "blue", "shirt": "rose", "build": "round", "accessories": ["scarf"]}
    executable = _model(tmp_path, look)
    kernel.bind(
        UserChatService, UserChatService(crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable))
    )
    kernel.bind(Notifications, MountedNotices())
    client = web_client(kernel, tmp_path)
    initial = {**look, "hat": "green"}
    created = client.post(
        "/api/heinzel/new-chat", headers=AUTH, json={"request_id": "new", "name": "Original", "appearance": initial}
    )
    assert created.status_code == 200
    session = created.json()["session_id"]

    class RecordedServer(uvicorn.Server):
        instances: ClassVar[list[uvicorn.Server]] = []

        def __init__(self, config):
            super().__init__(config)
            self.instances.append(self)

    monkeypatch.setattr(uvicorn, "Server", RecordedServer)
    ready, urls = threading.Event(), []

    def announce(value):
        urls.append(value)
        ready.set()

    server = threading.Thread(target=lambda: web.serve(kernel, port=0, browser=False, announce=announce), daemon=True)
    server.start()
    try:
        assert ready.wait(15)
        token = parse_qs(urlsplit(urls[0]).fragment).get("token", [""])[0]
        assert token
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/web_build.py"),
                "exec",
                "node",
                "e2e/chat-profile-color.mjs",
                urls[0],
                session,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert result.returncode == 0, (result.stderr + "\n" + result.stdout).replace(token, "<redacted-session>")
        assert json.loads(result.stdout.strip().splitlines()[-1])["completed"] is True
        snapshot = client.get("/api/heinzel/session", headers=AUTH, params={"session_id": session}).json()
        assert snapshot["appearance"] == look
        assert snapshot["name"] == "Original"
        assert snapshot["profile_revision"] == 2
    finally:
        if RecordedServer.instances:
            RecordedServer.instances[0].should_exit = True
        server.join(15)
        assert not server.is_alive()
