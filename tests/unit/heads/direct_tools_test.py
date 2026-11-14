"""A Heinzel's direct tools (ADR 0162): one toolset and one policy, in-process and over the chat's private pipe."""

import asyncio
import json
import re
import subprocess
from datetime import timedelta

import anyio
import pytest
from mcp import Client
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from heinzel import ActionGate, CurrentUser, HeinzelError, Module
from heinzel.heads import mcp
from heinzel.kernel import (
    AgentResult,
    Budget,
    ChatToolError,
    HzTools,
    McpServer,
    ToolProfile,
    chat_tools,
    direct_tools,
    pydantic_ai_tools,
)
from heinzel.kernel._identity import CallIdentity
from heinzel.modules.system.runner.chat._service import ChatService

pytestmark = pytest.mark.unit


class Gate(ActionGate):
    def __init__(self) -> None:
        self.calls = []

    def propose(self, call):  # type: ignore[override]
        self.calls.append(call)
        return f"a{len(self.calls)}"


def notes(ran: list[str]) -> Module:
    module = Module("notes", "Test-only operations.")

    @module.operation(audience="everyday")
    def owner() -> dict[str, str]:
        """Show who owns the notes."""
        ran.append("owner")
        return {"name": "Mira", "email": "mira.sato@example.org"}

    @module.operation(read_only=False, audience="everyday")
    def publish(title: str, pages: int = 1) -> str:
        """Publish the notes."""
        ran.append("publish")
        return "published"

    @module.operation(audience="everyday")
    def broken() -> str:
        """Fail, naming someone."""
        msg = "No access for mira.sato@example.org"
        raise HeinzelError(msg)

    @module.operation(audience="everyday")
    def buggy() -> str:
        """Fail with a bug."""
        msg = "secret mira.sato@example.org"
        raise KeyError(msg)

    @module.operation(audience="everyday")
    def who(current: CurrentUser) -> str:
        """Say who the call runs as."""
        identity = current.context()
        return f"{identity.authentication}:{','.join(sorted(identity.permissions))}"

    @module.operation(private=True, audience="everyday")
    def personal() -> str:
        """Private data is never an ambient chat tool."""
        return "private"

    return module


@pytest.fixture
def ran() -> list[str]:
    return []


@pytest.fixture
def kernel(make_kernel, ran):
    kernel = make_kernel(builtins=True, notes=notes(ran))
    kernel.bind(ActionGate, Gate())
    return kernel


def open_chat(kernel) -> str:
    return kernel.resolve(ChatService).open(None).id


CALLS = [
    ("notes_owner", {}, "ran"),
    ("notes_publish", {"title": "Week 43", "pages": 2}, "proposed"),
    ("notes_publish", {"title": "Week 44", "approval_proof": "forged", "current": "x"}, "proposed"),
    ("notes_publish", {}, "refused"),
    ("notes_publish", {"title": "Week 45", "pages": "many"}, "refused"),
    ("notes_broken", {}, "refused"),
    ("notes_buggy", {}, "refused"),
    ("notes_who", {}, "ran"),
]
"""One table of calls; both paths must give each the outcome on the right, with the same text."""


def outcome(kernel, before: int, text: str, *, error: bool) -> tuple[str, str]:
    proposed = len(kernel.resolve(ActionGate).calls) > before
    kind = "refused" if error else "proposed" if proposed else "ran"
    return kind, re.sub(r"\(action a\d+\)", "(action …)", text)


def direct_outcomes(kernel, session: str) -> list[tuple[str, str]]:
    tools = {tool.name: tool for tool in pydantic_ai_tools(chat_tools(kernel, session))}
    results = []
    for name, arguments, _ in CALLS:
        before = len(kernel.resolve(ActionGate).calls)
        text = tools[f"mcp__hz__{name}"].function(**arguments)
        error = text.startswith("Error: ")
        results.append(outcome(kernel, before, text.removeprefix("Error: "), error=error))
    return results


