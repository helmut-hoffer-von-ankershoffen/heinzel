"""Chat sessions (ADR 0045): storage and retention, the prompt and caps of a message, and confirmation in code."""

import os
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from heinzel import (
    AgentResult,
    Budget,
    HeinzelError,
    Kernel,
    Module,
    NoticeKind,
    Notifications,
    ProposedCall,
    TokenUsage,
    ToolProfile,
)
from heinzel.modules.system.runner._models import RunnerSettings
from heinzel.modules.system.runner.chat import ChatMessage, ChatService, ChatSettings, ChatStore
from heinzel.modules.system.runner.chat._policy import guarded_settings, refusal
from heinzel.modules.system.runner.chat._service import (
    CHAT_TIMEOUT,
    PREAMBLE,
    contract,
    parse_context,
    server_command,
    transcript,
)
from heinzel.modules.system.runner.chat._store import MAX_MESSAGES, MAX_PROPOSALS, StoreGate, now

pytestmark = pytest.mark.unit


class Recorder:
    """An agent runner that records what it was asked, may propose actions like the chat's MCP server, and answers."""

    def __init__(
        self,
        store: ChatStore,
        *,
        propose: list[ProposedCall] | None = None,
        cost: float | None = 0.01,
        during: Callable[[str], None] | None = None,
    ) -> None:
        self.store = store
        self.propose = propose or []
        self.cost = cost
        self.during = during
        self.calls: list[dict[str, object]] = []

    def run(self, prompt: str, *, contract: str, tools: ToolProfile, timeout: timedelta, budget: Budget) -> AgentResult:
        self.calls.append(
            {"prompt": prompt, "contract": contract, "tools": tools, "timeout": timeout, "budget": budget}
        )
        assert tools.working_dir is not None
        assert list(tools.working_dir.iterdir()) == []  # nothing of yours to read
        command = tools.mcp_servers["hz"].command
        session = command[command.index("--chat") + 1]
        for call in self.propose:
            self.store.propose(replace(call, session=session))  # as the gate records it; its policy is tested below
        if self.during is not None:
            self.during(session)
        return AgentResult(
            runner="fake",
            status="completed",
            draft="Proposed it.",
            usage=TokenUsage(cost_usd=self.cost),
            duration_seconds=0.1,
        )


def notes(seen: list[dict[str, object]]) -> Module:
    module = Module("notes", "Test-only operations.")

    @module.operation(read_only=False)
    def tag(name: str, weight: int = 1) -> dict[str, object]:
        """Tag something."""
        seen.append({"name": name, "weight": weight})
        return {"tagged": name, "weight": weight}

    @module.operation(read_only=False)
    def count(kernel: Kernel, times: int = 1) -> int:
        """Count, with the kernel injected."""
        seen.append({"kernel": type(kernel).__name__, "times": times})
        return times

    @module.operation(read_only=False)
    def crash() -> str:
        """Fail with a bug."""
        seen.append({"crash": True})
        msg = "bug"
        raise RuntimeError(msg)

    @module.operation(read_only=False, heads={"cli"})
    def secret() -> str:
        """Only for a terminal."""
        return "never"

    return module


def call(operation: str = "tag", module: str = "notes", **arguments: object) -> ProposedCall:
    return ProposedCall(
        session="",
        module=module,
        operation=operation,
        tool=f"{module}_{operation}",
        arguments=arguments or {"name": "weekly", "weight": 2},  # type: ignore[arg-type]
        command=f"hz {module} {operation}",
    )


@pytest.fixture
def seen() -> list[dict[str, object]]:
    return []


@pytest.fixture
def kernel(make_kernel, seen):
    return make_kernel(builtins=True, notes=notes(seen))


@pytest.fixture
def store(settings) -> ChatStore:
    return ChatStore(settings, ChatSettings(), RunnerSettings())


@pytest.fixture
def service(store, kernel) -> ChatService:
    return ChatService(store, kernel)


def test_a_session_is_kept_privately_and_lists_most_recent_first(store, settings):
    first = store.create(None)
    second = store.create(parse_context("contract:weekly", None))
    folder = settings.state_dir / "chat" / second.id
    assert oct(folder.stat().st_mode & 0o777) == "0o700"
    assert oct((folder / "session.json").stat().st_mode & 0o777) == "0o600"
    assert second.title == "contract weekly"
    assert [s.id for s in store.sessions()] == [second.id, first.id]
    assert [s.context for s in store.sessions()] == [second.context, None]
    assert store.load(second.id).context == second.context


