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


def fake_claude(
    directory: Path, *, pause: bool = False, tool: str = "text.word_count", arguments: dict | None = None
) -> Path:
    path = directory / "fake-claude"
    marker = directory / "model-calls"
    arguments = arguments if arguments is not None else {"text": "private"}
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
open({str(directory / "prompt-input")!r},'w').write(json.dumps(payload))
if len(payload['messages']) == 1:
 wire={{'kind':'tool','name':'use_tool','arguments':{{'name':{tool!r},'arguments':{arguments!r}}},'text':None}}
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
    assert HeinzelSettings().subscription_budget_mode == "plan-unknown"
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


def test_explicit_usd_cap_refuses_before_start_or_model_call(tmp_path):
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


def test_full_current_message_is_retained(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        crud = SessionCrud(
            PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2)
        )
        session = crud.new_chat("new").session_id
        text = "long-current-" + "z" * 7000
        crud.accept(session, MessageRequest(request_id="long", text=text))
        executable = fake_claude(tmp_path)
        service = UserChatService(crud, current, load=lambda: FixtureConfig(executable))
        answer = asyncio.run(service.answer(session, "long"))
        assert answer.message.text == "A real recorded reply"
        payload = (tmp_path / "prompt-input").read_text()
        assert text in payload


def test_earlier_started_uncertain_turn_blocks_later_without_launch(tmp_path):
    from heinzel.modules.heinzel._interactive_turn import _turn_id

    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        with crud.repository.control_writer(session) as writer:
            turn = _turn_id("hello")
            writer.append("interactive.started", key=turn, turn=turn, data={"message_id": "hello"})
        crud.accept(session, MessageRequest(request_id="later", text="Hello again"))
        executable = fake_claude(tmp_path)
        service = UserChatService(crud, current, load=lambda: FixtureConfig(executable))
        with pytest.raises(HeinzelError, match="uncertain"):
            asyncio.run(service.answer(session, "later"))
        assert not (tmp_path / "model-calls").exists()


def test_fifo_executable_is_refused_without_blocking(tmp_path):
    import os

    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(HeinzelError, match="executable"):
        UserChatService._executable(str(fifo))


@pytest.mark.parametrize("status", ["pending", "done"])
def test_existing_legacy_personal_action_does_not_block_new_generic_turn(tmp_path, status):
    from datetime import UTC, datetime

    from heinzel import Settings
    from heinzel.modules.system.runner.chat._models import ChatAction, ChatContext, ChatMessage, ChatSettings
    from heinzel.modules.system.runner.chat._store import ChatStore

    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        store = ChatStore(Settings(state_dir=tmp_path / "legacy"), ChatSettings(), RunnerSettings(), current)
        chat = store.create(ChatContext(kind="text", ref=session), personal_session_id=session)
        chat.messages.append(ChatMessage(role="user", text="Earlier legacy message", at=datetime.now(UTC)))
        chat.actions.append(
            ChatAction(
                id="a1",
                tool="heinzel_schedule_control",
                module="heinzel",
                operation="schedule-control",
                command="review",
                status=status,
                proposed=datetime.now(UTC),
                arguments={"session_id": session, "action": "activate"},
            )
        )
        store.save(chat)
        executable = fake_claude(tmp_path)
        service = UserChatService(crud, current, load=lambda: FixtureConfig(executable))
        answer = asyncio.run(service.answer(session, "hello"))
        assert answer.message.text == "A real recorded reply"
        assert store.load(chat.id).actions[0].status == status


def test_fake_model_proposes_contract_in_owner_private_journal(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        executable = fake_claude(
            tmp_path,
            tool="agent:propose",
            arguments={
                "expected_revision": 0,
                "contract": "Review a document",
                "max_usd": 0.2,
                "timeout_seconds": 30,
            },
        )
        service = UserChatService(crud, current, load=lambda: FixtureConfig(executable))
        answer = asyncio.run(service.answer(session, "hello"))
        assert answer.message.text == "A real recorded reply"
        proposal = crud.latest_scheduled_agent_proposal(session)
        assert proposal.contract == "Review a document"
        assert proposal.status == "proposed"
        assert crud.snapshot(session).schedule is None
        assert [(item.tool, item.status) for item in service.activity(session).items] == [
            ("agent:propose", "requested"),
            ("agent:propose", "completed"),
        ]


def test_executable_size_boundary_accepts_installed_cli_without_large_fixture():
    from stat import S_IFIFO, S_IFREG
    from types import SimpleNamespace

    from heinzel.modules.heinzel._claude_subscription_model import _allowed_executable_stat

    assert _allowed_executable_stat(SimpleNamespace(st_mode=S_IFREG, st_size=275_988_512))
    assert _allowed_executable_stat(SimpleNamespace(st_mode=S_IFREG, st_size=512 * 1024 * 1024))
    assert not _allowed_executable_stat(SimpleNamespace(st_mode=S_IFREG, st_size=512 * 1024 * 1024 + 1))
    assert not _allowed_executable_stat(SimpleNamespace(st_mode=S_IFIFO, st_size=1))


def test_subscription_subprocess_environment_preserves_identity_but_not_ambient_secrets(monkeypatch, tmp_path):
    from heinzel.modules.heinzel._claude_subscription_model import subscription_environment

    monkeypatch.setenv("USER", "fixture-user")
    monkeypatch.setenv("LOGNAME", "fixture-login")
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "private-fixture-key")
    monkeypatch.setenv("HTTPS_PROXY", "https://private.invalid")
    env = subscription_environment()
    assert env["USER"] == "fixture-user"
    assert env["LOGNAME"] == "fixture-login"
    assert env["SHELL"] == "/bin/sh"
    assert env["XDG_CONFIG_HOME"] == str(tmp_path)
    assert "ANTHROPIC_API_KEY" not in env
    assert "HTTPS_PROXY" not in env
    assert "private-fixture-key" not in repr(env)


def test_auth_and_model_processes_receive_same_restricted_identity_environment(tmp_path, monkeypatch):
    from heinzel.modules.system.runner._process import run_bounded

    monkeypatch.setenv("USER", "fixture-user")
    monkeypatch.setenv("LOGNAME", "fixture-login")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "private-fixture-key")
    monkeypatch.setenv("HTTPS_PROXY", "https://private.invalid")
    observed = []

    def execute(command, **kwargs):
        observed.append(kwargs["env"].copy())
        return run_bounded(command, **kwargs)

    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        executable = fake_claude(tmp_path)
        service = UserChatService(crud, current, load=lambda: FixtureConfig(executable), execute=execute)
        assert asyncio.run(service.answer(session, "hello")).message.text == "A real recorded reply"
    assert len(observed) >= 3
    assert all(env == observed[0] for env in observed)
    assert observed[0]["USER"] == "fixture-user"
    assert observed[0]["LOGNAME"] == "fixture-login"
    assert "ANTHROPIC_API_KEY" not in observed[0]
    assert "HTTPS_PROXY" not in observed[0]
