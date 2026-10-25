"""The MCP head for one chat (ADR 0045): the full tool surface, reads run, every other call only proposes."""

import json

import anyio
import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from heinzel import ActionGate, HeinzelError, Module
from heinzel.heads import mcp
from heinzel.heads._exposure import exposed

pytestmark = pytest.mark.unit


class Gate(ActionGate):
    def __init__(self) -> None:
        self.calls = []

    def propose(self, call):  # type: ignore[override]
        self.calls.append(call)
        return f"a{len(self.calls)}"


def notes(ran: list[str]) -> Module:
    module = Module("notes", "Test-only operations.")

    @module.operation
    def owner() -> dict[str, str]:
        """Show who owns the notes."""
        ran.append("owner")
        return {"name": "Mira", "email": "mira.sato@example.org"}

    @module.operation(read_only=False)
    def publish(title: str, pages: int = 1) -> str:
        """Publish the notes."""
        ran.append("publish")
        return "published"

    @module.operation
    def everything() -> str:
        """Show a lot."""
        return "mira.sato@example.org " + "x" * mcp.RESULT_LIMIT

    @module.operation
    def broken() -> str:
        """Fail, naming someone."""
        msg = "No access for mira.sato@example.org"
        raise HeinzelError(msg)

    @module.operation
    def buggy() -> str:
        """Fail with a bug."""
        msg = "secret mira.sato@example.org"
        raise KeyError(msg)

    @module.operation(heads={"cli", "web"})
    def private() -> str:
        """Not for agents."""
        return "private"

    @module.operation(private=True, audience="everyday")
    def personal_read() -> str:
        """Private data is granted by the durable engine, never ambient chat tools."""
        ran.append("personal-read")
        return "private"

    @module.operation(private=True, read_only=False, audience="everyday")
    def personal_change() -> str:
        """A private mutation is not an ambient chat tool either."""
        ran.append("personal-change")
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


def tools(kernel, session="0123456789abcdef"):
    return {item.name: mcp.chat_tool(kernel, item, session) for item in exposed(kernel, mcp.HEAD)}


def test_a_chat_asked_for_all_tools_gets_every_tool_the_mcp_head_has_and_nothing_it_hides(kernel):
    ordinary = exposed(kernel, mcp.HEAD)
    names = {item.name for item in ordinary if not item.operation.private}
    private = {item.name for item in ordinary if item.operation.private}
    assert {"notes_personal_read", "notes_personal_change"} <= private

    async def listed_tools(tools: str) -> set[str]:
        async with Client(mcp.build(kernel, chat="0123456789abcdef", chat_tools=tools)) as client:  # type: ignore[arg-type]
            return {tool.name for tool in (await client.list_tools()).tools}

    everyday = anyio.run(listed_tools, "everyday")  # the default: what a short message needs, far cheaper (ADR 0057)
    assert everyday == {
        item.name for item in ordinary if item.operation.audience == "everyday" and not item.operation.private
    }
    listed = anyio.run(listed_tools, "all")
    assert listed == names
    assert not (listed & private)
    assert {"notes_owner", "notes_publish", "system_doctor", "system_config_set"} <= listed
    assert "notes_private" not in listed
    assert "system_ui_navigation" not in listed
    assert "system_ui_navigation" in {item.name for item in exposed(kernel, mcp.HEAD)}
    # Expert mode is off and these are expert operations (the default audience): hidden in help, still tools.
    assert not kernel.settings.expert
    assert all(
        item.operation.audience == "expert"
        for item in ordinary
        if item.name.startswith("notes_") and not item.operation.private
    )
    assert not any(name.startswith("system_runner_chat") for name in listed)  # never its own confirm


def test_a_read_runs_and_returns_json_without_email_addresses(kernel, ran):
    result = tools(kernel)["notes_owner"]()
    assert ran == ["owner"]
    assert json.loads(result) == {"name": "Mira", "email": "[email]"}


def test_a_write_is_refused_and_only_proposed_with_its_exact_arguments(kernel, ran):
    reply = tools(kernel)["notes_publish"](title="Week 43", pages=2)
    assert ran == []  # nothing ran
    [call] = kernel.resolve(ActionGate).calls
    assert (call.session, call.module, call.operation, call.tool) == (
        "0123456789abcdef",
        "notes",
        "publish",
        "notes_publish",
    )
    assert call.arguments == {"title": "Week 43", "pages": 2}
    assert call.command == "hz notes publish --title 'Week 43' --pages 2"
    assert "Not run" in reply
    assert "(action a1)" in reply


def test_a_write_that_also_spends_is_only_proposed_too(kernel):
    tools(kernel)["system_config_set"](key="system.runner.ask_max_usd", value="100")
    [call] = kernel.resolve(ActionGate).calls
    assert call.command == "hz system config set --key system.runner.ask_max_usd --value 100"


def test_the_chat_server_never_listens_on_a_port():
    with pytest.raises(HeinzelError, match="over stdio only"):
        mcp.serve("streamable-http", chat="0123456789abcdef")


def test_a_cut_result_is_redacted_first_and_says_it_was_cut(kernel):
    result = tools(kernel)["notes_everything"]()
    assert result.startswith('"[email] ')
    assert result.endswith("…(cut)")
    assert len(result) == mcp.RESULT_LIMIT + len("…(cut)")


def test_errors_reach_the_agent_without_email_addresses(kernel):
    with pytest.raises(ToolError, match=r"No access for \[email\]"):
        tools(kernel)["notes_broken"]()
    with pytest.raises(ToolError) as bug:
        tools(kernel)["notes_buggy"]()
    assert "mira" not in str(bug.value)
    assert "KeyError" in str(bug.value)


def test_a_refused_proposal_is_a_tool_error(kernel):
    class Refusing(ActionGate):
        def propose(self, call):  # type: ignore[override]
            msg = "This chat has 100 proposed actions, the most it takes; tell mira@example.org."
            raise HeinzelError(msg)

    kernel.bind(ActionGate, Refusing())
    with pytest.raises(ToolError, match=r"the most it takes; tell \[email\]"):
        tools(kernel)["notes_publish"](title="Week 43")
