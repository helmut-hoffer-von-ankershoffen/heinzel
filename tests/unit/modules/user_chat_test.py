"""Selected Claude chat uses a fake executable and private temporary state only."""

import asyncio
import sys
from pathlib import Path

import pytest

from heinzel import CallIdentity, CurrentUser, HeinzelError, Principal
from heinzel.kernel._bindings import CapabilitySettings
from heinzel.modules.heinzel._models import HeinzelSettings
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import MessageRequest, SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository
from heinzel.modules.heinzel._user_chat import UserChatService
from heinzel.modules.system.runner._models import RunnerSettings

pytestmark = pytest.mark.unit


def fake_claude(directory: Path, *, pause: bool = False) -> Path:
    path = directory / "fake-claude"
    marker = directory / "model-calls"
    path.write_text(
        f"""#!{sys.executable}
import json,sys,time
if sys.argv[1:] == ['auth','status','--json']:
 print(json.dumps({{'loggedIn':True,'authMethod':'claude.ai','apiProvider':'firstParty'}})); raise SystemExit
if sys.argv[1:] == ['--version']:
 print('2.1.312 (Claude Code)'); raise SystemExit
assert '--safe-mode' in sys.argv and '--restricted' in sys.argv and '--strict-mcp-config' in sys.argv
assert sys.argv[sys.argv.index('--tools')+1] == '' and sys.argv[sys.argv.index('--allowed-tools')+1] == ''
open({str(marker)!r},'a').write('x')
if {pause!r}: time.sleep(10)
payload=json.load(sys.stdin)
if len(payload['messages']) == 1:
 wire={{'kind':'tool','name':'use_tool','arguments':{{'name':'text.word_count','arguments':{{'text':'private'}}}},'text':None}}
else:
 name=next(t['name'] for t in payload['tools'] if t['name'] != 'use_tool')
 wire={{'kind':'tool','name':name,'arguments':{{'report':'A real recorded reply'}},'text':None}}
print(json.dumps({{'type':'result','subtype':'success','is_error':False,
 'permission_denials':[], 'structured_output':wire}}))
""",
        encoding="utf-8",
    )
    path.chmod(0o700)
    return path


class FixtureConfig:
    def __init__(self, executable: Path, *, mode: str = "plan-unknown") -> None:
        self.executable, self.mode = executable, mode

    def settings(self, cls, _section):
        if cls is CapabilitySettings:
            return CapabilitySettings(ai_access="claude-code", agent_runner="claude-code")
        if cls is RunnerSettings:
            return RunnerSettings(claude_executable=str(self.executable))
        if cls is HeinzelSettings:
            return HeinzelSettings(subscription_budget_mode=self.mode)
        msg = "Unexpected settings"
        raise AssertionError(msg)


def setup_chat(current):
    repo = PersonalSessionRepository(current)
    crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
    session = crud.new_chat("new").session_id
    crud.accept(session, MessageRequest(request_id="hello", text="Hello"))
    return current, crud, session


def test_fake_cli_reply_tool_denial_unknown_usage_and_restart(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        executable = fake_claude(tmp_path)
        service = UserChatService(crud, current, load=lambda: FixtureConfig(executable))
        answer = asyncio.run(service.answer(session, "hello"))
        assert answer.session_id == session
        assert answer.request_id == "hello"
        assert answer.message.text == "A real recorded reply"
        assert answer.message.billing == "plan"
        assert answer.message.cost_usd is None
        assert answer.message.backend_binding.mode == "cli-model"
        assert crud.snapshot(session).costs.unknown_turns == 1
        assert crud.snapshot(session).schedule is None
        activity = service.activity(session).items
        assert [(item.tool, item.status) for item in activity] == [("unavailable", "denied")]
        marker = tmp_path / "model-calls"
        assert marker.read_text() == "xx"
        reopened = UserChatService(
            SessionCrud(PersonalSessionRepository(current), crud.costs), current, load=lambda: FixtureConfig(executable)
        )
        assert asyncio.run(reopened.answer(session, "hello")) == answer
        assert marker.read_text() == "xx"
        stranger = CallIdentity(
            Principal("u-" + "a" * 32), "api-bearer", "other", "private", frozenset({"private:write"})
        )
        with current.using(stranger), pytest.raises(HeinzelError):
            asyncio.run(service.answer(session, "hello"))


def test_default_usd_cap_refuses_before_start_or_model_call(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        executable = fake_claude(tmp_path)
        service = UserChatService(crud, current, load=lambda: FixtureConfig(executable, mode="usd-cap"))
        with pytest.raises(HeinzelError, match="cannot enforce"):
            asyncio.run(service.answer(session, "hello"))
        assert not (tmp_path / "model-calls").exists()
        assert not any(event.kind == "interactive.started" for event in crud.repository.control_read(session).events)


def test_cancelled_cli_keeps_started_turn_and_will_not_relaunch(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        executable = fake_claude(tmp_path, pause=True)
        service = UserChatService(crud, current, load=lambda: FixtureConfig(executable))
        marker = tmp_path / "model-calls"

        async def run():
            task = asyncio.create_task(service.answer(session, "hello"))
            for _ in range(300):
                if marker.exists():
                    break
                await asyncio.sleep(0.01)
            assert marker.exists()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(run())
        with pytest.raises(HeinzelError, match="uncertain"):
            asyncio.run(service.answer(session, "hello"))
        assert marker.read_text() == "x"
        assert crud.snapshot(session).costs.unknown_turns == 1