@pytest.mark.parametrize("bad", ["../../etc", "0123", "ZZZZZZZZZZZZZZZZ", "0123456789abcdeg"])
def test_a_session_id_is_never_a_path(store, bad):
    with pytest.raises(HeinzelError, match="not a chat session id"):
        store.load(bad)


def test_an_unknown_session_says_how_to_find_one(store):
    with pytest.raises(HeinzelError, match="hz chat list"):
        store.load("0123456789abcdef")


def test_retention_removes_old_sessions_and_the_oldest_beyond_the_count(settings):
    old, *rest = [ChatStore(settings, ChatSettings(), RunnerSettings()).create(None) for _ in range(4)]
    store = ChatStore(settings, ChatSettings(keep_days=7, keep_sessions=2), RunnerSettings())
    stale = time.time() - 8 * 86400
    os.utime(store.root / old.id / "session.json", (stale, stale))
    for index, chat in enumerate(rest):  # distinct ages, newest last
        age = time.time() - (len(rest) - index) * 60
        os.utime(store.root / chat.id / "session.json", (age, age))
    removed = store.prune()
    assert old.id in removed
    assert rest[0].id in removed  # third newest of what is left: beyond keep_sessions
    assert {s.id for s in store.sessions()} == {rest[1].id, rest[2].id}


def test_proposals_get_one_id_each_and_are_bounded(store):
    chat = store.create(None)
    ids = [store.propose(replace(call(), session=chat.id)) for _ in range(3)]  # type: ignore[arg-type]
    assert ids == ["a1", "a2", "a3"]
    assert [a.status for a in store.load(chat.id).actions] == ["pending"] * 3
    (store.root / chat.id / "proposals.jsonl").write_text("x\n" * MAX_PROPOSALS, encoding="utf-8")
    with pytest.raises(HeinzelError, match="most it takes"):
        store.propose(replace(call(), session=chat.id))


def test_context_parses_kinds_and_a_bare_id_is_an_event():
    assert parse_context("route:/contracts", None).model_dump() == {"kind": "route", "ref": "/contracts", "detail": ""}  # type: ignore[union-attr]
    assert parse_context("3f2a", "Build failed").model_dump() == {
        "kind": "event",
        "ref": "3f2a",
        "detail": "Build failed",
    }  # type: ignore[union-attr]
    assert parse_context(None, None) is None
    with pytest.raises(HeinzelError, match="Unknown chat context 'mail'"):
        parse_context("mail:inbox", None)


def test_the_context_seeds_the_contract_without_email_addresses():
    text = contract(parse_context("event:ev1", "Ana (ana.lopez@example.org) asked for a review"))
    assert text.startswith(PREAMBLE)
    assert "notification ev1" in text
    assert "ana.lopez@example.org" not in text
    assert "Ana ([email]) asked for a review" in text


def test_a_message_runs_the_runner_with_hz_tools_for_this_session_only(service, store):
    chat = service.open(parse_context("contract:weekly", None))
    runner = Recorder(store)
    answered = service.send(runner, chat.id, "What does mira@example.org own?", None)  # type: ignore[arg-type]
    [run] = runner.calls
    tools = run["tools"]
    assert isinstance(tools, ToolProfile)
    assert tools.mcp_servers["hz"].command == server_command(chat.id, Path.cwd())
    assert server_command(chat.id, Path.cwd())[:3] == [sys.executable, "-m", "heinzel"]
    assert server_command(chat.id, Path.cwd())[-2:] == ["--project", str(Path.cwd())]
    assert server_command(chat.id, Path.cwd())[-4:-2] == ["--chat-tools", "everyday"]
    assert tools.read_files is False  # no file tools: the chat reads through hz only
    assert run["prompt"] == "User: What does [email] own?"
    assert "heinzel_show" in str(run["contract"])
    assert run["timeout"] == CHAT_TIMEOUT
    assert run["budget"] == Budget(max_usd=0.25)
    assert [m.role for m in answered.messages] == ["user", "assistant"]
    assert answered.costs.spent_usd == pytest.approx(0.01)


