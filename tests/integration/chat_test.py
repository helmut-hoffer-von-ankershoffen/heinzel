"""The chat end to end with a fake runner (ADR 0045): `hz chat` in a terminal, the chat's MCP server, the web stream.

The fake runner behaves like an agent: it streams text through the injected `Progress`, and calls hz tools through
the chat's MCP head in-process, so what it proposes goes through the same tool layer a real agent's calls do. One test
starts the very command line the runner hands the agent, as a real stdio MCP server.
"""

import json
import os
import pty
import re
import select
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import anyio
import pytest
from fastapi.testclient import TestClient
from mcp import Client, StdioServerParameters
from mcp.server.mcpserver.exceptions import ToolError
from typer.testing import CliRunner

from heinzel import (
    ActionGate,
    AgentResult,
    Budget,
    CurrentUser,
    HeinzelError,
    Module,
    Progress,
    TokenUsage,
    ToolProfile,
    __version__,
)
from heinzel.heads import _stream as operation_stream
from heinzel.heads import api, mcp, web
from heinzel.heads._exposure import exposed
from heinzel.heads.cli import build
from heinzel.kernel import Config, Kernel, boot
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import MessageRequest
from heinzel.modules.heinzel._session_operations import _crud
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository
from heinzel.modules.system.daemon._service import DaemonService
from heinzel.modules.system.runner.chat._models import ChatMessage
from heinzel.modules.system.runner.chat._service import ChatService, server_command

pytestmark = pytest.mark.integration

TOKEN = "chat-token-for-tests"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
WORDS = ("Proposed ", "publishing ", "it.")
DRAFT = "I proposed publishing Week 43."
"""Differs from the streamed words, so a test sees which of the two the terminal printed."""


@dataclass
class Script:
    """What the fake agent does on each run of one test: the tools it calls, and how its run ends."""

    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    contracts: list[str] = field(default_factory=list)
    choices: list[dict[str, object]] = field(default_factory=list)
    result: AgentResult | None = None
    kernel: Kernel | None = None


def fake_runner_module(script: Script, ran: list[str]) -> Module:
    module = Module("fakeagent", "A fake agent runner and an operation to act on.")

    class FakeRunner:
        def __init__(self, progress: Progress) -> None:
            self.progress = progress

        def run(
            self,
            prompt: str,
            *,
            contract: str,
            tools: ToolProfile,
            timeout: timedelta,
            budget: Budget,
            **choice: object,
        ) -> AgentResult:
            del timeout, budget
            script.choices.append(choice)
            assert not tools.read_files  # a chat reads through hz only
            script.prompts.append(prompt)
            script.contracts.append(contract)
            emit = self.progress.emitter()
            command = tools.mcp_servers["hz"].command
            session = command[command.index("--chat") + 1]
            assert script.kernel is not None
            head = "mcp-check-in" if "personal-check-in" in command else mcp.HEAD
            surface = {item.name: item for item in exposed(script.kernel, head)}
            for name, arguments in script.calls:
                emit("tool", {"name": name})
                mcp.chat_tool(script.kernel, surface[name], session, local=True)(**arguments)
            if script.result is not None:
                return script.result
            for word in WORDS:
                emit("text", {"text": word})
            return AgentResult(
                runner="fake", status="completed", draft=DRAFT, usage=TokenUsage(cost_usd=0.02), duration_seconds=0.1
            )

    module.provides("agent-runner", modes={"read"}, name="fake")(FakeRunner)

    @module.operation(read_only=False)
    def publish(title: str) -> str:
        """Publish something."""
        ran.append(title)
        return f"published {title}"

    return module


@pytest.fixture
def ran() -> list[str]:
    return []


@pytest.fixture
def script() -> Script:
    return Script(calls=[("fakeagent_publish", {"title": "Week 43"})])


@pytest.fixture
def kernel(make_kernel, ran, script):
    config = Config(files=(), data={"capabilities": {"agent_runner": "fake"}})
    script.kernel = make_kernel(builtins=True, fakeagent=fake_runner_module(script, ran), config=config)
    return script.kernel


def hz(kernel: Kernel, *args: str, input: str | None = None):  # noqa: A002 - as CliRunner names it
    return CliRunner().invoke(build(kernel), list(args), input=input)


def test_hz_chat_streams_the_answer_and_runs_a_proposal_only_after_y(kernel, ran):
    result = hz(kernel, "chat", "--context", "contract:weekly", input="Publish it\ny\n/exit\n")
    assert result.exit_code == 0, result.output
    assert "".join(WORDS) in result.output  # streamed as it came
    assert DRAFT not in result.output  # not printed again after streaming
    assert "· fakeagent_publish" in result.output  # the tool call, as it happened
    assert "The agent proposes: hz fakeagent publish --title 'Week 43'" in result.output
    assert "Ran `hz fakeagent publish --title 'Week 43'`" in result.output
    assert ran == ["Week 43"]
    assert "fake · $0.0200" in result.output
    usage = json.loads(hz(kernel, "ai", "usage", "--by", "cause", "--json").output)  # the message was recorded
    assert [(row["key"], row["cost_usd"]) for row in usage["breakdowns"]["cause"]] == [("chat", 0.02)]


def test_hz_chat_continue_resumes_and_anything_but_y_declines(kernel, ran, script):
    first = hz(kernel, "chat", input="Publish it\nn\n/exit\n")
    assert first.exit_code == 0, first.output
    assert "Declined `hz fakeagent publish --title 'Week 43'`." in first.output
    script.calls = []
    again = hz(kernel, "chat", "--continue", input="And now?\n")
    assert again.exit_code == 0, again.output
    assert "Publish it" in again.output  # the conversation so far, shown again
    assert script.prompts[-1].startswith("User: Publish it")
    assert script.prompts[-1].endswith("User: And now?")
    assert ran == []


def test_leaving_at_the_question_keeps_the_action_pending_for_later(kernel, ran):
    left = hz(kernel, "chat", input="Publish it\n")  # Ctrl-D at "Run it? [y/N]"
    assert left.exit_code == 0, left.output
    assert ran == []
    back = hz(kernel, "chat", "--continue", input="y\n/exit\n")
    assert "The agent proposes: hz fakeagent publish --title 'Week 43'" in back.output
    assert ran == ["Week 43"]


def test_a_failed_answer_says_why_and_how_to_fix_it(kernel, script):
    script.calls = []
    script.result = AgentResult(
        runner="claude-code",
        status="failed",
        detail="Claude Code's login expired: run `claude auth login`",
        fix="claude auth login",
        duration_seconds=0.1,
    )
    result = hz(kernel, "chat", input="Hello\n/exit\n")
    assert result.exit_code == 0, result.output
    assert "Stopped: Claude Code's login expired" in result.output
    assert "Fix: claude auth login" in result.output


