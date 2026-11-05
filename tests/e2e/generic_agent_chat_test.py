"""Mounted generic agent conversation using the real private head and a fake local CLI."""

import json
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import ClassVar
from urllib.parse import parse_qs, urlsplit

import pytest
import uvicorn

from heinzel import CurrentUser
from heinzel.heads import web
from tests.e2e.durable_five_minute_journey_test import BROWSER_CACHE, REPO, web_build

pytestmark = pytest.mark.e2e


@dataclass(frozen=True)
class ChatJourney:
    engine: str
    late_page: bool
    activity_scroll: bool
    fractional_scroll: bool = False
    partial_scroll: bool = False


def _mounted_generic_agent_chat(make_kernel, tmp_path, monkeypatch, journey: ChatJourney):
    engine, late_page, activity_scroll = journey.engine, journey.late_page, journey.activity_scroll
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
if n == 3:
 print(json.dumps({{'type':'result','subtype':'success','is_error':True,'api_error_status':429,
  'api_error':'usage_limit_reached','result':'private provider details'}})); raise SystemExit
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
    monkeypatch.setenv("HZ_SYSTEM_RUNNER__CLAUDE_EXECUTABLE", str(executable))
    monkeypatch.setenv("HZ_CAPABILITIES__AI_ACCESS", "claude-code")
    monkeypatch.setenv("HZ_CAPABILITIES__AGENT_RUNNER", "claude-code")
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
    from heinzel.kernel import ConfiguredApiAccess, RunnerSnapshotProvider

    # Production services and their registry provenance resolve normally; only the executable is fake.
    kernel.resolve_builtin_service(RunnerSnapshotProvider, "system.runner")
    kernel.resolve_builtin_service(ConfiguredApiAccess, "ai.access")

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
                "late-page" if late_page else "normal",
                str(tmp_path),
                "partial"
                if journey.partial_scroll
                else "fractional"
                if journey.fractional_scroll
                else "smooth"
                if activity_scroll
                else "normal",
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
        assert marker.read_text() == "xxxxx"
    finally:
        if RecordedServer.instances:
            RecordedServer.instances[0].should_exit = True
        thread.join(15)
        assert not thread.is_alive()


@pytest.mark.timeout(600)
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("late_page", [False, True])
def test_mounted_generic_agent_chat(make_kernel, tmp_path, monkeypatch, engine, late_page):
    _mounted_generic_agent_chat(
        make_kernel, tmp_path, monkeypatch, ChatJourney(engine=engine, late_page=late_page, activity_scroll=False)
    )


@pytest.mark.timeout(600)
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_reply_disclosure_remains_clickable_during_workspace_scroll(make_kernel, tmp_path, monkeypatch, engine):
    """A real physical press stops an in-flight workspace scroll before its release can hit a different node."""
    _mounted_generic_agent_chat(
        make_kernel, tmp_path, monkeypatch, ChatJourney(engine=engine, late_page=True, activity_scroll=True)
    )


@pytest.mark.timeout(600)
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_fractional_reply_disclosure_remains_clickable_during_workspace_scroll(
    make_kernel, tmp_path, monkeypatch, engine
):
    """A quarter-pixel layout fringe must not leave a native reveal moving under a genuine held press."""
    _mounted_generic_agent_chat(
        make_kernel,
        tmp_path,
        monkeypatch,
        ChatJourney(engine=engine, late_page=True, activity_scroll=True, fractional_scroll=True),
    )


@pytest.mark.timeout(600)
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_partially_clipped_reply_disclosure_remains_clickable_during_workspace_scroll(
    make_kernel, tmp_path, monkeypatch, engine
):
    """A trusted native summary press stays clickable with five pixels beyond the workspace edge."""
    _mounted_generic_agent_chat(
        make_kernel,
        tmp_path,
        monkeypatch,
        ChatJourney(engine=engine, late_page=True, activity_scroll=True, partial_scroll=True),
    )