def test_each_message_is_capped_by_the_message_cap_and_what_the_session_has_left(settings, kernel):
    store = ChatStore(settings, ChatSettings(session_max_usd=0.3), RunnerSettings(ask_max_usd=0.25))
    service = ChatService(store, kernel)
    chat = service.open(None)
    runner = Recorder(store, cost=0.2)
    service.send(runner, chat.id, "one", None)  # type: ignore[arg-type]
    service.send(runner, chat.id, "two", 5.0)  # type: ignore[arg-type]
    assert [c["budget"] for c in runner.calls] == [Budget(max_usd=0.25), Budget(max_usd=0.1)]
    with pytest.raises(HeinzelError, match=r"cost \$0\.40 of its \$0\.30 cap"):
        service.send(runner, chat.id, "three", None)  # type: ignore[arg-type]
    assert len(runner.calls) == 2


def test_the_transcript_keeps_the_most_recent_messages_that_fit(service, store):
    chat = service.open(None)
    runner = Recorder(store)
    for text in ["a" * 50, "second"]:
        service.send(runner, chat.id, text, None)  # type: ignore[arg-type]
    messages = store.load(chat.id).messages
    assert transcript(messages, limit=60) == "User: second\n\nAssistant: Proposed it."


def test_what_the_agent_proposes_is_attached_to_its_answer_and_runs_only_when_confirmed(service, store, seen):
    chat = service.open(None)
    answered = service.send(Recorder(store, propose=[call()]), chat.id, "Tag weekly", None)  # type: ignore[arg-type]
    assert answered.messages[-1].actions == ["a1"]
    assert [a.status for a in answered.pending] == ["pending"]
    assert seen == []  # proposing ran nothing
    done = service.confirm(chat.id, "a1")
    assert seen == [{"name": "weekly", "weight": 2}]  # exactly the proposed arguments
    assert done.actions[0].status == "done"
    assert done.actions[0].result == '{"tagged": "weekly", "weight": 2}'
    assert done.messages[-1].role == "action"
    with pytest.raises(HeinzelError, match="done already; it runs at most once"):
        service.confirm(chat.id, "a1")
    assert len(seen) == 1


def test_a_declined_action_never_runs(service, store, seen):
    chat = service.open(None)
    service.send(Recorder(store, propose=[call()]), chat.id, "Tag weekly", None)  # type: ignore[arg-type]
    declined = service.decline(chat.id, "a1")
    assert declined.actions[0].status == "declined"
    assert declined.messages[-1].text == "Declined `hz notes tag --name weekly --weight 2`."
    with pytest.raises(HeinzelError, match="declined already"):
        service.confirm(chat.id, "a1")
    assert seen == []


def test_confirm_refuses_unknown_actions_and_operations_no_chat_tool_offers(service, store, seen):
    chat = service.open(None)
    with pytest.raises(HeinzelError, match="has no action 'a9'"):
        service.confirm(chat.id, "a9")
    store.propose(replace(call("secret"), session=chat.id))
    store.propose(replace(call("gone", "nowhere"), session=chat.id))
    for action in ("a1", "a2"):
        with pytest.raises(HeinzelError, match="not available to the chat"):
            service.confirm(chat.id, action)
    assert seen == []


def test_a_failing_action_is_recorded_as_failed(service, store):
    chat = service.open(None)
    store.propose(replace(call(name=None), session=chat.id))
    failed = service.confirm(chat.id, "a1")
    assert failed.actions[0].status == "failed"
    assert "Invalid arguments" in failed.actions[0].result


def test_a_session_answering_a_message_is_busy_for_everyone_else(service, store):
    chat = service.open(None)
    with store.locked(chat.id), pytest.raises(HeinzelError, match="answering a message right now"):
        service.confirm(chat.id, "a1")


def test_a_blank_message_is_refused_before_anything_runs(service, store):
    chat = service.open(None)
    runner = Recorder(store)
    with pytest.raises(HeinzelError, match="the message is empty"):
        service.send(runner, chat.id, "  ", None)  # type: ignore[arg-type]
    assert runner.calls == []


def test_an_answer_whose_cost_is_not_reported_counts_at_its_cap(settings, kernel):
    store = ChatStore(settings, ChatSettings(session_max_usd=0.5), RunnerSettings(ask_max_usd=0.25))
    service = ChatService(store, kernel)
    chat = service.open(None)
    answered = service.send(Recorder(store, cost=None), chat.id, "one", None)  # type: ignore[arg-type]
    assert answered.messages[-1].budget_usd == 0.25
    assert answered.costs.spent_usd == pytest.approx(0.25)
    service.send(Recorder(store, cost=None), chat.id, "two", None)  # type: ignore[arg-type]
    with pytest.raises(HeinzelError, match="cap"):
        service.send(Recorder(store, cost=None), chat.id, "three", None)  # type: ignore[arg-type]