def test_a_message_the_chat_refuses_is_an_error_and_the_chat_goes_on(make_kernel, ran, script):
    config = Config(
        files=(),
        data={"capabilities": {"agent_runner": "fake"}, "system": {"runner": {"chat": {"session_max_usd": 0.005}}}},
    )
    script.kernel = make_kernel(builtins=True, fakeagent=fake_runner_module(script, ran), config=config)
    result = hz(script.kernel, "chat", input="Hello\n/exit\n")
    assert result.exit_code == 0, result.output
    assert "Error: This chat has cost $0.00 of its $0.01 cap" in " ".join(result.output.split())
    assert script.prompts == []


def test_hz_chat_continue_without_a_chat_says_so(kernel):
    result = hz(kernel, "chat", "--continue")
    assert result.exit_code == 1
    assert "no chat to continue" in result.output


def test_chat_open_with_an_event_id_returns_a_session_to_link_to(kernel):
    result = hz(kernel, "chat", "open", "--context", "ev42", "--detail", "Build failed", "--json")
    assert result.exit_code == 0, result.output
    session = json.loads(result.output)
    assert {key: session["context"][key] for key in ("kind", "ref", "detail")} == {
        "kind": "event",
        "ref": "ev42",
        "detail": "Build failed",
    }
    assert len(session["id"]) == 16


def test_chat_remove_deletes_a_session(kernel):
    session = json.loads(hz(kernel, "chat", "open", "--json").output)["id"]
    assert json.loads(hz(kernel, "chat", "remove", session, "--json").output) == []
    assert "There is no chat session" in hz(kernel, "chat", "show", session).output


def test_the_chat_mcp_server_proposes_writes_over_mcp_and_runs_nothing(kernel, ran):
    session = json.loads(hz(kernel, "chat", "open", "--json").output)["id"]

    async def scenario() -> None:
        async with Client(mcp.build(kernel, chat=session, chat_tools="all")) as client:
            listed = {tool.name for tool in (await client.list_tools()).tools}
            assert "fakeagent_publish" in listed
            assert {name for name in listed if name.startswith("system_runner_chat")} == {
                "system_runner_chat_confirm_authorized_mcp"
            }
            reply = await client.call_tool("fakeagent_publish", {"title": "Week 44"})
            assert "Not run" in str(reply.content)

    anyio.run(scenario)
    assert ran == []
    shown = json.loads(hz(kernel, "chat", "show", session, "--json").output)
    assert [(a["command"], a["status"]) for a in shown["actions"]] == [
        ("hz fakeagent publish --title 'Week 44'", "pending")
    ]
    assert isinstance(kernel.resolve(ActionGate), ActionGate)


def test_runner_chat_can_only_propose_the_bound_inactive_check_in(kernel, script):
    with kernel.resolve(CurrentUser).local("cli"):
        crud = kernel.resolve(SessionCrud)
        durable = crud.new_chat("create-chat-proposal-test", "A test Heinzel")
        crud.accept(durable.session_id, MessageRequest(request_id="prior-history-001", text="Keep our notes local."))
        timezone = "Europe/Berlin"
        script.calls = [
            (
                "heinzel_propose_check_in",
                {"session_id": durable.session_id, "request_id": "runner-checkin-001"},
            )
        ]
        service = kernel.resolve(ChatService)
        opened = hz(
            kernel,
            "chat",
            "open-check-in",
            durable.session_id,
            "open-checkin-001",
            timezone,
            "--json",
        )
        assert opened.exit_code == 0, opened.output
        chat = json.loads(opened.output)
        assert chat["context"]["detail"].find("Keep our notes local.") >= 0
        assert chat["context"]["timezone"] == timezone
        assert hz(kernel, "chat", "open", "--context", f"check-in:{durable.session_id}").exit_code == 1
        unrelated = crud.new_chat("unrelated-check-in-owner", "Unrelated session")

        async def listed_tools() -> set[str]:
            async with Client(mcp.build(kernel, chat=chat["id"], chat_tools="personal-check-in")) as client:
                names = {tool.name for tool in (await client.list_tools()).tools}
                wrong = await client.call_tool(
                    "heinzel_propose_check_in",
                    {"session_id": durable.session_id, "request_id": "wrong-timezone", "timezone": "UTC"},
                )
                assert wrong.is_error
                unbound = await client.call_tool(
                    "heinzel_propose_check_in",
                    {"session_id": unrelated.session_id, "request_id": "other-session", "timezone": timezone},
                )
                assert unbound.is_error
                return names

        assert anyio.run(listed_tools) == {"heinzel_propose_check_in"}
        assert not kernel.resolve(ChatService)._store.load(chat["id"]).actions
        ordinary_chat = service.open(None)
        with pytest.raises(HeinzelError, match="requires a durable check-in chat"):
            mcp.build(kernel, chat=ordinary_chat.id, chat_tools="personal-check-in")
        assert any(
            item.record.qualname == "heinzel" and item.operation.name == "propose-check-in"
            for item in exposed(kernel, mcp.HEAD)
        )
        assert not any(
            item.record.qualname == "heinzel" and item.operation.name == "propose-check-in"
            for item in exposed(kernel, "mcp-http")
        )
        sent = hz(
            kernel,
            "chat",
            "send",
            chat["id"],
            "notify me every 5 minutes",
            "--request-id",
            "message-checkin-001",
            "--json",
        )
        assert sent.exit_code == 0, sent.output
        answer = json.loads(sent.output)
        assert "Keep our notes local." in script.contracts[-1]
        assert "only prepare the standard local five-minute check-in" in script.contracts[-1]
        assert answer["actions"][0]["status"] == "pending"
        assert answer["actions"][0]["module"] == "heinzel"
        assert answer["actions"][0]["operation"] == "propose-check-in"
        assert answer["actions"][0]["arguments"] == {
            "session_id": durable.session_id,
            "request_id": "runner-checkin-001",
            "timezone": timezone,
        }
        prompt_count = len(script.prompts)
        retried = hz(
            kernel,
            "chat",
            "send",
            chat["id"],
            "notify me every 5 minutes",
            "--request-id",
            "message-checkin-001",
            "--json",
        )
        assert retried.exit_code == 0, retried.output
        assert len(script.prompts) == prompt_count
        changed = hz(
            kernel,
            "chat",
            "send",
            chat["id"],
            "notify me every ten minutes",
            "--request-id",
            "message-checkin-001",
            "--json",
        )
        assert changed.exit_code == 1
        assert "already identifies different message text" in changed.output
        assert len(script.prompts) == prompt_count
        confirmed = service.confirm(chat["id"], answer["actions"][0]["id"])
        assert confirmed.actions[0].status == "done"
        assert crud.snapshot(durable.session_id).schedule.status == "proposed"


