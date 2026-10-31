"""Mounted workshop to owner-private inert draft, using only a fake local CLI model."""

import hashlib
import json
import os
import sys
import threading
from pathlib import Path
from typing import ClassVar
from urllib.parse import parse_qs, urlsplit

import pytest
import uvicorn

from heinzel import ChatDraftTemplate, CurrentUser, Module, PlaybookLet
from heinzel.heads import web
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._user_chat import UserChatService
from tests.e2e._browser_result import check_browser, require_browser, wait_browser
from tests.e2e.docs_test import GuardedCommand
from tests.e2e.durable_five_minute_journey_test import BROWSER_CACHE, web_build
from tests.unit.modules.user_chat_test import FixtureConfig

pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parents[2]


def _assert_inert_workshop(kernel, result, digest):
    session = result["session"]
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    with current.local("cli"):
        proposal = crud.latest_scheduled_agent_proposal(session)
        assert proposal is not None
        assert proposal.status == "proposed"
        assert proposal.permitted_tools == []
        assert digest in proposal.contract
        snapshot = crud.snapshot(session)
        assert snapshot.schedule is None
        assert [message.text for message in snapshot.messages if message.role == "user"] == [
            "Help me keep a private summary of my notes current",
            "Use the example information and prepare a private summary",
        ]
        events = crud.repository.control_read(session).events
        assert not any("approved" in event.kind or "activated" in event.kind for event in events)
        assert not any("observed" in event.kind or "notification" in event.kind for event in events)


@pytest.mark.timeout(600)
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_mounted_workshop_discovers_chat_draft_without_grant(make_kernel, tmp_path, monkeypatch, engine):
    web_build.ensure_built()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(BROWSER_CACHE))
    source = "Ask what information and output the person wants before proposing an inert draft."
    digest = hashlib.sha256(source.encode()).hexdigest()
    module = Module("fixture", "Fixture")
    module.add(
        PlaybookLet(name="fixture", summary="Fixture", chat_drafts=(ChatDraftTemplate(name="draft", text=source),))
    )
    kernel = make_kernel(builtins=True, fixture=module)
    service = kernel.resolve(UserChatService)
    executable = tmp_path / "fake-model"
    calls = tmp_path / "calls"
    assert not executable.exists()
    assert not executable.is_symlink()
    executable.write_text(f"""#!{sys.executable}
import json,sys
from pathlib import Path
if sys.argv[1:] == ['auth','status','--json']:
 print(json.dumps({{'loggedIn':True,'authMethod':'claude.ai','apiProvider':'firstParty'}})); raise SystemExit
if sys.argv[1:] == ['--version']:
 print('2.1.312 (Claude Code)'); raise SystemExit
marker=Path({str(calls)!r})
n=len(marker.read_text()) if marker.exists() else 0
marker.write_text('x'*(n+1))
payload=json.load(sys.stdin)
if n == 0:
 assert 'Help me keep a private summary of my notes current' in json.dumps(payload['messages'])
 wire={{'kind':'tool','name':'use_tool','arguments':{{'name':'playbook:list','arguments':{{}}}},'text':None}}
elif n in (1,3):
 if n == 1: assert 'fixture:draft' in json.dumps(payload['messages'])
 wire={{'kind':'tool','name':'use_tool','arguments':{{'name':'playbook:preview','arguments':{{'template':'fixture:draft'}}}},'text':None}}
elif n == 4:
 assert {digest!r} in json.dumps(payload['messages'])
 args={{'template':'fixture:draft','sha256':{digest!r},'expected_revision':0,'timeout_seconds':30,
        'contract':'Use the example information and prepare a private summary.'}}
 wire={{'kind':'tool','name':'use_tool','arguments':{{'name':'playbook:propose','arguments':args}},'text':None}}
else:
 name=next(t['name'] for t in payload['tools'] if t['name'] != 'use_tool')
 report='Which information and output should I use?' if n == 2 else 'Draft ready for review'
 wire={{'kind':'tool','name':name,'arguments':{{'report':report}},'text':None}}
print(json.dumps({{'type':'result','subtype':'success','is_error':False,
                  'permission_denials':[], 'structured_output':wire}}))
""")
    assert executable.resolve().parent == tmp_path.resolve()
    assert not executable.samefile(sys.executable)
    assert not executable.samefile(sys._base_executable)
    executable.chmod(0o700)
    monkeypatch.setattr(service, "_load", lambda: FixtureConfig(executable))

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
            "e2e/chat-only-workshop.mjs",
            urls[0],
            engine,
        ]
        guarded = GuardedCommand(command, cwd=ROOT, env=os.environ.copy())
        try:
            returncode = require_browser(wait_browser(guarded, 180))
        finally:
            guarded.stop()
        stdout, stderr = guarded.capture.text("stdout"), guarded.capture.text("stderr")
        check_browser(returncode, (stderr + "\n" + stdout).replace(token, "<redacted-session>"))
        result = json.loads(stdout.strip().splitlines()[-1])
        assert result["completed"] is True
        _assert_inert_workshop(kernel, result, digest)
    finally:
        if RecordedServer.instances:
            RecordedServer.instances[0].should_exit = True
        thread.join(15)
        assert not thread.is_alive()