def mcp_outcomes(kernel, session: str) -> list[tuple[str, str]]:
    async def run() -> list[tuple[str, str]]:
        results = []
        async with Client(mcp.build(kernel, chat=session)) as client:
            for name, arguments, _ in CALLS:
                before = len(kernel.resolve(ActionGate).calls)
                result = await client.call_tool(name, arguments)
                text = result.content[0].text
                if result.is_error:
                    # The MCP SDK frames every tool error so; the words inside are the policy's own.
                    frame = f"Error executing tool {name}: "
                    assert text.startswith(frame), text
                    text = text.removeprefix(frame)
                results.append(outcome(kernel, before, text, error=bool(result.is_error)))
        return results

    return anyio.run(run)


def test_one_table_of_calls_has_the_same_outcome_in_process_and_over_the_chat_pipe(kernel, ran):
    session = open_chat(kernel)
    direct = direct_outcomes(kernel, session)
    direct_ran = list(ran)
    proposals = [(call.tool, call.arguments, call.command) for call in kernel.resolve(ActionGate).calls]
    assert [kind for kind, _ in direct] == [kind for _, _, kind in CALLS]
    over_mcp = mcp_outcomes(kernel, session)
    assert [kind for kind, _ in over_mcp] == [kind for kind, _ in direct]
    # The same words too, except where the MCP SDK checks the arguments first and words a bad one its own way.
    checked_first = {3, 4}
    assert [o for i, o in enumerate(over_mcp) if i not in checked_first] == [
        o for i, o in enumerate(direct) if i not in checked_first
    ]
    assert direct[3][1].startswith("Invalid arguments for notes_publish: title")
    assert ran == direct_ran * 2  # only the reads ran, on both paths
    assert direct_ran == ["owner"]
    assert [(c.tool, c.arguments, c.command) for c in kernel.resolve(ActionGate).calls] == proposals * 2
    # An undeclared argument never reaches the operation or the proposal: not a forged proof, not an injected name.
    assert proposals[1] == (
        "notes_publish",
        {"title": "Week 44", "pages": 1},
        "hz notes publish --title 'Week 44' --pages 1",
    )
    owner, who = direct[0][1], direct[-1][1]
    assert json.loads(owner) == {"name": "Mira", "email": "[email]"}
    assert json.loads(who) == "mcp-stdio:private:read"
    assert "mira" not in direct[6][1]
    assert "KeyError" in direct[6][1]
    assert direct[5][1] == "No access for [email]"


def test_both_paths_list_the_same_tools_with_the_same_schemas(kernel):
    session = open_chat(kernel)
    direct = {tool.name.removeprefix("mcp__hz__"): tool.tool_def.parameters_json_schema for tool in _direct(kernel)}

    async def listed():
        async with Client(mcp.build(kernel, chat=session)) as client:
            return {tool.name for tool in (await client.list_tools()).tools}

    assert set(direct) == anyio.run(listed)
    assert "notes_personal" not in direct
    assert set(direct["notes_publish"]["properties"]) == {"title", "pages"}


def _direct(kernel):
    return pydantic_ai_tools(chat_tools(kernel, open_chat(kernel)))


def test_a_call_never_runs_as_the_identity_of_the_thread_that_runs_the_turn(kernel):
    [who] = [tool for tool in chat_tools(kernel, open_chat(kernel)) if tool.name == "notes_who"]
    user = kernel.resolve(CurrentUser)
    web = CallIdentity(user.owner(), "web-session", "session", "hz-web", frozenset({"private:read", "private:write"}))
    with user.using(web):
        assert json.loads(who.call({})) == "mcp-stdio:private:read"
        assert user.context() == web


def test_the_tools_need_an_existing_chat_and_the_check_in_profile_needs_a_check_in(kernel):
    with pytest.raises(HeinzelError):
        chat_tools(kernel, "0123456789abcdef")
    with pytest.raises(HeinzelError, match="durable check-in chat"):
        chat_tools(kernel, open_chat(kernel), "personal-check-in")


def test_the_scoped_confirmation_runs_only_for_its_own_chat(kernel):
    session = open_chat(kernel)
    [confirm] = [tool for tool in chat_tools(kernel, session) if tool.kind == "confirm-authorized"]
    assert not confirm.read_only
    with pytest.raises(ChatToolError, match="limited to the MCP chat session"):
        confirm.call({"session": "fedcba9876543210", "action": "a1"})