def test_unanswered_check_in_runner_turn_cannot_be_replayed_after_reload(kernel, script):
    with kernel.resolve(CurrentUser).local("cli"):
        crud = kernel.resolve(SessionCrud)
        durable = crud.new_chat("create-chat-replay-guard", "Replay guard")
        service = kernel.resolve(ChatService)
        chat = service.open_check_in(durable.session_id, "open-checkin-replay-guard", "UTC", "Schedule: none")
        interrupted = service._store.load(chat.id)
        interrupted.messages.extend(
            [
                ChatMessage(role="user", text="earlier request", at=datetime.now(UTC)),
                ChatMessage(role="assistant", text="earlier answer", at=datetime.now(UTC)),
            ]
        )
        interrupted.messages.append(ChatMessage(role="user", text="notify me every 5 minutes", at=datetime.now(UTC)))
        service._store.save(interrupted)

        retry = hz(
            kernel,
            "chat",
            "send",
            chat.id,
            "notify me every 5 minutes",
            "--request-id",
            "new-after-reload",
            "--json",
        )
        assert retry.exit_code == 1
        assert "previous runner turn has no saved answer" in retry.output
        assert script.prompts == []


def test_the_command_the_agent_starts_is_the_gated_chat_server(tmp_path, monkeypatch):
    """The real `python -m heinzel system serve mcp --chat <id> --project <dir>`, as a stdio process."""
    project = tmp_path / "project"
    project.mkdir()
    state = tmp_path / "project-state"
    (project / "hz.toml").write_text(f"[hz]\nstate_dir = {json.dumps(str(state))}\n", encoding="utf-8")
    monkeypatch.delenv("HZ_STATE_DIR")  # so the project file decides where sessions are, in both processes
    monkeypatch.chdir(project)  # the head that opens the chat runs in the project, and so must the server
    kernel = boot()  # as `hz` boots there
    session = json.loads(hz(kernel, "chat", "open", "--json").output)["id"]
    assert (state / "chat" / session).is_dir()
    command = server_command(session, Path.cwd(), "all")
    monkeypatch.chdir(tmp_path)  # where the agent starts it: anywhere else

    async def scenario() -> str:
        server = StdioServerParameters(command=command[0], args=command[1:], env=dict(os.environ))
        async with Client(server) as client:
            reply = await client.call_tool(
                "system_config_set", {"key": "system.directory.lookback_days", "value": "30"}
            )
            return str(reply.content)

    assert "Not run" in anyio.run(scenario)
    shown = json.loads(hz(kernel, "chat", "show", session, "--json").output)
    assert [(a["operation"], a["status"]) for a in shown["actions"]] == [("set", "pending")]


def test_a_chat_gets_the_everyday_operations_unless_all_are_asked_for(kernel):
    """Every tool's schema goes with every message: the everyday ones make a short message far cheaper (ADR 0057)."""
    session = json.loads(hz(kernel, "chat", "open", "--json").output)["id"]

    async def listed(tools: str) -> set[str]:
        async with Client(mcp.build(kernel, chat=session, chat_tools=tools)) as client:  # type: ignore[arg-type]
            return {tool.name for tool in (await client.list_tools()).tools}

    everyday = anyio.run(listed, "everyday")
    every = anyio.run(listed, "all")
    assert "fakeagent_publish" not in everyday  # a test module's operations are expert ones
    assert "fakeagent_publish" in every
    assert everyday < every


def test_the_chat_keeps_its_model_and_effort_and_each_answer_says_how_it_was_made(kernel, script):
    script.calls = []
    script.result = AgentResult(
        runner="fake",
        model="sonnet-5",
        billing="plan",
        status="completed",
        draft="Hi!",
        usage=TokenUsage(input_tokens=3, output_tokens=40, cache_read_tokens=900, cost_usd=0.1337),
        duration_seconds=2.5,
    )
    session = json.loads(hz(kernel, "chat", "open", "--json").output)["id"]
    first = hz(kernel, "chat", "send", session, "hi", "--model", "sonnet", "--effort", "high", "--json")
    assert first.exit_code == 0, first.output
    hz(kernel, "chat", "send", session, "and again", "--json")
    assert script.choices == [{"model": "sonnet", "effort": "high"}] * 2  # the chat keeps its choice
    shown = json.loads(hz(kernel, "chat", "show", session, "--json").output)
    assert (shown["model"], shown["effort"]) == ("sonnet", "high")
    answer = shown["messages"][1]
    assert {key: answer[key] for key in ("model", "effort", "billing", "duration_seconds", "input_tokens")} == {
        "model": "sonnet-5",
        "effort": "high",
        "billing": "plan",
        "duration_seconds": 2.5,
        "input_tokens": 3,
    }
    hz(kernel, "chat", "send", session, "back to default", "--model", "", "--json")
    assert script.choices[-1] == {"effort": "high"}  # an empty model goes back to the runner's own


def test_the_terminal_labels_plan_usage_as_api_prices_covered_by_the_plan(kernel, script):
    script.calls = []
    script.result = AgentResult(
        runner="fake",
        billing="plan",
        status="completed",
        draft="Hi!",
        usage=TokenUsage(cost_usd=0.13),
        duration_seconds=1,
    )
    result = hz(kernel, "chat", input="hi\n/exit\n")
    assert "uses your plan · ≈ $0.13 at API prices" in result.output


def test_a_runner_from_before_model_and_effort_answers_without_them_and_says_so(make_kernel):
    module = Module("oldagent", "A runner with the run of before model and effort.")

    class OldRunner:
        def run(
            self, prompt: str, *, contract: str, tools: ToolProfile, timeout: timedelta, budget: Budget
        ) -> AgentResult:
            del prompt, contract, tools, timeout, budget
            return AgentResult(runner="old", status="completed", draft="Hi!", duration_seconds=0.1)

    module.provides("agent-runner", modes={"read"}, name="old")(OldRunner)
    config = Config(files=(), data={"capabilities": {"agent_runner": "old"}})
    kernel = make_kernel(builtins=True, oldagent=module, config=config)
    session = json.loads(hz(kernel, "chat", "open", "--json").output)["id"]
    result = hz(kernel, "chat", "send", session, "hi", "--model", "sonnet", "--json")
    assert result.exit_code == 0, result.output
    answer = json.loads(result.output)["messages"][-1]
    assert (answer["status"], answer["text"]) == ("completed", "Hi!")
    assert any("can't take a model or an effort" in warning for warning in answer["warnings"])


def test_serving_a_chat_on_a_port_is_refused(kernel):
    result = hz(kernel, "system", "serve", "mcp", "--chat", "0123456789abcdef", "--transport", "streamable-http")
    assert result.exit_code == 2
    assert "over stdio only" in result.output


def stream_events(body: str) -> list[tuple[str, dict]]:
    return [
        (block.split("\n")[0].removeprefix("event: "), json.loads(block.split("\n")[1].removeprefix("data: ")))
        for block in body.strip().split("\n\n")
    ]


def web_client(kernel: Kernel, tmp_path: Path) -> TestClient:
    (tmp_path / "index.html").write_text("<!doctype html>", encoding="utf-8")
    return TestClient(web.build(kernel, port=8000, token=TOKEN, static=tmp_path), base_url="http://127.0.0.1:8000")


def stage_schedule(client: TestClient, session_id: str, activation: dict[str, object], *, revision: int | None = None):
    """Stage an exact schedule review, optionally changing its revision for a retry test."""
    return client.post(
        "/api/system/runner/chat/propose-schedule-control",
        headers=AUTH,
        params={
            "durable_session": session_id,
            "request_id": activation["request_id"],
            "expected_revision": activation["expected_revision"] if revision is None else revision,
            "action": activation["action"],
            "expected_contract_version": activation["expected_contract_version"],
            "expected_preview_hash": activation["expected_preview_hash"],
        },
    )


