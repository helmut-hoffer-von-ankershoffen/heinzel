"""Mounted generic agent conversation using the real private head and a fake local CLI."""

import json
import subprocess
import sys
import threading
from typing import ClassVar
from urllib.parse import parse_qs, urlsplit

import pytest
import uvicorn

from heinzel import ConnectedReadMethod, CurrentUser
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
    opens = []

    class Lease(ConnectedReadLease):
        def __init__(self, session, operation, fixed):
            self.session, self.operation, self.fixed = session, operation, fixed

        def review(self):
            descriptor = describe_capability_read(holder["kernel"], "documents", "read")
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
            reads.append(arguments)
            return {"items": ["fictional result"]}

        def close(self):
            pass

    class Provider(ConnectedReadService):
        def supported_reads(self):
            return (ConnectedReadMethod(capability="documents", method="read"),)

        def open_lease(self, session, operation, fixed_arguments):
            opens.append((session, operation, dict(fixed_arguments)))
            return Lease(session, operation, dict(fixed_arguments))

    class Documents:
        def read(self, ref):
            return {}

    fixture = Module("fixture", "Offline fixture")
    fixture.service(Provider)
    fixture.provides("documents", modes={"read"}, name="fixture", hosts=["fixture.invalid"])(Documents)
    config = Config(files=(), data={"capabilities": {"documents": "fixture"}})
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

    return kernel, reads, opens


@pytest.mark.timeout(600)
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_mounted_source_suggestion(make_kernel, tmp_path, monkeypatch, engine):
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
if n == 0:
 name='source:propose'
 wire={{'kind':'tool','name':'use_tool','arguments':{{'name':name,'arguments':{{'expected_revision':0,'capability':'documents','method':'read','fixed_arguments':{{'ref':'https://fixture.invalid/document'}}}}}},'text':None}}
elif n == 2:
 wire={{'kind':'tool','name':'use_tool','arguments':{{'name':'connected_read','arguments':{{}}}},'text':None}}
else:
 name=next(t['name'] for t in payload['tools'] if t['name'] != 'use_tool')
 summary='Source read finished.' if n == 3 else 'Suggestion recorded.'
 wire={{'kind':'tool','name':name,'arguments':{{'report':summary,'summary':summary}},'text':None}}
print(json.dumps({{'type':'result','subtype':'success','is_error':False,'permission_denials':[],'structured_output':wire}}))
""",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    monkeypatch.setenv("HZ_SYSTEM_RUNNER__CLAUDE_EXECUTABLE", str(executable))
    monkeypatch.setenv("HZ_CAPABILITIES__AI_ACCESS", "claude-code")
    monkeypatch.setenv("HZ_CAPABILITIES__AGENT_RUNNER", "claude-code")
    kernel, reads, opens = _setup_kernel(make_kernel, tmp_path, monkeypatch)

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
                "e2e/source-suggestion.mjs",
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
        assert marker.read_text() == "xxxx"
        assert reads == [{}]
        assert opens
        assert all(
            item[1:] == ("capability-method:documents.read", {"ref": "https://fixture.invalid/document"})
            for item in opens
        )
    finally:
        if RecordedServer.instances:
            RecordedServer.instances[0].should_exit = True
        thread.join(15)
        assert not thread.is_alive()