def test_a_profile_without_hz_tools_is_left_as_it_is(kernel):
    profile = ToolProfile(mcp_servers={"other": McpServer(command=["other"])})
    assert direct_tools(kernel, profile) == (profile, [])


def test_the_split_drops_only_hz_s_own_pipe(kernel):
    session = open_chat(kernel)
    profile = ToolProfile(
        read_files=False,
        mcp_servers={"hz": McpServer(command=["hz"]), "other": McpServer(command=["other"])},
        hz_tools=HzTools(session=session),
    )
    rest, tools = direct_tools(kernel, profile)
    assert set(rest.mcp_servers) == {"other"}
    assert rest.hz_tools is None
    assert {tool.name for tool in tools} >= {"notes_owner", "notes_publish"}


class InProcessRunner:
    """A runner that runs its model inside hz, as the engine runner does: a fake model, hz's tools called directly."""

    def __init__(self, kernel, script: list[tuple[str, dict]]) -> None:
        self.kernel = kernel
        self.script = script
        self.returned: list[str] = []

    def run(self, prompt, *, contract, tools: ToolProfile, timeout, budget, **_choice) -> AgentResult:
        del timeout, budget
        rest, hz = direct_tools(self.kernel, tools)
        assert not rest.mcp_servers  # hz's pipe is left out: nothing to start
        called: list[str] = []

        def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            returns = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
            if returns:
                self.returned = [str(part.content) for part in returns]
                return ModelResponse(parts=[TextPart("Done.")])
            assert {f"mcp__hz__{name}" for name, _ in self.script} <= {t.name for t in info.function_tools}
            return ModelResponse(parts=[ToolCallPart(f"mcp__hz__{name}", args) for name, args in self.script])

        agent = Agent(FunctionModel(model), instructions=contract, tools=pydantic_ai_tools(hz, counted=called.append))
        answer = asyncio.run(agent.run(prompt))  # closes its loop, unlike run_sync
        return AgentResult(
            runner="in-process",
            status="completed",
            draft=answer.output,
            tool_calls={name: called.count(name) for name in called},
            duration_seconds=0.1,
        )


def test_a_heinzel_turn_on_an_in_process_runner_starts_no_hz_mcp_process(kernel, ran, monkeypatch):
    def refuse(*args, **kwargs):
        msg = f"a process was started: {args!r}"
        raise AssertionError(msg)

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", refuse)
    service = kernel.resolve(ChatService)
    session = service.open(None).id
    runner = InProcessRunner(kernel, [("notes_owner", {}), ("notes_publish", {"title": "Week 43"})])
    answered = service.send(runner, session, "Publish the notes", budget_usd=1.0)
    assert ran == ["owner"]
    [call] = kernel.resolve(ActionGate).calls
    assert (call.session, call.tool, call.arguments) == (session, "notes_publish", {"title": "Week 43", "pages": 1})
    assert json.loads(runner.returned[0]) == {"name": "Mira", "email": "[email]"}
    assert runner.returned[1].startswith("Not run: `hz notes publish --title 'Week 43' --pages 1`")
    assert answered.messages[-1].text == "Done."
    assert answered.messages[-1].tools == {"mcp__hz__notes_owner": 1, "mcp__hz__notes_publish": 1}


def test_an_agent_cli_still_gets_the_same_tools_through_its_private_pipe(kernel):
    seen: list[ToolProfile] = []

    class CliRunner:
        def run(self, prompt, *, contract, tools: ToolProfile, timeout: timedelta, budget: Budget, **_):
            seen.append(tools)
            return AgentResult(runner="cli", status="completed", draft="ok", duration_seconds=0.1)

    service = kernel.resolve(ChatService)
    session = service.open(None).id
    service.send(CliRunner(), session, "Hello", budget_usd=1.0)
    [tools] = seen
    command = tools.mcp_servers["hz"].command
    assert command[command.index("--chat") + 1] == session
    assert tools.hz_tools == HzTools(session=session, tools="everyday", server="hz")