def activation_arguments(session_id: str, schedule: dict[str, object], request_id: str) -> dict[str, object]:
    """Bind an activation to one exact schedule preview."""
    return {
        "session_id": session_id,
        "request_id": request_id,
        "expected_revision": schedule["revision"],
        "action": "activate",
        "expected_contract_version": schedule["contract_version"],
        "expected_preview_hash": schedule["preview_hash"],
    }


def test_the_web_head_streams_a_message_as_server_sent_events(kernel, tmp_path):
    client = web_client(kernel, tmp_path)
    opened = client.post("/api/system/runner/chat/open", params={"context": "route:/doctor"}, headers=AUTH).json()
    with client.stream(
        "POST",
        "/api/stream/system/runner/chat/send",
        params={"session": opened["id"], "message": "Publish it"},
        headers=AUTH,
    ) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())
    events = stream_events(body)
    assert [kind for kind, _ in events] == ["tool", "text", "text", "text", "result"]
    assert "".join(data["text"] for kind, data in events if kind == "text") == "".join(WORDS)
    session = events[-1][1]["data"]
    assert session["actions"][0]["status"] == "pending"
    refused = client.post("/api/stream/system/runner/chat/send", params={"session": opened["id"], "message": "x"})
    assert refused.status_code == 401  # the token guards streams like every other call


