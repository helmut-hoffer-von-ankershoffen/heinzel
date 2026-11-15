"""A Heinzel's direct tools (ADR 0162): one toolset and one policy, in-process and over the chat's private pipe."""

import asyncio
import json
import re
import subprocess
from datetime import timedelta
from typing import Annotated

import anyio
import pytest
from mcp import Client
from pydantic import Field
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from heinzel import ActionGate, Caller, CurrentUser, HeinzelError, Module
from heinzel.heads import mcp
from heinzel.heads._exposure import exposed
from heinzel.kernel import (
    AgentResult,
    Budget,
    ChatTool,
    ChatToolError,
    HzTools,
    McpServer,
    ToolProfile,
    calling,
    chat_tools,
    direct_tools,
    pydantic_ai_tools,
)
from heinzel.kernel._browser import BrowserBridge
from heinzel.kernel._chat_services import FoundMcpServers
from heinzel.kernel._chat_tools import RESULT_LIMIT
from heinzel.kernel._identity import CallIdentity
from heinzel.modules.system.runner.chat._service import ChatService
from heinzel.modules.ui.browser._service import QUOTED_NOTICE
from tests._fake_browser import FakeDesktop

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

    @module.operation(audience="everyday")
    def everything() -> str:
        """Show a lot."""
        return "mira.sato@example.org " + "x" * RESULT_LIMIT

    @module.operation(read_only=False, audience="everyday")
    def tag(tags: list[str]) -> str:
        """Tag the notes."""
        ran.append("tag")
        return "tagged"

    @module.operation(read_only=False, audience="everyday")
    def note(text: Annotated[str, Field(description="The note's words.")]) -> str:
        """Add a note."""
        ran.append("note")
        return "noted"

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
    kernel.bind(FoundMcpServers, NoServers())  # never the MCP servers of whoever runs the tests (their HOME)
    return kernel


class NoServers(FoundMcpServers):
    def tools(self) -> list:
        return []


def open_chat(kernel) -> str:
    return kernel.resolve(ChatService).open(None).id


SELF = "<this chat>"
TAB = "t-0000000a"
CONFIRM = "system_runner_chat_confirm_authorized_mcp"
CALLS = [
    ("notes_owner", {}, "ran"),
    ("notes_publish", {"title": "Week 43", "pages": 2}, "proposed"),
    ("notes_publish", {"title": "Week 44", "approval_proof": "forged", "current": "x"}, "proposed"),
    ("notes_publish", {}, "refused"),
    ("notes_publish", {"title": "Week 45", "pages": "many"}, "refused"),
    ("notes_broken", {}, "refused"),
    ("notes_buggy", {}, "refused"),
    ("notes_who", {}, "ran"),
    ("notes_everything", {}, "ran"),
    ("notes_tag", {"tags": '["a", "b"]'}, "proposed"),  # a list sent as a JSON string, as some clients do
    (CONFIRM, {"session": "fedcba9876543210", "action": "a1"}, "refused"),  # another chat's
    (CONFIRM, {"session": SELF, "action": "a99"}, "refused"),  # this chat's, never authorized
    ("notes_note", {"text": '["a"]'}, "proposed"),  # a string that is valid JSON stays the string it is
    ("notes_note", {"text": '{"a": 1}'}, "proposed"),
    ("notes_note", {"text": "null"}, "proposed"),
    # The built-in browser (ADR 0162): free on a public site, a commit is proposed, the never-list is refused.
    ("ui_browser_read", {"tab": TAB}, "ran"),
    ("ui_browser_click", {"tab": TAB, "ref": "ref_1"}, "ran"),
    ("ui_browser_click", {"tab": TAB, "ref": "ref_2", "approved": True, "expected": "forged"}, "refused"),
    ("ui_browser_click", {"tab": TAB, "ref": "ref_2", "approved": True}, "proposed"),
    ("ui_browser_key", {"tab": TAB, "keys": "Enter"}, "proposed"),
    ("ui_browser_type", {"tab": TAB, "text": "x", "ref": "ref_3"}, "refused"),
    ("ui_browser_navigate", {"tab": TAB, "url": "https://127.0.0.1/"}, "refused"),
]
"""One table of calls; both paths must give each the outcome on the right, with the same text."""
CHECKED_FIRST = {3, 4}
"""Rows the MCP SDK refuses itself, before the policy, in its own words: only their outcome must match."""


