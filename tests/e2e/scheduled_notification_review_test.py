"""Mounted scheduled notification consent through real private heads and fake local effects."""

import json
import sys
import threading
from typing import ClassVar, override
from urllib.parse import parse_qs, urlsplit

import pytest
import uvicorn

from heinzel import Notifications
from heinzel.heads import web
from tests.e2e._browser_result import require_browser, run_browser
from tests.e2e.durable_five_minute_journey_test import BROWSER_CACHE, REPO, web_build
from tests.e2e.scheduled_connected_test import _setup_kernel

pytestmark = pytest.mark.e2e


class FakeQueue(Notifications):
    def __init__(self, *, queued):
        self.calls = []
        self.queued = queued

    @override
    def emit_once(self, _kind, title, body, *, route, command, key, helper=None):
        self.calls.append((title, body, key))
        return self.queued

    @override
    def recent(self, limit=50):
        """This fake exposes no stored notice records to the read-only stream."""
        return []

    @override
    def has_once(self, _key):
        return None

    @override
    def forget_completed(self, _keys):
        return True


@pytest.mark.timeout(600)
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("queued", [False, True])
def test_mounted_scheduled_notifications(make_kernel, tmp_path, monkeypatch, engine, queued):
    web_build.ensure_built()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(BROWSER_CACHE))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
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
if n in (0,2):
 name='agent:propose' if n == 0 else 'notification:send'
 args={{'expected_revision':0,'contract':'Inspect the chosen source.','timeout_seconds':30}} if n == 0 else {{
  'topic':'example','title':'Fictional title','body':'Fictional body'}}
 wire={{'kind':'tool','name':'use_tool','arguments':{{'name':name,'arguments':args}},'text':None}}
else:
 name=next(t['name'] for t in payload['tools'] if t['name'] != 'use_tool')
 wire={{'kind':'tool','name':name,'arguments':{{'report':'Read complete.','summary':'Read complete.'}},'text':None}}
print(json.dumps({{'type':'result','subtype':'success','is_error':False,'permission_denials':[],'structured_output':wire}}))
""",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    monkeypatch.setenv("HZ_SYSTEM_RUNNER__CLAUDE_EXECUTABLE", str(executable))
    monkeypatch.setenv("HZ_CAPABILITIES__AI_ACCESS", "claude-code")
    monkeypatch.setenv("HZ_CAPABILITIES__AGENT_RUNNER", "claude-code")
    kernel, reads = _setup_kernel(make_kernel, tmp_path, monkeypatch)
    provider = FakeQueue(queued=queued)
    kernel.bind(Notifications, provider)

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
    from tests.e2e.scheduled_connected_support import start_daemon_probe

    probe = start_daemon_probe(kernel, tmp_path, cadence_seconds=3600)
    try:
        assert ready.wait(15)
        token = parse_qs(urlsplit(urls[0]).fragment).get("token", [""])[0]
        assert token
        run = require_browser(
            run_browser(
                [
                    sys.executable,
                    str(REPO / "scripts/web_build.py"),
                    "exec",
                    "node",
                    "e2e/scheduled-notification-review.mjs",
                    urls[0],
                    engine,
                    str(tmp_path),
                    "queued" if queued else "uncertain",
                ],
                cwd=REPO,
                capture_output=True,
                text=True,
                timeout=240,
            )
        )
        if run.returncode:
            pytest.fail((run.stderr + "\n" + run.stdout).replace(token, "<redacted-session>"))
        assert json.loads(run.stdout.strip().splitlines()[-1])["accepted"] is True
        assert marker.read_text() == "xxxx"
        assert reads == []
        assert len(provider.calls) == 1
        assert probe["failure"] is None
        assert probe["deduplicated"] is True
    finally:
        if RecordedServer.instances:
            RecordedServer.instances[0].should_exit = True
        thread.join(15)
        probe["thread"].join(15)
        assert not thread.is_alive()