def test_personal_schedule_approval_uses_the_existing_human_chat_gate(kernel, tmp_path):
    client = web_client(kernel, tmp_path)
    created_response = client.post(
        "/api/heinzel/new-chat", headers=AUTH, json={"request_id": "human-check-in-create", "name": "Quiet helper"}
    )
    assert created_response.status_code == 200, created_response.text
    session_id = created_response.json()["session_id"]
    proposed_response = client.post(
        "/api/heinzel/propose-check-in",
        headers=AUTH,
        json={"session_id": session_id, "request_id": "human-check-in-proposal", "timezone": "UTC"},
    )
    assert proposed_response.status_code == 200, proposed_response.text
    schedule = proposed_response.json()["schedule"]
    activation = activation_arguments(session_id, schedule, "human-check-in-activation")

    # An ordinary private request or a chat-scoped MCP action cannot supply or mint a human decision.
    denied = client.post("/api/heinzel/schedule-control", headers=AUTH, json=activation)
    assert denied.status_code == 400
    chat_proposal = stage_schedule(client, session_id, activation)
    assert chat_proposal.status_code == 200, chat_proposal.text
    proposal = chat_proposal.json()
    assert proposal["actions"][0]["status"] == "pending"
    assert "approval_proof" not in proposal["actions"][0]["arguments"]

    async def agent_cannot_stage_or_confirm_itself():
        async with Client(mcp.build(kernel, chat=proposal["id"])) as agent_chat:
            available = {tool.name for tool in (await agent_chat.list_tools()).tools}
            assert "heinzel_schedule_control" not in available
            assert "system_runner_chat_propose_schedule_control" not in available
            assert "system_runner_chat_confirm" not in available
            assert "system_runner_chat_recover" not in available
        async with Client(mcp.build(kernel)) as direct:
            response = await direct.call_tool("heinzel_schedule_control", activation)
            assert response.is_error

    anyio.run(agent_cannot_stage_or_confirm_itself)

    confirmed = client.post(
        "/api/system/runner/chat/confirm",
        headers=AUTH,
        params={"session": proposal["id"], "action": proposal["actions"][0]["id"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["actions"][0]["status"] == "done"
    assert "approval_proof" not in json.dumps(confirmed.json())
    assert confirmed.json()["messages"][-1]["text"].startswith("Ran `hz heinzel schedule-control ")
    snapshot = client.get("/api/heinzel/session", headers=AUTH, params={"session_id": session_id}).json()
    assert snapshot["schedule"]["status"] == "active"

    with kernel.resolve(CurrentUser).local("cli"):
        events = PersonalSessionRepository(kernel.resolve(CurrentUser)).control_read(session_id).events
    assert sum(event.kind == "approval.issued" for event in events) == 1
    assert sum(event.kind == "schedule.activated" for event in events) == 1
    assert "approval_proof" not in json.dumps([event.model_dump(mode="json") for event in events])

    # Simulate a crash after the journal effect committed but before chat.confirm saved its result.
    chat_store = kernel.resolve(ChatService)._store
    interrupted = chat_store.load(proposal["id"])
    interrupted.actions[0].status = "running"
    chat_store.save(interrupted)
    reconciled = client.post(
        "/api/system/runner/chat/recover", headers=AUTH, params={"session": proposal["id"], "action": "a1"}
    )
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["actions"][0]["status"] == "done"
    with kernel.resolve(CurrentUser).local("cli"):
        events = PersonalSessionRepository(kernel.resolve(CurrentUser)).control_read(session_id).events
    assert sum(event.kind == "approval.issued" for event in events) == 1
    assert sum(event.kind == "schedule.activated" for event in events) == 1


def test_schedule_confirmation_failure_before_effect_is_uncertain_and_not_replayed(kernel, tmp_path, monkeypatch):
    client = web_client(kernel, tmp_path)
    session_id = client.post(
        "/api/heinzel/new-chat", headers=AUTH, json={"request_id": "confirm-fail-before-create", "name": "Moss"}
    ).json()["session_id"]
    schedule = client.post(
        "/api/heinzel/propose-check-in",
        headers=AUTH,
        json={"session_id": session_id, "request_id": "confirm-fail-before-schedule", "timezone": "UTC"},
    ).json()["schedule"]
    proposal = stage_schedule(
        client, session_id, activation_arguments(session_id, schedule, "confirm-fail-before-action")
    ).json()
    chat_id, action_id = proposal["id"], proposal["actions"][0]["id"]

    def fail_before_effect(*_args, **_kwargs):
        msg = "synthetic failure before the schedule journal append"
        raise HeinzelError(msg)

    monkeypatch.setattr(kernel.resolve(SessionCrud), "schedule_control", fail_before_effect)
    confirmation = client.post(
        "/api/system/runner/chat/confirm", headers=AUTH, params={"session": chat_id, "action": action_id}
    )
    assert confirmation.status_code == 200, confirmation.text
    assert confirmation.json()["actions"][0]["status"] == "uncertain"
    assert "not repeated" in confirmation.json()["actions"][0]["result"]
    assert confirmation.json()["messages"][-1]["text"].startswith("Uncertain `hz heinzel schedule-control")
    assert (
        client.post(
            "/api/system/runner/chat/confirm", headers=AUTH, params={"session": chat_id, "action": action_id}
        ).status_code
        == 400
    )
    assert (
        client.get("/api/heinzel/session", headers=AUTH, params={"session_id": session_id}).json()["schedule"]["status"]
        == "proposed"
    )


def test_schedule_confirmation_response_failure_reconciles_committed_effect(kernel, tmp_path, monkeypatch):
    client = web_client(kernel, tmp_path)
    session_id = client.post(
        "/api/heinzel/new-chat", headers=AUTH, json={"request_id": "confirm-after-create", "name": "Moss"}
    ).json()["session_id"]
    schedule = client.post(
        "/api/heinzel/propose-check-in",
        headers=AUTH,
        json={"session_id": session_id, "request_id": "confirm-after-schedule", "timezone": "UTC"},
    ).json()["schedule"]
    proposal = stage_schedule(
        client, session_id, activation_arguments(session_id, schedule, "confirm-after-action")
    ).json()
    chat_id, action_id = proposal["id"], proposal["actions"][0]["id"]
    crud = kernel.resolve(SessionCrud)
    original_snapshot = crud.snapshot
    raised = False

    def fail_after_effect(found_session: str, *args, **kwargs):
        nonlocal raised
        snapshot = original_snapshot(found_session, *args, **kwargs)
        if found_session == session_id and snapshot.schedule.status == "active" and not raised:
            raised = True
            msg = "synthetic response failure after the schedule journal append"
            raise HeinzelError(msg)
        return snapshot

    monkeypatch.setattr(crud, "snapshot", fail_after_effect)
    confirmation = client.post(
        "/api/system/runner/chat/confirm", headers=AUTH, params={"session": chat_id, "action": action_id}
    )
    assert confirmation.status_code == 200, confirmation.text
    assert raised
    assert confirmation.json()["actions"][0]["status"] == "done"
    assert "exact schedule change is recorded" in confirmation.json()["actions"][0]["result"]
    monkeypatch.setattr(crud, "snapshot", original_snapshot)
    assert (
        client.get("/api/heinzel/session", headers=AUTH, params={"session_id": session_id}).json()["schedule"]["status"]
        == "active"
    )
    assert (
        client.post(
            "/api/system/runner/chat/confirm", headers=AUTH, params={"session": chat_id, "action": action_id}
        ).status_code
        == 400
    )


def test_human_web_approval_is_consumed_by_mcp_without_crossing_the_tool_protocol(  # noqa: PLR0915 - full handoff
    kernel, tmp_path, caplog
):
    client = web_client(kernel, tmp_path)
    created = client.post(
        "/api/heinzel/new-chat", headers=AUTH, json={"request_id": "mcp-handoff-create", "name": "Moss"}
    ).json()
    session_id = created["session_id"]

    async def propose_from_stdio_mcp():
        async with Client(mcp.build(kernel)) as direct:
            tools = {tool.name for tool in (await direct.list_tools()).tools}
            assert "heinzel_propose_check_in" in tools
            response = await direct.call_tool(
                "heinzel_propose_check_in",
                {"session_id": session_id, "request_id": "mcp-handoff-schedule", "timezone": "UTC"},
            )
            assert not response.is_error
            assert response.structured_content["schedule"]["status"] == "proposed"
            return response.structured_content["schedule"]

    schedule = anyio.run(propose_from_stdio_mcp)
    proposal = stage_schedule(client, session_id, activation_arguments(session_id, schedule, "mcp-handoff-action"))
    assert proposal.status_code == 200, proposal.text
    chat_id = proposal.json()["id"]
    chat_store = kernel.resolve(ChatService)._store
    outside = tmp_path / "outside-approval-directory"
    outside.mkdir()
    approval_directory = chat_store.root / chat_id / "mcp-approvals"
    approval_directory.symlink_to(outside, target_is_directory=True)
    redirected = client.post(
        "/api/system/runner/chat/authorize-for-mcp",
        headers=AUTH,
        params={"session": chat_id, "action": "a1"},
    )
    assert redirected.status_code == 400
    assert list(outside.iterdir()) == []
    approval_directory.unlink()
    headless_cli = hz(kernel, "chat", "authorize-for-mcp", chat_id, "a1", "--json")
    assert headless_cli.exit_code != 0
    assert "interactive terminal" in headless_cli.output

    async def consume_before_authorization() -> str:
        async with Client(mcp.build(kernel, chat=chat_id)) as agent:
            tools = {tool.name for tool in (await agent.list_tools()).tools}
            assert "system_runner_chat_authorize_for_mcp" not in tools
            assert "system_runner_chat_confirm" not in tools
            confirm_tool = next(name for name in tools if name.endswith("confirm_authorized_mcp"))
            response = await agent.call_tool(confirm_tool, {"session": chat_id, "action": "a1"})
            assert response.is_error
            return confirm_tool

    confirm_tool = anyio.run(consume_before_authorization)
    denied = client.post(
        "/api/heinzel/schedule-control",
        headers=AUTH,
        json=activation_arguments(session_id, schedule, "mcp-handoff-action"),
    )
    assert denied.status_code == 400
    authorized = client.post(
        "/api/system/runner/chat/authorize-for-mcp",
        headers=AUTH,
        params={"session": chat_id, "action": "a1"},
    )
    assert authorized.status_code == 200, authorized.text
    body = authorized.json()
    assert body["actions"][0]["mcp_authorized"] is True
    assert "approval_proof" not in json.dumps(body)
    proof_file = chat_store.root / chat_id / "mcp-approvals" / "a1.proof"
    assert proof_file.stat().st_mode & 0o777 == 0o600
    proof = proof_file.read_text(encoding="ascii").splitlines()[0]
    assert proof not in authorized.text

    # A delayed MCP call cannot use an expired proof; the human can explicitly authorize it again.
    proof_file.write_text(f"{proof}\n2000-01-01T00:00:00+00:00", encoding="ascii")

    async def reject_expired() -> None:
        async with Client(mcp.build(kernel, chat=chat_id)) as agent:
            response = await agent.call_tool(confirm_tool, {"session": chat_id, "action": "a1"})
            assert response.is_error

    anyio.run(reject_expired)
    still_pending = kernel.resolve(ChatService)._store.load(chat_id)
    assert still_pending.actions[0].status == "pending"
    assert still_pending.actions[0].mcp_authorized is False
    reauthorized = client.post(
        "/api/system/runner/chat/authorize-for-mcp",
        headers=AUTH,
        params={"session": chat_id, "action": "a1"},
    )
    assert reauthorized.status_code == 200, reauthorized.text
    renewed_proof = proof_file.read_text(encoding="ascii").splitlines()[0]
    assert renewed_proof != proof

    async def consume_and_replay() -> None:
        async with Client(mcp.build(kernel, chat=chat_id)) as agent:
            other_session = await agent.call_tool(confirm_tool, {"session": "ffffffffffffffff", "action": "a1"})
            assert other_session.is_error
            responses = []

            async def consume() -> None:
                responses.append(await agent.call_tool(confirm_tool, {"session": chat_id, "action": "a1"}))

            async with anyio.create_task_group() as tasks:
                tasks.start_soon(consume)
                tasks.start_soon(consume)
            assert sum(not response.is_error for response in responses) == 1
            assert sum(response.is_error for response in responses) == 1
            successful = next(response for response in responses if not response.is_error)
            assert proof not in str(successful.content)
            assert renewed_proof not in str(successful.content)
            replay = await agent.call_tool(confirm_tool, {"session": chat_id, "action": "a1"})
            assert replay.is_error
        async with Client(mcp.build(kernel)) as direct:
            assert confirm_tool not in {tool.name for tool in (await direct.list_tools()).tools}

    anyio.run(consume_and_replay)
    assert not proof_file.exists()
    result = client.get("/api/heinzel/session", headers=AUTH, params={"session_id": session_id}).json()
    assert result["schedule"]["status"] == "active"
    assert proof not in caplog.text
    assert renewed_proof not in caplog.text
    with kernel.resolve(CurrentUser).local("cli"):
        events = PersonalSessionRepository(kernel.resolve(CurrentUser)).control_read(session_id).events
    assert sum(event.kind == "approval.issued" for event in events) == 2
    assert sum(event.kind == "schedule.activated" for event in events) == 1


def test_stdio_mcp_can_require_direct_human_terminal_approval_without_a_bearer(  # noqa: PLR0915 - proof boundary flow
    kernel, tmp_path, monkeypatch
):
    user = kernel.resolve(CurrentUser)
    with user.local("cli"):
        crud = kernel.resolve(SessionCrud)
        created = crud.new_chat("mcp-terminal-create", "Terminal approved helper")
        proposed = crud.propose_check_in(created.session_id, "mcp-terminal-proposal", "UTC")
    schedule = proposed.schedule
    assert schedule is not None
    arguments = {
        "session_id": created.session_id,
        "request_id": "mcp-terminal-activate",
        "expected_revision": schedule.revision,
        "action": "activate",
        "expected_contract_version": schedule.contract_version,
        "expected_preview_hash": schedule.preview_hash,
    }
    item = next(
        item
        for item in exposed(kernel, mcp.HEAD)
        if item.record.qualname == "heinzel" and item.operation.name == "schedule-control"
    )
    handler = mcp.tool(kernel, mcp._mcp_item(item), local=True)

    async def schema():
        async with Client(mcp.build(kernel)) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            return tools["heinzel_schedule_control"].input_schema

    schedule_schema = anyio.run(schema)
    assert "approval_proof" not in schedule_schema["properties"]
    with pytest.raises(ToolError, match="cannot accept approval proofs"):
        handler(**(arguments | {"approval_proof": "model-supplied-bearer"}))
    assert not any(
        item.record.qualname == "heinzel" and item.operation.name == "schedule-control"
        for item in exposed(kernel, "mcp-http")
    )

    def invoke_with_tty(call_arguments: dict[str, object], *, approve: bool):
        master, slave = pty.openpty()
        real_open = os.open
        terminal_prompt: list[str] = []
        terminal_errors: list[str] = []

        def open_terminal(path: str, flags: int, *args, **kwargs):
            return os.dup(slave) if path == "/dev/tty" else real_open(path, flags, *args, **kwargs)

        with monkeypatch.context() as patcher:
            patcher.setattr(mcp.os, "open", open_terminal)

            def answer_on_tty() -> None:
                prompt = bytearray()
                deadline = time.monotonic() + 3
                while b"> " not in prompt:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not select.select([master], [], [], max(0, remaining))[0]:
                        terminal_errors.append("MCP did not show the exact schedule approval challenge")
                        os.write(master, b"no\n")
                        return
                    prompt.extend(os.read(master, 4096))
                displayed = prompt.decode("utf-8")
                match = re.search(r"Type exactly: ([^\r\n]+)", displayed)
                if match is None:
                    terminal_errors.append("MCP approval prompt omitted its challenge")
                    os.write(master, b"no\n")
                    return
                terminal_prompt.append(displayed)
                answer = match.group(1) if approve else "no"
                os.write(master, f"{answer}\n".encode())

            reader = threading.Thread(target=answer_on_tty)
            reader.start()

            async def call_tool():
                async with Client(mcp.build(kernel)) as connected:
                    return await connected.call_tool("heinzel_schedule_control", call_arguments)

            response = anyio.run(call_tool)
            reader.join(timeout=3)
            os.close(master)
            os.close(slave)
            assert not reader.is_alive()
            assert terminal_errors == []
            assert len(terminal_prompt) == 1
            displayed = terminal_prompt[0]
            assert call_arguments["session_id"] in displayed
            assert call_arguments["expected_preview_hash"] in displayed
            return response, displayed

    rejected, _ = invoke_with_tty(arguments, approve=False)
    assert rejected.is_error
    with user.local("cli"):
        assert crud.snapshot(created.session_id).schedule.status == "proposed"
        assert not any(
            event.kind in {"approval.issued", "schedule.activated"}
            for event in PersonalSessionRepository(user).control_read(created.session_id).events
        )

    activated, shown = invoke_with_tty(arguments, approve=True)
    assert not activated.is_error
    assert activated.structured_content["schedule"]["status"] == "active"
    assert schedule.summary in shown
    assert schedule.cron in shown
    assert schedule.timezone in shown
    with pytest.raises(ToolError, match="schedule changed"):
        handler(**arguments)
    with user.local("cli"):
        events = PersonalSessionRepository(user).control_read(created.session_id).events
    assert sum(event.kind == "approval.issued" for event in events) == 1
    assert sum(event.kind == "schedule.activated" for event in events) == 1

    foreign = CurrentUser(tmp_path / "foreign-owner" / "identity")
    with foreign.local("cli"):
        foreign_crud = _crud(foreign)
        foreign_created = foreign_crud.new_chat("foreign-mcp-create", "Foreign helper")
        foreign_proposal = foreign_crud.propose_check_in(foreign_created.session_id, "foreign-mcp-proposal", "UTC")
    assert foreign_proposal.schedule is not None
    foreign_arguments = {
        "session_id": foreign_created.session_id,
        "request_id": "foreign-mcp-activate",
        "expected_revision": foreign_proposal.schedule.revision,
        "action": "activate",
        "expected_contract_version": foreign_proposal.schedule.contract_version,
        "expected_preview_hash": foreign_proposal.schedule.preview_hash,
    }
    with pytest.raises(ToolError):
        handler(**foreign_arguments)
    with foreign.local("cli"):
        assert foreign_crud.snapshot(foreign_created.session_id).schedule.status == "proposed"
        foreign_events = PersonalSessionRepository(foreign).control_read(foreign_created.session_id).events
    assert not any(event.kind in {"approval.issued", "schedule.activated"} for event in foreign_events)


def test_mcp_handoff_crashes_fail_closed_before_and_after_the_schedule_commit(kernel, tmp_path, monkeypatch):
    client = web_client(kernel, tmp_path)
    created = client.post(
        "/api/heinzel/new-chat", headers=AUTH, json={"request_id": "mcp-crash-create", "name": "Moss"}
    ).json()
    session_id = created["session_id"]
    schedule = client.post(
        "/api/heinzel/propose-check-in",
        headers=AUTH,
        json={"session_id": session_id, "request_id": "mcp-crash-schedule", "timezone": "UTC"},
    ).json()["schedule"]
    chat = stage_schedule(client, session_id, activation_arguments(session_id, schedule, "mcp-crash-action")).json()
    chat_id = chat["id"]
    action = chat["actions"][0]["id"]
    authorize = client.post(
        "/api/system/runner/chat/authorize-for-mcp",
        headers=AUTH,
        params={"session": chat_id, "action": action},
    )
    assert authorize.status_code == 200, authorize.text
    service = kernel.resolve(ChatService)
    user = kernel.resolve(CurrentUser)

    # A process stopping after atomically taking the handoff but before saving `running` loses this approval only.
    assert service._store.take_mcp_approval(chat_id, action) is not None
    with user.local("mcp-stdio"), pytest.raises(HeinzelError):
        service.confirm_authorized_mcp(chat_id, action)
    pending = service._store.load(chat_id).actions[0]
    assert pending.status == "pending"
    assert not pending.mcp_authorized

    authorize = client.post(
        "/api/system/runner/chat/authorize-for-mcp",
        headers=AUTH,
        params={"session": chat_id, "action": action},
    )
    assert authorize.status_code == 200, authorize.text

    def crash_before_owner_commit(*_args, **_kwargs):
        msg = "simulated process death"
        raise SystemExit(msg)

    monkeypatch.setattr(kernel, "invoke", crash_before_owner_commit)
    with user.local("mcp-stdio"), pytest.raises(SystemExit, match="simulated process death"):
        service.confirm_authorized_mcp(chat_id, action)
    interrupted = service._store.load(chat_id).actions[0]
    assert interrupted.status == "running"
    assert not interrupted.mcp_authorized
    assert not (service._store.root / chat_id / "mcp-approvals" / f"{action}.proof").exists()

    monkeypatch.undo()
    with user.local("mcp-stdio"):
        recovered = service.recover(chat_id, action)
    assert recovered.actions[0].status == "uncertain"
    assert (
        client.get("/api/heinzel/session", headers=AUTH, params={"session_id": session_id}).json()["schedule"]["status"]
        == "proposed"
    )
    with user.local("mcp-stdio"), pytest.raises(HeinzelError):
        service.confirm_authorized_mcp(chat_id, action)


def test_human_decline_revokes_the_pending_mcp_handoff(kernel, tmp_path):
    client = web_client(kernel, tmp_path)
    created = client.post(
        "/api/heinzel/new-chat", headers=AUTH, json={"request_id": "mcp-revoke-create", "name": "Moss"}
    ).json()
    session_id = created["session_id"]
    schedule = client.post(
        "/api/heinzel/propose-check-in",
        headers=AUTH,
        json={"session_id": session_id, "request_id": "mcp-revoke-schedule", "timezone": "UTC"},
    ).json()["schedule"]
    proposal = stage_schedule(
        client, session_id, activation_arguments(session_id, schedule, "mcp-revoke-action")
    ).json()
    chat_id, action = proposal["id"], proposal["actions"][0]["id"]
    authorized = client.post(
        "/api/system/runner/chat/authorize-for-mcp",
        headers=AUTH,
        params={"session": chat_id, "action": action},
    )
    assert authorized.status_code == 200, authorized.text
    chat_store = kernel.resolve(ChatService)._store
    proof_file = chat_store.root / chat_id / "mcp-approvals" / f"{action}.proof"
    assert proof_file.exists()

    declined = client.post(
        "/api/system/runner/chat/decline", headers=AUTH, params={"session": chat_id, "action": action}
    )
    assert declined.status_code == 200, declined.text
    assert declined.json()["actions"][0]["status"] == "declined"
    assert not declined.json()["actions"][0]["mcp_authorized"]
    assert not proof_file.exists()

    async def mcp_cannot_use_declined_action() -> None:
        async with Client(mcp.build(kernel, chat=chat_id)) as agent:
            tools = {tool.name for tool in (await agent.list_tools()).tools}
            confirm_tool = next(name for name in tools if name.endswith("confirm_authorized_mcp"))
            response = await agent.call_tool(confirm_tool, {"session": chat_id, "action": action})
            assert response.is_error

    anyio.run(mcp_cannot_use_declined_action)
    current = client.get("/api/heinzel/session", headers=AUTH, params={"session_id": session_id}).json()
    assert current["schedule"]["status"] == "proposed"


@pytest.mark.parametrize(
    "status_versions",
    [
        ("0.7.27", "0.7.27", True),
        (None, None, False),
        (None, __version__, False),
        (__version__, "0.7.27", True),
    ],
)
def test_approval_refuses_a_mismatched_or_unobserved_daemon_version_with_a_recovery_instruction(
    kernel, tmp_path, monkeypatch, status_versions
):
    version, installed_version, mismatch = status_versions
    client = web_client(kernel, tmp_path)
    created = client.post(
        "/api/heinzel/new-chat", headers=AUTH, json={"request_id": "mcp-daemon-create", "name": "Moss"}
    ).json()
    session_id = created["session_id"]
    schedule = client.post(
        "/api/heinzel/propose-check-in",
        headers=AUTH,
        json={"session_id": session_id, "request_id": "mcp-daemon-schedule", "timezone": "UTC"},
    ).json()["schedule"]
    proposal = stage_schedule(
        client, session_id, activation_arguments(session_id, schedule, "mcp-daemon-action")
    ).json()
    daemon = kernel.resolve(DaemonService)
    installed_old = daemon.status().model_copy(
        update={
            "installed": True,
            "loaded": True,
            "version": version,
            "installed_version": installed_version,
            "version_mismatch": mismatch,
        }
    )
    monkeypatch.setattr(daemon, "status", lambda: installed_old)

    authorization = client.post(
        "/api/system/runner/chat/authorize-for-mcp",
        headers=AUTH,
        params={"session": proposal["id"], "action": "a1"},
    )
    assert authorization.status_code == 400
    assert "hz system daemon restart" in authorization.json()["error"]
    assert "wait for a service-managed tick" in authorization.json()["error"]
    assert (version or "unknown") in authorization.json()["error"]
    assert (installed_version or "unknown") in authorization.json()["error"]
    direct_confirmation = client.post(
        "/api/system/runner/chat/confirm",
        headers=AUTH,
        params={"session": proposal["id"], "action": "a1"},
    )
    assert direct_confirmation.status_code == 400
    assert "hz system daemon restart" in direct_confirmation.json()["error"]

    pending = kernel.resolve(ChatService)._store.load(proposal["id"]).actions[0]
    assert pending.status == "pending"
    assert not pending.mcp_authorized
    with kernel.resolve(CurrentUser).local("cli"):
        events = PersonalSessionRepository(kernel.resolve(CurrentUser)).control_read(session_id).events
    assert not any(event.kind == "approval.issued" for event in events)


def test_schedule_proposal_retries_return_the_same_chat_and_reject_changed_arguments(kernel, tmp_path):
    client = web_client(kernel, tmp_path)
    created = client.post(
        "/api/heinzel/new-chat", headers=AUTH, json={"request_id": "approval-retry-create", "name": "Retry helper"}
    ).json()
    session_id = created["session_id"]
    proposed = client.post(
        "/api/heinzel/propose-check-in",
        headers=AUTH,
        json={"session_id": session_id, "request_id": "approval-retry-proposal", "timezone": "UTC"},
    ).json()
    activation = activation_arguments(session_id, proposed["schedule"], "approval-retry-activation")

    first = stage_schedule(client, session_id, activation)
    retry = stage_schedule(client, session_id, activation)
    changed = stage_schedule(client, session_id, activation, revision=activation["expected_revision"] + 1)

    assert first.status_code == 200, first.text
    assert retry.status_code == 200, retry.text
    assert retry.json()["id"] == first.json()["id"]
    assert len(retry.json()["actions"]) == 1
    assert changed.status_code == 400


def test_interrupted_schedule_confirmation_without_a_journal_effect_is_blocked(kernel, tmp_path):
    client = web_client(kernel, tmp_path)
    created = client.post(
        "/api/heinzel/new-chat",
        headers=AUTH,
        json={"request_id": "interrupted-check-in-create", "name": "Quiet helper"},
    )
    assert created.status_code == 200, created.text
    session_id = created.json()["session_id"]
    proposed = client.post(
        "/api/heinzel/propose-check-in",
        headers=AUTH,
        json={"session_id": session_id, "request_id": "interrupted-check-in-proposal", "timezone": "UTC"},
    ).json()
    schedule = proposed["schedule"]
    staged = client.post(
        "/api/system/runner/chat/propose-schedule-control",
        headers=AUTH,
        params={
            "durable_session": session_id,
            "request_id": "interrupted-activation",
            "expected_revision": schedule["revision"],
            "action": "activate",
            "expected_contract_version": schedule["contract_version"],
            "expected_preview_hash": schedule["preview_hash"],
        },
    )
    assert staged.status_code == 200, staged.text
    chat = staged.json()
    store = kernel.resolve(ChatService)._store
    interrupted = store.load(chat["id"])
    interrupted.actions[0].status = "running"
    store.save(interrupted)

    recovered = client.post(
        "/api/system/runner/chat/recover", headers=AUTH, params={"session": chat["id"], "action": "a1"}
    )
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["actions"][0]["status"] == "uncertain"
    assert "not repeated" in recovered.json()["actions"][0]["result"]
    current = client.get("/api/heinzel/session", headers=AUTH, params={"session_id": session_id}).json()
    assert current["schedule"]["status"] == "proposed"
    with kernel.resolve(CurrentUser).local("cli"):
        events = PersonalSessionRepository(kernel.resolve(CurrentUser)).control_read(session_id).events
    assert not any(event.kind in {"approval.issued", "schedule.activated"} for event in events)
    refused = client.post(
        "/api/system/runner/chat/confirm", headers=AUTH, params={"session": chat["id"], "action": "a1"}
    )
    assert refused.status_code == 400
    with kernel.resolve(CurrentUser).local("cli"):
        events = PersonalSessionRepository(kernel.resolve(CurrentUser)).control_read(session_id).events
    assert not any(event.kind in {"approval.issued", "schedule.activated"} for event in events)


def test_cli_chat_confirmation_issues_and_consumes_the_exact_schedule_intent(kernel):
    runner = CliRunner()

    def call(*arguments: str) -> dict:
        result = runner.invoke(build(kernel), [*arguments, "--json"])
        assert result.exit_code == 0, result.output
        return json.loads(result.output)

    created = call("heinzel", "new-chat", "cli-human-check-in", "--name", "CLI helper")
    proposed = call("heinzel", "propose-check-in", created["session_id"], "cli-check-in-proposal", "--timezone", "UTC")
    schedule = proposed["schedule"]
    staged = call(
        "chat",
        "propose-schedule-control",
        created["session_id"],
        "cli-check-in-activate",
        str(schedule["revision"]),
        "activate",
        str(schedule["contract_version"]),
        schedule["preview_hash"],
    )
    assert staged["actions"][0]["status"] == "pending"
    assert "approval_proof" not in json.dumps(staged)
    confirmed = call("chat", "confirm", staged["id"], staged["actions"][0]["id"])
    assert confirmed["actions"][0]["status"] == "done", confirmed["actions"][0]["result"]
    assert "approval_proof" not in json.dumps(confirmed)
    with kernel.resolve(CurrentUser).local("cli"):
        snapshot = kernel.resolve(SessionCrud).snapshot(created["session_id"])
    assert snapshot.schedule is not None
    assert snapshot.schedule.status == "active"
    store = kernel.resolve(ChatService)._store
    interrupted = store.load(staged["id"])
    interrupted.actions[0].status = "running"
    store.save(interrupted)
    recovered = call("chat", "recover", staged["id"], "a1")
    assert recovered["actions"][0]["status"] == "done"
    with kernel.resolve(CurrentUser).local("cli"):
        events = PersonalSessionRepository(kernel.resolve(CurrentUser)).control_read(created["session_id"]).events
    assert sum(event.kind == "schedule.activated" for event in events) == 1


def test_the_web_stream_ends_with_the_error_an_operation_raised(kernel, tmp_path):
    body = (
        web_client(kernel, tmp_path)
        .post(
            "/api/stream/system/runner/chat/send", params={"session": "0123456789abcdef", "message": "Hi"}, headers=AUTH
        )
        .text
    )
    assert body.startswith("event: error\n")
    assert "There is no chat session 0123456789abcdef" in body


def test_a_bug_in_a_streamed_operation_ends_the_stream_with_an_error(make_kernel, tmp_path):
    module = Module("buggy", "An operation with a bug.")

    @module.operation(heads={"cli", "web"})
    def crash() -> str:
        """Fail."""
        msg = "secret detail"
        raise RuntimeError(msg)

    body = web_client(make_kernel(builtins=True, buggy=module), tmp_path).post("/api/stream/buggy/crash", headers=AUTH)
    assert stream_events(body.text) == [("error", {"error": "crash failed with a bug; the log of `hz ui` says why."})]


def test_a_quiet_long_operation_stream_sends_keepalives(make_kernel, tmp_path, monkeypatch):
    module = Module("slow", "A quiet, long operation.")

    @module.operation(heads={"cli", "web"})
    def install(progress: Progress) -> str:
        """Report an install before doing quiet work."""
        with progress.task("Installing plugin"):
            time.sleep(0.04)
        return "installed"

    monkeypatch.setattr(operation_stream, "HEARTBEAT_SECONDS", 0.01)
    body = (
        web_client(make_kernel(builtins=True, slow=module), tmp_path)
        .post("/api/stream/slow/install", headers=AUTH)
        .text
    )

    assert 'event: task\ndata: {"text": "Installing plugin"}' in body
    assert ": keep-alive\n\n" in body
    assert 'event: result\ndata: {"data": "installed"}' in body


def test_the_unauthenticated_rest_head_has_no_chat(kernel):
    paths = {route.path for route in api.build(kernel).routes}  # type: ignore[attr-defined]
    assert not any("chat" in path or path.startswith("/stream") for path in paths)