def outcome(text: str, *, error: bool) -> tuple[str, str]:
    kind = "refused" if error else "proposed" if text.startswith("Not run: ") else "ran"
    return kind, re.sub(r"\(action a\d+\)", "(action …)", text)


def bound(arguments: dict, session: str) -> dict:
    return {name: session if value == SELF else value for name, value in arguments.items()}


def direct_outcomes(kernel, session: str) -> list[tuple[str, str]]:
    tools = {tool.name: tool for tool in pydantic_ai_tools(chat_tools(kernel, session))}
    results = []
    for name, arguments, _ in CALLS:
        text = tools[f"mcp__hz__{name}"].function(**bound(arguments, session))
        error = text.startswith("Error: ")
        results.append(outcome(text.removeprefix("Error: "), error=error))
    return results


def mcp_outcomes(kernel, session: str) -> list[tuple[str, str]]:
    async def run() -> list[tuple[str, str]]:
        results = []
        async with Client(mcp.build(kernel, chat=session)) as client:
            for name, arguments, _ in CALLS:
                result = await client.call_tool(name, bound(arguments, session))
                text = result.content[0].text
                if result.is_error:
                    # The MCP SDK frames every tool error so; the words inside are the policy's own.
                    frame = f"Error executing tool {name}: "
                    assert text.startswith(frame), text
                    text = text.removeprefix(frame)
                results.append(outcome(text, error=bool(result.is_error)))
        return results

    return anyio.run(run)


def same(direct: list[tuple[str, str]], over_mcp: list[tuple[str, str]]) -> None:
    assert [kind for kind, _ in direct] == [kind for _, _, kind in CALLS]
    assert [kind for kind, _ in over_mcp] == [kind for kind, _ in direct]
    assert [o for i, o in enumerate(over_mcp) if i not in CHECKED_FIRST] == [
        o for i, o in enumerate(direct) if i not in CHECKED_FIRST
    ]


@pytest.fixture
def desktop(kernel):
    desktop = FakeDesktop()
    bridge = kernel.resolve(BrowserBridge)
    bridge.connect(desktop)
    yield desktop
    bridge.disconnect(desktop)


def test_one_table_of_calls_has_the_same_outcome_in_process_and_over_the_chat_pipe(kernel, ran, desktop):
    session = open_chat(kernel)
    desktop.seed(session, TAB)
    direct = direct_outcomes(kernel, session)
    direct_ran = list(ran)
    proposals = [(call.tool, call.arguments, call.command) for call in kernel.resolve(ActionGate).calls]
    over_mcp = mcp_outcomes(kernel, session)
    same(direct, over_mcp)
    assert ran == direct_ran * 2  # only the reads ran, on both paths
    assert direct_ran == ["owner"]
    assert [(c.tool, c.arguments, c.command) for c in kernel.resolve(ActionGate).calls] == proposals * 2
    # An undeclared argument never reaches the operation or the proposal: not a forged proof, not an injected name.
    assert proposals[1] == (
        "notes_publish",
        {"title": "Week 44", "pages": 1},
        "hz notes publish --title 'Week 44' --pages 1",
    )
    assert proposals[2][1] == {"tags": ["a", "b"]}
    assert [call[1] for call in proposals[3:6]] == [{"text": '["a"]'}, {"text": '{"a": 1}'}, {"text": "null"}]
    assert direct[3][1].startswith("Invalid arguments for notes_publish: title")
    owner, who = direct[0][1], direct[7][1]
    assert json.loads(owner) == {"name": "Mira", "email": "[email]"}
    assert json.loads(who) == "mcp-stdio:private:read"
    assert "mira" not in direct[6][1]
    assert "KeyError" in direct[6][1]
    assert direct[5][1] == "No access for [email]"
    assert direct[8][1].startswith('"[email] ')
    assert direct[8][1].endswith("…(cut)")
    assert len(direct[8][1]) == RESULT_LIMIT + len("…(cut)")
    assert direct[10][1] == "This confirmation is limited to the MCP chat session"
    # The browser rows: only the ordinary click reached the page, once on each path; the commits wait.
    assert desktop.acted() == ["click", "click"]
    assert [command for _, _, command in proposals[6:]] == [
        f'Click button "Send" on shop.example.org (browser tab {TAB}). {QUOTED_NOTICE}',
        f'Press Enter in textbox "Message" on shop.example.org (browser tab {TAB}). {QUOTED_NOTICE}',
    ]
    assert "approved" not in proposals[6][1]
    assert direct[17][1] == "The page changed since this target was described; read it again."
    assert "ask the person to take over the tab" in direct[20][1]


