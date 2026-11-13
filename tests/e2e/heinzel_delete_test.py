"""Delete a retired Heinzel for good through the typed-name dialog: phone and desktop, Chromium and WebKit."""

import json
import os
import sys
import threading
from pathlib import Path
from typing import ClassVar
from urllib.parse import parse_qs, urlsplit

import pytest
import uvicorn

from heinzel import CurrentUser
from heinzel.heads import web
from heinzel.modules.heinzel._session_crud import SessionCrud
from tests.e2e._browser_result import check_browser, require_browser, wait_browser
from tests.e2e.docs_test import GuardedCommand
from tests.e2e.durable_five_minute_journey_test import BROWSER_CACHE, web_build

pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.timeout(600)
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_mounted_delete_for_good_from_the_retired_list(make_kernel, monkeypatch, engine):
    web_build.ensure_built()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(BROWSER_CACHE))
    kernel = make_kernel(builtins=True)
    crud = kernel.resolve(SessionCrud)
    with kernel.resolve(CurrentUser).local("cli"):
        kept = crud.new_chat("create-kept", "Keeper").session_id
        retired = {}
        for name in ("Pocket", "Desk"):
            session = crud.new_chat(f"create-{name}", name, initial_message=f"Hello {name}").session_id
            crud.retire(session, f"retire-{name}")
            retired[name] = session

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
        command = [
            sys.executable,
            str(ROOT / "scripts/web_build.py"),
            "exec",
            "node",
            "e2e/heinzel-delete.mjs",
            urls[0],
            engine,
            "Pocket",
            "Desk",
        ]
        guarded = GuardedCommand(command, cwd=ROOT, env=os.environ.copy())
        try:
            returncode = require_browser(wait_browser(guarded, 180))
        finally:
            guarded.stop()
        stdout, stderr = guarded.capture.text("stdout"), guarded.capture.text("stderr")
        check_browser(returncode, (stderr + "\n" + stdout).replace(token, "<redacted-session>"))
        assert json.loads(stdout.strip().splitlines()[-1]) == {"completed": True}
    finally:
        if RecordedServer.instances:
            RecordedServer.instances[0].should_exit = True
        thread.join(15)
    with kernel.resolve(CurrentUser).local("cli"):
        assert [item.session_id for item in crud.listing().sessions] == [kept]
        assert {crud.repository.deletion_state(session) for session in retired.values()} == {"deleted"}