def test_a_misconfigured_runner_is_a_failed_answer_that_costs_nothing(service, store):
    class Broken:
        def run(self, *args: object, **kwargs: object) -> AgentResult:
            msg = "Claude Code (claude) is not on PATH."
            raise HeinzelError(msg)

    chat = service.open(None)
    answered = service.send(Broken(), chat.id, "Hello", None)  # type: ignore[arg-type]
    reply = answered.messages[-1]
    assert (reply.role, reply.status, reply.detail) == ("assistant", "failed", "Claude Code (claude) is not on PATH.")
    assert answered.costs.spent_usd == 0


def test_each_answer_gets_only_what_was_proposed_while_it_ran(service, store):
    chat = service.open(None)
    service.send(Recorder(store, propose=[call()]), chat.id, "first", None)  # type: ignore[arg-type]
    second = service.send(Recorder(store, propose=[call()]), chat.id, "second", None)  # type: ignore[arg-type]
    assert [m.actions for m in second.messages if m.role == "assistant"] == [["a1"], ["a2"]]


def test_the_session_is_locked_while_the_agent_runs(service, store):
    refused: list[str] = []

    def confirm_meanwhile(session: str) -> None:
        with pytest.raises(HeinzelError, match="answering a message right now") as busy:
            service.confirm(session, "a1")
        refused.append(str(busy.value))

    chat = service.open(None)
    service.send(Recorder(store, propose=[call()], during=confirm_meanwhile), chat.id, "Tag", None)  # type: ignore[arg-type]
    assert len(refused) == 1


@pytest.mark.parametrize(
    "case",
    [
        ("system.plugin", "add", {"spec": "heinzel-acme"}),
        ("system", "update", {}),
        ("system.config", "set", {"key": "system.runner.read_only_tools", "value": '["Read", "Bash"]'}),
        ("system.config", "set", {"key": "capabilities.agent_runner", "value": "command"}),
        ("system.config", "unset", {"key": "system.runner.chat.session_max_usd"}),
        ("system.config", "init", {"path": "/etc/passwd", "force": True}),
        ("system.config", "set", {"key": "heinzel.schedule_team", "value": '["team:payroll"]'}),
        ("system.config", "set", {"key": "heinzel.retention_days", "value": "1"}),
        ("system.config", "unset", {"key": " HEINZEL.retention-days "}),
        ("system.config", "set", {"key": "team.repository", "value": "https://example.org/other.git"}),
        ("system.daemon", "resume", {}),
        ("system.config", "set", {"key": "org.email", "value": "someone@example.org"}),
        ("system.config", "set", {"key": "system.plugin.github_token", "value": "x"}),
    ],
)
def test_a_chat_never_proposes_or_runs_what_installs_code_or_widens_its_powers(store, service, kernel, case):
    module, operation, arguments = case
    chat = service.open(None)
    proposal = ProposedCall(
        session=chat.id, module=module, operation=operation, tool="x", arguments=arguments, command="hz x"
    )
    with pytest.raises(HeinzelError, match="terminal"):
        StoreGate(store, kernel).propose(proposal)
    store.propose(proposal)  # forged straight into the store: confirm refuses it too
    with pytest.raises(HeinzelError, match=r"Not run: .*terminal"):
        service.confirm(chat.id, "a1")


def test_a_setting_that_changes_nothing_that_runs_may_be_proposed(store, service, kernel):
    chat = service.open(None)
    proposal = ProposedCall(
        session=chat.id,
        module="system.config",
        operation="set",
        tool="system_config_set",
        arguments={"key": "system.directory.lookback_days", "value": "30"},
        command="hz system config set",
    )
    assert StoreGate(store, kernel).propose(proposal) == "a1"


@pytest.mark.parametrize("operation", ["set", "unset"])
@pytest.mark.parametrize("key", ["heinzel.retention_days", " HEINZEL.retention-days ", "heinzel.future_policy"])
def test_heinzel_settings_are_guarded_even_without_registered_setting_metadata(operation, key):
    assert refusal("system.config", operation, {"key": key}) is not None