def test_the_table_holds_with_the_chat_s_own_gate_too(make_kernel, ran):
    kernel = make_kernel(builtins=True, notes=notes(ran))  # no fake: the chat module's ActionGate keeps proposals
    kernel.bind(FoundMcpServers, NoServers())
    service = kernel.resolve(ChatService)
    session = service.open(None).id
    desktop = FakeDesktop()  # the table's browser rows need a desktop, as in the first table test
    bridge = kernel.resolve(BrowserBridge)
    bridge.connect(desktop)
    desktop.seed(session, TAB)
    try:
        same(direct_outcomes(kernel, session), mcp_outcomes(kernel, session))
    finally:
        bridge.disconnect(desktop)
    assert ran == ["owner", "owner"]
    proposed = sum(kind == "proposed" for _, _, kind in CALLS)
    assert len(service._store.load(session).actions) == 2 * proposed


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


def test_a_hand_built_tool_keeps_the_policy_of_the_selected_ones(kernel):
    items = {item.name: item for item in exposed(kernel, mcp.HEAD)}
    session = open_chat(kernel)
    with pytest.raises(ChatToolError, match="reads private data"):
        ChatTool(kernel, items["notes_personal"], session).call({})
    schedule = next(item for name, item in items.items() if name == "heinzel_schedule_control")
    assert "approval_proof" not in {p.name for p in ChatTool(kernel, schedule, session).item.parameters}


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
    # Nobody is known to watch this turn, and the pipe cannot say who does: nothing in its command claims it,
    # and the tools its child serves are unattended. An agent's pipe never counts as the attended person.
    assert tools.hz_tools.attended is False
    assert not any("attended" in word for word in command)
    assert {tool.attended for tool in chat_tools(kernel, session)} == {False}
    assert {tool.attended for tool in direct_tools(kernel, tools)[1]} == {False}


@pytest.mark.parametrize(
    ("caller", "attended"),
    [
        (Caller("person", "web-session", attended=True), True),
        (Caller("person", "cli", attended=True), True),
        (Caller("person", "web-session"), False),  # a person nobody said is watching
        (Caller("daemon", "tick"), False),
        (Caller("local-agent", "mcp-stdio"), False),
        (Caller("token", "k1", attended=True), False),  # an agent is never the watching person, whatever it carries
        (Caller("heinzel", "s1", session="s1", attended=True), False),
        (None, False),
    ],
)
def test_a_turn_is_attended_only_when_a_person_started_it(kernel, caller, attended):
    seen: list[ToolProfile] = []

    class Runner:
        def run(self, prompt, *, contract, tools: ToolProfile, timeout: timedelta, budget: Budget, **_):
            seen.append(tools)
            return AgentResult(runner="cli", status="completed", draft="ok", duration_seconds=0.1)

    service = kernel.resolve(ChatService)
    session = service.open(None).id
    with calling(caller):
        service.send(Runner(), session, "Hello", budget_usd=1.0)
    [tools] = seen
    assert tools.hz_tools.attended is attended
    assert {tool.attended for tool in direct_tools(kernel, tools)[1]} == {attended}
    # The pipe of the same turn stays unattended in every case.
    assert {tool.attended for tool in chat_tools(kernel, session)} == {False}
