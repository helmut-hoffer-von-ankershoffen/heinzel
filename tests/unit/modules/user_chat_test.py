"""Selected Claude chat uses a fake executable and private temporary state only."""

import asyncio
import sys
from pathlib import Path

import pytest

from heinzel import CallIdentity, CurrentUser, HeinzelError, Principal
from heinzel.kernel._bindings import CapabilitySettings
from heinzel.modules.heinzel._models import HeinzelSettings
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import QUESTION_ASKED, MessageRequest, SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository
from heinzel.modules.heinzel._turn_activity import interactive_turn_id
from heinzel.modules.heinzel._user_chat import UserChatService
from heinzel.modules.system.runner._models import RunnerSettings
from heinzel.modules.system.runner._snapshot import RunnerSnapshotService
from tests._fake_claude import CLAUDE_PRELUDE

pytestmark = pytest.mark.integration


def fake_claude(
    directory: Path,
    *,
    pause: bool = False,
    tool: str = "text.word_count",
    arguments: dict | None = None,
    final: dict | None = None,
) -> Path:
    path = directory / "fake-claude"
    marker = directory / "model-calls"
    arguments = arguments if arguments is not None else {"text": "private"}
    path.write_text(
        f"""#!{sys.executable}
import json,sys,time
{CLAUDE_PRELUDE}
assert '--safe-mode' in sys.argv and '--restricted' in sys.argv and '--strict-mcp-config' in sys.argv
assert sys.argv[sys.argv.index('--tools')+1] == '' and sys.argv[sys.argv.index('--allowed-tools')+1] == ''
open({str(marker)!r},'a').write('x')
if {pause!r}: time.sleep(10)
payload=json.load(sys.stdin)
open({str(directory / "prompt-input")!r},'w').write(json.dumps(payload))
if len(payload['messages']) == 1:
 wire={{'kind':'tool','name':'use_tool','arguments':{{'name':{tool!r},'arguments':{arguments!r}}},'text':None}}
else:
 name=[t['name'] for t in payload['tools'] if t['name'] != 'use_tool'][-1]
 wire={{'kind':'tool','name':name,'arguments':{final or {"report": "A real recorded reply"}!r},'text':None}}
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


def test_chat_refuses_unavailable_connections_and_unknown_provider(tmp_path):
    from types import SimpleNamespace

    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        _, crud, _session = setup_chat(current)
        config = FixtureConfig(tmp_path / "missing")
        service = UserChatService(crud, current, RunnerSnapshotService(), load=lambda: config)
        with pytest.raises(HeinzelError, match="Connected reads are unavailable"):
            service.connected_grants()
        assert service._plugin_held() is None
        with pytest.raises(HeinzelError, match="neither the configured AI access"):
            service._access(config, SimpleNamespace(provider="missing"))
        service._models = SimpleNamespace(offers=lambda name: name == "installed")
        assert service._access(config, SimpleNamespace(provider="installed")) == "installed"
        assert service._plugin_held() is None
        service.which = lambda _name: None
        with pytest.raises(HeinzelError, match="executable is unavailable"):
            service.binding_terms()


def test_fake_cli_reply_tool_denial_unknown_usage_and_restart(tmp_path):
    assert HeinzelSettings().subscription_budget_mode == "plan-unknown"
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        executable = fake_claude(tmp_path)
        service = UserChatService(crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable))
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
            SessionCrud(PersonalSessionRepository(current), crud.costs),
            current,
            RunnerSnapshotService(),
            load=lambda: FixtureConfig(executable),
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
        service = UserChatService(
            crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable, mode="usd-cap")
        )
        with pytest.raises(HeinzelError, match="cannot enforce"):
            asyncio.run(service.answer(session, "hello"))
        assert not (tmp_path / "model-calls").exists()
        assert not any(event.kind == "interactive.started" for event in crud.repository.control_read(session).events)


def test_cancelled_cli_keeps_started_turn_and_will_not_relaunch(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        executable = fake_claude(tmp_path, pause=True)
        service = UserChatService(crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable))
        marker = tmp_path / "model-calls"

        async def run():
            task = asyncio.create_task(service.answer(session, "hello"))
            # Wait for the model call's mark itself, not the file: the fake creates the file before it writes,
            # and a cancel in between kills it with the call unrecorded (seen on a hosted Python 3.12 runner).
            for _ in range(300):
                if marker.exists() and marker.read_text() == "x":
                    break
                await asyncio.sleep(0.01)
            assert marker.read_text() == "x"
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
        service = UserChatService(crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable))
        answer = asyncio.run(service.answer(session, "long"))
        assert answer.message.text == "A real recorded reply"
        payload = (tmp_path / "prompt-input").read_text()
        assert text in payload


def test_earlier_started_uncertain_turn_blocks_later_without_launch(tmp_path):
    from heinzel.modules.heinzel._interactive_turn import interactive_turn_id

    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        with crud.repository.control_writer(session) as writer:
            turn = interactive_turn_id("hello")
            writer.append("interactive.started", key=turn, turn=turn, data={"message_id": "hello"})
        crud.accept(session, MessageRequest(request_id="later", text="Hello again"))
        executable = fake_claude(tmp_path)
        service = UserChatService(crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable))
        with pytest.raises(HeinzelError, match="uncertain"):
            asyncio.run(service.answer(session, "later"))
        assert not (tmp_path / "model-calls").exists()


def test_queue_head_survives_paging_and_advances_without_replaying(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        _, crud, session = setup_chat(current)
        with crud.repository.control_writer(session) as writer:
            turn = interactive_turn_id("hello")
            writer.append("interactive.started", key=turn, turn=turn, data={"message_id": "hello"})
        for request in ("second", "third"):
            crud.accept(session, MessageRequest(request_id=request, text=request))
        executable = fake_claude(tmp_path)
        service = UserChatService(crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable))
        first_page = crud.snapshot(session, limit=1)
        later_page = crud.snapshot(session, cursor=first_page.next_cursor, limit=1)
        assert later_page.messages[0].id == "second"
        assert later_page.queue_head.id == "hello"
        with pytest.raises(HeinzelError, match="uncertain"):
            asyncio.run(service.answer(session, "third"))
        assert not (tmp_path / "model-calls").exists()
        service.abandon(session, "hello")
        assert crud.snapshot(session).queue_head.id == "second"
        with pytest.raises(HeinzelError, match="earlier accepted"):
            asyncio.run(service.answer(session, "third"))
        asyncio.run(service.answer(session, "second"))
        assert crud.snapshot(session).queue_head.id == "third"
        asyncio.run(service.answer(session, "third"))
        assert crud.snapshot(session).queue_head is None
        starts = [e for e in crud.repository.control_read(session).events if e.kind == "interactive.started"]
        assert sum(e.key == interactive_turn_id("hello") for e in starts) == 1


def test_fifo_executable_is_refused_without_blocking(tmp_path):
    import os

    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(HeinzelError, match="executable"):
        UserChatService.executable(str(fifo))


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
        service = UserChatService(crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable))
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
                "timeout_seconds": 30,
            },
        )
        service = UserChatService(crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable))
        answer = asyncio.run(service.answer(session, "hello"))
        assert answer.message.text == "A real recorded reply"
        proposal = crud.latest_scheduled_agent_proposal(session)
        assert proposal.contract == "Review a document"
        assert proposal.status == "proposed"
        assert proposal.max_usd is None
        assert crud.snapshot(session).schedule is None
        assert [(item.tool, item.status) for item in service.activity(session).items] == [
            ("agent:propose", "requested"),
            ("agent:propose", "completed"),
        ]


def test_executable_size_boundary_accepts_installed_cli_without_large_fixture():
    from stat import S_IFIFO, S_IFREG
    from types import SimpleNamespace

    from heinzel.kernel._claude_code import _allowed_executable_stat

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
        service = UserChatService(
            crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable), execute=execute
        )
        assert asyncio.run(service.answer(session, "hello")).message.text == "A real recorded reply"
    assert len(observed) >= 3
    assert all(env == observed[0] for env in observed)
    assert observed[0]["USER"] == "fixture-user"
    assert observed[0]["LOGNAME"] == "fixture-login"
    assert "ANTHROPIC_API_KEY" not in observed[0]
    assert "HTTPS_PROXY" not in observed[0]


def test_abandon_requires_owner_stopped_turn_and_never_changes_finished_reply(tmp_path):
    from heinzel import CallIdentity, Principal
    from heinzel.modules.heinzel._interactive_turn import interactive_turn_id

    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        executable = fake_claude(tmp_path)
        service = UserChatService(crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable))
        with pytest.raises(HeinzelError, match="stopped uncertain turn"):
            service.abandon(session, "hello")
        turn = interactive_turn_id("hello")
        with crud.repository.control_writer(session) as writer:
            writer.append("interactive.started", key=turn, turn=turn, data={"message_id": "hello"})
        stranger = CallIdentity(
            Principal("u-" + "a" * 32), "api-bearer", "other", "private", frozenset({"private:write"})
        )
        with current.using(stranger), pytest.raises(HeinzelError):
            service.abandon(session, "hello")
        with crud.repository.main_writer(session), pytest.raises(HeinzelError):
            service.abandon(session, "hello")
        assert service.abandon(session, "hello").status == "abandoned"
        assert service.abandon(session, "hello").status == "abandoned"


@pytest.fixture
def expired_chat(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        _, crud, session = setup_chat(current)
        accepted = next(e for e in crud.repository.control_read(session).events if e.key == "message-hello")
        path = (
            current.directory.parent
            / "heinzel-sessions"
            / current.principal().id
            / "control"
            / session
            / "turns"
            / f"{accepted.data['body_id']}.jsonl"
        )
        body = path.read_bytes()
        path.unlink()
        crud.accept(session, MessageRequest(request_id="later", text="A fresh message"))
        executable = fake_claude(tmp_path)
        service = UserChatService(crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable))
        yield current, crud, session, service, path, body


def test_expired_unstarted_human_queue_head_can_be_explicitly_set_aside(tmp_path, expired_chat):
    _, crud, session, service, _path, _body = expired_chat
    assert crud.snapshot(session).queue_head.status == "expired"
    assert service.turn_state(session, "hello").status == "pending"
    assert service.abandon(session, "hello", expected_status="pending").status == "abandoned"
    assert service.abandon(session, "hello", expected_status="pending").status == "abandoned"
    assert service.turn_state(session, "hello").status == "abandoned"
    assert crud.snapshot(session).queue_head.id == "later"
    assert not (tmp_path / "model-calls").exists()
    assert asyncio.run(service.answer(session, "later")).message.text == "A real recorded reply"


@pytest.mark.parametrize(
    "change", ["started", "restored", "foreign", "foreign-after-lock", "locked", "wrong-state", "wrong-request"]
)
def test_expired_input_cancellation_rechecks_owner_and_state_under_lock(expired_chat, tmp_path, monkeypatch, change):
    from contextlib import contextmanager

    current, crud, session, service, path, body = expired_chat
    original = crud.repository.main_writer
    assert service.turn_state(session, "hello").status == "pending"

    @contextmanager
    def changed_writer(session_id):
        with original(session_id) as writer:
            if change == "started":
                with crud.repository.control_writer(session_id) as control:
                    turn = interactive_turn_id("hello")
                    control.append("interactive.started", key=turn, turn=turn, data={"message_id": "hello"})
            elif change == "restored":
                path.write_bytes(body)
            if change == "foreign-after-lock":
                stranger = CallIdentity(
                    Principal("u-" + "a" * 32), "api-bearer", "other", "private", frozenset({"private:write"})
                )
                with current.using(stranger):
                    yield writer
            else:
                yield writer

    monkeypatch.setattr(crud.repository, "main_writer", changed_writer)
    if change == "foreign":
        stranger = CallIdentity(
            Principal("u-" + "a" * 32), "api-bearer", "other", "private", frozenset({"private:write"})
        )
        with current.using(stranger), pytest.raises(HeinzelError):
            service.abandon(session, "hello", expected_status="pending")
    elif change == "locked":
        with original(session), pytest.raises(HeinzelError):
            service.abandon(session, "hello", expected_status="pending")
    else:
        with pytest.raises(HeinzelError):
            service.abandon(
                session,
                "missing" if change == "wrong-request" else "hello",
                expected_status="unresolved" if change == "wrong-state" else "pending",
            )
    assert not any(e.kind == "interactive.abandoned" for e in crud.repository.control_read(session).events)
    assert not (tmp_path / "model-calls").exists()


def test_expired_cancellation_receipt_cannot_hide_a_started_turn(expired_chat, tmp_path):
    _, crud, session, service, _path, _body = expired_chat
    service.abandon(session, "hello", expected_status="pending")
    with crud.repository.control_writer(session) as writer:
        turn = interactive_turn_id("hello")
        writer.append("interactive.started", key=turn, turn=turn, data={"message_id": "hello"})
    with pytest.raises(HeinzelError, match="invalid resolution"):
        service.turn_state(session, "hello")
    assert crud.snapshot(session).queue_head.id == "hello"
    with pytest.raises(HeinzelError, match="invalid resolution"):
        asyncio.run(service.answer(session, "later"))
    assert not (tmp_path / "model-calls").exists()


def test_expired_cancellation_refuses_mismatched_accepted_request_identity(expired_chat, monkeypatch):
    _, crud, session, service, _path, _body = expired_chat
    read = crud.repository.control_read
    snapshot = read(session)
    corrupted = snapshot.model_copy(
        update={
            "events": [
                e.model_copy(update={"data": {**e.data, "message_id": "different"}}) if e.key == "message-hello" else e
                for e in snapshot.events
            ]
        }
    )
    monkeypatch.setattr(crud.repository, "control_read", lambda _session: corrupted)
    with pytest.raises(HeinzelError, match="authenticated user-chat origin"):
        service.abandon(session, "hello", expected_status="pending")
    assert not any(e.kind == "interactive.abandoned" for e in read(session).events)


def test_fake_model_suggests_inert_schedule_in_personal_turn(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        executable = fake_claude(
            tmp_path,
            tool="schedule:propose",
            arguments={
                "expected_revision": 0,
                "trigger": {"cron": "0 9 * * 1", "timezone": "UTC"},
                "suggested_reads": [],
            },
        )
        service = UserChatService(crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable))
        answer = asyncio.run(service.answer(session, "hello"))
        assert answer.message.text == "A real recorded reply"
        events = crud.repository.control_read(session).events
        suggestions = [event for event in events if event.kind == "schedule-suggestion.proposed"]
        assert len(suggestions) == 1
        assert crud.read_body(session, suggestions[0])["status"] == "suggested"
        assert crud.snapshot(session).schedule is None
        assert [(item.tool, item.status) for item in service.activity(session).items] == [
            ("schedule:propose", "requested"),
            ("schedule:propose", "completed"),
        ]


def test_the_turn_verifier_binds_the_observed_release(tmp_path):
    from heinzel.modules.heinzel._claude_subscription_model import COMMAND_FLAGS, VERSION, SubscriptionAdmission
    from heinzel.modules.heinzel._user_chat import _ClaudeVerifier

    policy = SubscriptionAdmission("u-" + "a" * 32, "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV", 1, 2, 5, 1024)
    path = tmp_path / "claude"

    def verifier():
        return _ClaudeVerifier(lambda: True, path, "d" * 64, policy)

    def admits(target, version):
        return target.verify(path, "d" * 64, version, COMMAND_FLAGS, policy)

    turn = verifier()
    assert admits(turn, VERSION)
    assert admits(turn, "2.1.321")
    assert admits(turn, "2.1.321")
    assert not admits(turn, "2.1.322")  # the release cannot change inside one turn
    for version in ("2.1.311", "2.2.0", "3.0.0"):
        assert admits(verifier(), version)
    for invalid in ("2.1.321 (Claude Code)", "latest"):
        assert not admits(verifier(), invalid)


class Questions:
    """Stands in for the chat relay (ADR 0160): records which turns asked the owner something."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def asked(self, session: str, turn: str) -> None:
        self.calls.append((session, turn))


def test_a_turn_that_asks_the_owner_is_marked_and_handed_to_the_chat_relay(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        executable = fake_claude(tmp_path, final={"report": "Needs a detail", "question": "Which calendar?"})
        service = UserChatService(crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable))
        questions = Questions()
        service.questions = questions  # type: ignore[assignment]
        answer = asyncio.run(service.answer(session, "hello"))
        assert answer.message.text == "Which calendar?"
        events = crud.repository.control_read(session).events
        marked = [e for e in events if e.kind == QUESTION_ASKED]
        assert [e.data["message_id"] for e in marked] == [answer.message.id]
        assert questions.calls == [(session, marked[0].turn)]


def test_a_turn_that_reports_is_not_handed_to_the_chat_relay(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        executable = fake_claude(tmp_path)
        service = UserChatService(crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable))
        questions = Questions()
        service.questions = questions  # type: ignore[assignment]
        asyncio.run(service.answer(session, "hello"))
        assert questions.calls == []
        assert not any(e.kind == QUESTION_ASKED for e in crud.repository.control_read(session).events)


def test_only_the_self_chat_identity_runs_a_turn_for_a_chat_answer(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        executable = fake_claude(tmp_path)
        service = UserChatService(crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable))
        asyncio.run(service.answer(session, "hello"))
        with pytest.raises(HeinzelError, match="no chat answer origin"):
            asyncio.run(service.answer_from_chat(session, "hello"))
    with current.local("self-chat"):
        with pytest.raises(HeinzelError, match="no chat answer origin"):  # a typed message is no chat answer
            asyncio.run(service.answer_from_chat(session, "hello"))
        crud.accept(session, MessageRequest(request_id="from-chat", text="The work one"))
        answer = asyncio.run(service.answer_from_chat(session, "from-chat"))
        assert answer.message.role == "assistant"
        with pytest.raises(HeinzelError, match="authenticated user"):  # no head runs it as its own
            asyncio.run(service.answer(session, "from-chat"))


def test_a_stopped_turn_for_a_chat_answer_is_set_aside_from_the_app_like_a_typed_one(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        executable = fake_claude(tmp_path)
        service = UserChatService(crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable))
        asyncio.run(service.answer(session, "hello"))
    with current.local("self-chat"):
        crud.accept(session, MessageRequest(request_id="from-chat", text="The work one"))
        turn = interactive_turn_id("from-chat")
        with crud.repository.control_writer(session) as writer:  # the process ended mid-turn
            writer.append("interactive.started", key=turn, turn=turn, data={"message_id": "from-chat"})
    web = CallIdentity(
        current.owner(), "web-session", "browser", "private", frozenset({"private:read", "private:write"})
    )
    with current.using(web):
        assert service.turn_state(session, "from-chat").status == "unresolved"
        service.abandon(session, "from-chat")
        assert service.turn_state(session, "from-chat").status == "abandoned"
    with current.local("self-chat"):
        service._set_aside(session, "from-chat")  # idempotent: already resolved
    with current.using(web):
        assert service.turn_state(session, "from-chat").status == "abandoned"


def test_a_message_accepted_before_retire_is_not_answered_until_restore(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        executable = fake_claude(tmp_path)
        service = UserChatService(crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable))
        crud.retire(session, "retire-after-accept")
        with pytest.raises(HeinzelError, match="retired"):
            asyncio.run(service.answer(session, "hello"))
        events = crud.repository.control_read(session).events
        assert not [event for event in events if event.kind == "interactive.started"]
        assert not (tmp_path / "model-calls").exists()
        crud.retire(session, "restore-after-accept", retired=False)
        assert asyncio.run(service.answer(session, "hello")).message.text == "A real recorded reply"
