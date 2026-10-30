"""A mounted mobile fake-provider journey for explicit whole-set human approval."""

import json
import subprocess
import sys
import threading
from typing import ClassVar
from urllib.parse import parse_qs, urlsplit

import pytest
import uvicorn

from heinzel.heads import web
from heinzel.modules.heinzel._connected_grants import ConnectedReadGrants
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._user_chat import UserChatService
from heinzel.modules.system.runner._snapshot import RunnerSnapshotService
from tests.e2e.durable_five_minute_journey_test import BROWSER_CACHE, REPO, web_build
from tests.unit.modules.user_chat_test import FixtureConfig, fake_claude

pytestmark = pytest.mark.e2e
pytest_plugins = ("tests.integration.source_discovery_prototype_test",)


@pytest.mark.timeout(600)
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_mobile_read_set_suggestion_review_approval_and_revocation(bound_source, tmp_path, monkeypatch, engine):
    web_build.ensure_built()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(BROWSER_CACHE))
    _client, kernel, current, session, plugin, _source, _sources = bound_source
    selections = [
        {"capability": "calendar", "method": "events", "fixed_arguments": {"window": {"since": start, "until": end}}}
        for start, end in (
            ("2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"),
            ("2026-01-02T00:00:00Z", "2026-01-03T00:00:00Z"),
        )
    ]
    executable = fake_claude(
        tmp_path, tool="source-set:propose", arguments={"expected_revision": 0, "entries": selections}
    )
    kernel.bind(
        UserChatService,
        UserChatService(
            kernel.resolve(SessionCrud),
            current,
            RunnerSnapshotService(),
            connected=lambda: kernel.resolve(ConnectedReadGrants),
            load=lambda: FixtureConfig(executable),
        ),
    )

    class RecordedServer(uvicorn.Server):
        instances: ClassVar[list[uvicorn.Server]] = []

        def __init__(self, config):
            super().__init__(config)
            self.instances.append(self)

    monkeypatch.setattr(uvicorn, "Server", RecordedServer)
    ready = threading.Event()
    urls = []

    def announce(value):
        urls.append(value)
        ready.set()

    thread = threading.Thread(target=lambda: web.serve(kernel, port=0, browser=False, announce=announce), daemon=True)
    thread.start()
    try:
        assert ready.wait(15)
        token = parse_qs(urlsplit(urls[0]).fragment).get("token", [""])[0]
        assert token
        run = subprocess.run(
            [
                sys.executable,
                str(REPO / "scripts/web_build.py"),
                "exec",
                "node",
                "e2e/read-set-approval.mjs",
                urls[0],
                engine,
                session,
            ],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
        if run.returncode:
            pytest.fail((run.stderr + "\n" + run.stdout).replace(token, "<redacted-session>"))
        assert json.loads(run.stdout.strip().splitlines()[-1])["accepted"] is True
        assert plugin.reads == []
    finally:
        if RecordedServer.instances:
            RecordedServer.instances[0].should_exit = True
        thread.join(15)
        assert not thread.is_alive()
