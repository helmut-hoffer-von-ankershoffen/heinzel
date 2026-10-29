"""Mounted generic agent conversation using the real private head and a fake local CLI."""

import json
import subprocess
import sys
import threading
from typing import ClassVar
from urllib.parse import parse_qs, urlsplit

import pytest
import uvicorn

from heinzel import CurrentUser
from heinzel.heads import web
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._user_chat import UserChatService
from tests.e2e.durable_five_minute_journey_test import BROWSER_CACHE, REPO, web_build
from tests.unit.modules.user_chat_test import FixtureConfig

pytestmark = pytest.mark.e2e


@pytest.mark.timeout(600)
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_mounted_generic_agent_chat(make_kernel, tmp_path, monkeypatch, engine):
    web_build.ensure_built()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(BROWSER_CACHE))
    (tmp_path / "home").mkdir()
    executable = tmp_path / "fake-claude"
    marker = tmp_path / "calls"
    executable.write_text(
        f"""#!{sys.executable}
import json,sys
if sys.argv[1:] == ['auth','status','--json']:
 print(json.dumps({{'loggedIn':True,'authMethod':'claude.ai','apiProvider':'firstParty'}})); raise SystemExit
if sys.argv[1:] == ['--version']:
 print('2.1.312 (Claude Code)'); raise SystemExit
assert '--safe-mode' in sys.argv and '--restricted' in sys.argv and '--strict-mcp-config' in sys.argv
assert sys.argv[sys.argv.index('--tools')+1] == '' and sys.argv[sys.argv.index('--allowed-tools')+1] == ''
payload=json.load(sys.stdin)
try: n=len(open({str(marker)!r}).read())
except FileNotFoundError: n=0
open({str(marker)!r},'a').write('x')
if n == 0:
 name='agent:propose'
 args={{'expected_revision':0,'contract':'Prepare a private draft from my context.','timeout_seconds':30}}
elif n == 1:
 name='profile:propose'
 args={{'expected_revision':1,'name':'Mira','appearance':{{'age':'grown','build':'round'}}}}
else:
 name=next(t['name'] for t in payload['tools'] if t['name'] != 'use_tool')
 args={{'report':'I prepared proposals for your review.'}}
wire={{'kind':'tool','name':'use_tool' if n < 2 else name,
 'arguments':{{'name':name,'arguments':args}} if n < 2 else args,'text':None}}
print(json.dumps({{'type':'result','subtype':'success','is_error':False,'permission_denials':[],'structured_output':wire}}))
""",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    from heinzel import Settings

    settings = Settings(
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "logs",
    )
    kernel = make_kernel(builtins=True, settings_=settings)
    current = CurrentUser(tmp_path / "state" / "identity")
    kernel.bind(CurrentUser, current)
    crud = kernel.resolve(SessionCrud)
    kernel.bind(UserChatService, UserChatService(crud, current, load=lambda: FixtureConfig(executable)))

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
                "e2e/generic-agent-chat.mjs",
                urls[0],
                engine,
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
        assert marker.read_text() == "xxx"
    finally:
        if RecordedServer.instances:
            RecordedServer.instances[0].should_exit = True
        thread.join(15)
        assert not thread.is_alive()