def test_what_the_person_reads_is_built_from_what_runs_and_only_parameters_run(service, store, seen):
    chat = service.open(None)
    forged = ProposedCall(
        session=chat.id,
        module="notes",
        operation="count",
        tool="notes_count",
        arguments={"times": 3, "kernel": "not the kernel"},
        command="hz system info",  # what a forged line might claim
    )
    store.propose(forged)
    shown = store.load(chat.id).actions[0].command
    assert shown == "hz notes count --times 3 --kernel 'not the kernel'"
    service.confirm(chat.id, "a1")
    assert seen == [{"kernel": "Kernel", "times": 3}]  # the injected kernel, never the forged argument


def test_a_bug_in_a_confirmed_action_is_recorded_and_it_never_runs_again(service, store, seen):
    chat = service.open(None)
    store.propose(replace(call("crash"), session=chat.id, arguments={}))
    failed = service.confirm(chat.id, "a1")
    assert failed.actions[0].status == "failed"
    assert failed.actions[0].result == "RuntimeError: failed with a bug; the log says why"
    with pytest.raises(HeinzelError, match="failed already"):
        service.confirm(chat.id, "a1")
    assert seen == [{"crash": True}]


def test_an_unreadable_or_foreign_session_file_names_the_fix(store):
    chat = store.create(None)
    path = store.root / chat.id / "session.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(HeinzelError, match=f"hz chat remove {chat.id}"):
        store.load(chat.id)
    other = store.create(None)
    (store.root / other.id / "session.json").replace(path)
    with pytest.raises(HeinzelError, match="another session's file"):
        store.load(chat.id)


def test_a_full_session_takes_no_more_messages(service, store):
    chat = service.open(None)
    full = store.load(chat.id)
    full.messages = [ChatMessage(role="user", text="x", at=now())] * MAX_MESSAGES
    store.save(full)
    with pytest.raises(HeinzelError, match="the most it keeps"):
        service.send(Recorder(store), chat.id, "one more", None)  # type: ignore[arg-type]


def test_retention_keeps_a_session_that_is_answering_and_clears_unfinished_folders(settings):
    store = ChatStore(settings, ChatSettings(keep_days=1), RunnerSettings())
    busy = store.create(None)
    stale = time.time() - 3 * 86400
    os.utime(store.root / busy.id / "session.json", (stale, stale))
    unfinished = store.root / "fedcba9876543210"
    unfinished.mkdir()
    os.utime(unfinished, (stale, stale))
    with store.locked(busy.id):
        assert store.prune() == []
    assert not unfinished.exists()
    assert busy.id in store.prune()
    with pytest.raises(HeinzelError, match="removed meanwhile"):
        store.save(busy)


def test_a_long_message_keeps_its_start_and_end_and_leaves_room_for_the_rest():
    long = ChatMessage(role="assistant", text="a" * 3000 + "b" * 20000 + "c" * 3000, at=now())
    messages = [ChatMessage(role="user", text="first", at=now()), long, ChatMessage(role="user", text="next", at=now())]
    text = transcript(messages)
    assert text.startswith("User: first")
    assert text.endswith("User: next")
    assert "a" * 100 in text
    assert "c" * 100 in text
    assert "b" * 100 not in text


def test_a_chat_about_a_notification_gets_what_the_notice_says(service, kernel):
    notices = kernel.resolve(Notifications)
    notice = notices.emit(
        NoticeKind.FAILED,
        "The weekly digest failed",
        "The tracker refused the token.",
        route="/contracts",
        command="hz heinzel list",
        key="weekly",
    )
    assert notice is not None
    chat = service.open(parse_context(notice.id, None))
    assert chat.context is not None
    assert chat.context.kind == "event"
    assert chat.context.detail.startswith("The weekly digest failed\nThe tracker refused the token.")
    assert "notification" in contract(chat.context)
    assert service.open(parse_context("no-such-event", None)).context.detail == ""  # type: ignore[union-attr]


def test_every_setting_a_project_file_may_not_set_is_guarded(kernel):
    guarded = guarded_settings(kernel)
    assert {"system.runner.ask_max_usd", "system.runner.chat.session_max_usd", "hz.expert"} <= guarded
    assert refusal("system.config", "set", {"key": "notes.anything"}, frozenset({"notes.anything"})) is not None


def test_an_address_at_a_cut_is_redacted_before_the_cut():
    text = "x" * 1990 + " alice.smith@example.com " + "y" * 4000
    cut = transcript([ChatMessage(role="assistant", text=text, at=now())])
    assert "alice" not in cut
    assert "[email]" in cut
