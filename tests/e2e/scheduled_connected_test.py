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
from tests.e2e.durable_five_minute_journey_test import BROWSER_CACHE, REPO, web_build

pytestmark = pytest.mark.e2e


def _setup_kernel(make_kernel, tmp_path, monkeypatch):
    from heinzel import Settings

    settings = Settings(
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "logs",
    )
    from heinzel import Config, ConnectedReadLease, ConnectedReadReview, ConnectedReadService, Module
    from heinzel.kernel._approval import arguments_hash
    from heinzel.modules.heinzel._capability_review import describe_capability_read

    holder = {}
    reads = []

    class Lease(ConnectedReadLease):
        def __init__(self, session, operation, fixed):
            self.session, self.operation, self.fixed = session, operation, fixed

        def review(self):
            descriptor = describe_capability_read(holder["kernel"], "calendar", "events")
            return ConnectedReadReview(
                owner=holder["current"].owner().id,
                session=self.session,
                operation=self.operation,
                schema_sha256=arguments_hash(descriptor.schema),
                source_revision="fixture-source",
                account_identity="fixture-account",
                provider_scopes=("fixture-read",),
                fixed_arguments=self.fixed,
            )

        def read(self, arguments):
            reads.append({"arguments": arguments, "fixed": self.fixed})
            return {"items": ["fictional result"]}

        def close(self):
            pass

    class Provider(ConnectedReadService):
        def open_lease(self, session, operation, fixed_arguments):
            return Lease(session, operation, dict(fixed_arguments))

    class Calendar:
        def events(self, window):
            return []

        def free_busy(self, people, window):
            return []

    fixture = Module("fixture", "Offline fixture")
    fixture.service(Provider)
    fixture.provides("calendar", modes={"read"}, name="fixture", hosts=["fixture.invalid"])(Calendar)
    config = Config(files=(), data={"capabilities": {"calendar": "fixture"}})
    monkeypatch.setattr(Config, "load", classmethod(lambda _cls, _cwd=None: config))
    kernel = make_kernel(builtins=True, settings_=settings, config=config, fixture=fixture)
    holder["kernel"] = kernel
    current = CurrentUser(tmp_path / "state" / "identity")
    kernel.bind(CurrentUser, current)
    holder["current"] = current
    from heinzel.kernel import ConfiguredApiAccess, RunnerSnapshotProvider

    # Production services and their registry provenance resolve normally; only the executable is fake.
    kernel.resolve_builtin_service(RunnerSnapshotProvider, "system.runner")
    kernel.resolve_builtin_service(ConfiguredApiAccess, "ai.access")

    return kernel, reads


def _assert_read(reads, rolling):
    assert len(reads) == 1
    assert reads[0]["arguments"] == {}
    if rolling:
        from datetime import UTC, datetime

        window = reads[0]["fixed"]["window"]
        since = datetime.fromisoformat(window["since"])
        until = datetime.fromisoformat(window["until"])
        assert (until - since).total_seconds() == 660
        assert abs((datetime.now(UTC) - since).total_seconds() - 60) < 60
    else:
        assert reads[0]["fixed"]["window"]["since"] == "2026-01-01T00:00:00Z"


@pytest.mark.timeout(600)
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("rolling", [False, True])
def test_mounted_scheduled_connected_read(make_kernel, tmp_path, monkeypatch, engine, rolling):
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
 name='agent:propose' if n == 0 else 'connected_read'
 args={{'expected_revision':0,'contract':'Inspect the chosen source.','timeout_seconds':30}} if n == 0 else {{}}
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

    probe = start_daemon_probe(kernel, tmp_path)
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
                "e2e/scheduled-connected-read.mjs",
                urls[0],
                engine,
                str(tmp_path),
                "rolling" if rolling else "fixed",
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
        assert marker.read_text() == "xxxx"
        _assert_read(reads, rolling)
        assert probe["failure"] is None
        assert probe["deduplicated"] is True
    finally:
        if RecordedServer.instances:
            RecordedServer.instances[0].should_exit = True
        thread.join(15)
        probe["thread"].join(15)
        assert not thread.is_alive()
