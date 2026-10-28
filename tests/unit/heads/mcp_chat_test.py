"""The MCP head for one chat (ADR 0045): the full tool surface, reads run, every other call only proposes."""

import json
import os
import pty
import re
import select
import threading
import time

import anyio
import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from heinzel import ActionGate, CurrentUser, HeinzelError, Module
from heinzel.heads import mcp
from heinzel.heads._exposure import exposed
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

    @module.operation
    def authenticated_read(current: CurrentUser) -> str:
        """A read that needs the local stdio identity but not write permission."""
        identity = current.context()
        permissions = ",".join(sorted(identity.permissions))
        return f"{identity.authentication}:{permissions}"

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
    return {item.name: mcp.chat_tool(kernel, item, session, local=True) for item in exposed(kernel, mcp.HEAD)}


def open_chat(kernel) -> str:
    return kernel.resolve(ChatService).open(None).id


def test_a_chat_asked_for_all_tools_gets_every_tool_the_mcp_head_has_and_nothing_it_hides(kernel):
    ordinary = exposed(kernel, mcp.HEAD)
    names = {item.name for item in ordinary if not item.operation.private}
    private = {item.name for item in ordinary if item.operation.private}
    assert {"notes_personal_read", "notes_personal_change"} <= private

    async def listed_tools(tools: str) -> set[str]:
        async with Client(mcp.build(kernel, chat=open_chat(kernel), chat_tools=tools)) as client:  # type: ignore[arg-type]
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
    assert {name for name in listed if name.startswith("system_runner_chat")} == {
        "system_runner_chat_confirm_authorized_mcp"
    }  # it can consume a human grant, never confirm/authorize directly


def test_chat_read_uses_only_the_local_stdio_identity(kernel):
    assert json.loads(tools(kernel)["notes_authenticated_read"]()) == "mcp-stdio:private:read"

    async def call_and_read_schema():
        async with Client(mcp.build(kernel, chat=open_chat(kernel), chat_tools="all")) as client:
            tool = next(tool for tool in (await client.list_tools()).tools if tool.name == "notes_authenticated_read")
            result = await client.call_tool("notes_authenticated_read", {})
            return tool, result.content[0].text

    tool, result = anyio.run(call_and_read_schema)
    assert json.loads(result) == "mcp-stdio:private:read"
    assert "current" not in tool.input_schema.get("properties", {})
    with pytest.raises(HeinzelError, match="requires an authenticated private API"):
        kernel.resolve(CurrentUser).context()


def test_schedule_approval_reads_the_fresh_challenge_from_the_controlling_terminal(monkeypatch):
    master, slave = pty.openpty()
    real_open = os.open
    monkeypatch.setattr(
        mcp.os,
        "open",
        lambda path, flags, *args, **kwargs: (
            os.dup(slave) if path == "/dev/tty" else real_open(path, flags, *args, **kwargs)
        ),
    )
    arguments = {
        "session_id": "hz-01M62DMG48VTMF92SK1KN5QZWN",
        "request_id": "mcp-owner-approval",
        "expected_revision": 1,
        "action": "activate",
        "expected_contract_version": 1,
        "expected_preview_hash": "a" * 64,
    }
    result: list[Exception] = []

    def confirm() -> None:
        try:
            mcp._confirm_schedule_on_tty(arguments)
        except HeinzelError as exc:
            result.append(exc)
        else:
            result.append(None)

    worker = threading.Thread(target=confirm)
    worker.start()
    prompt = bytearray()
    deadline = time.monotonic() + 2
    while b"> " not in prompt:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([master], [], [], max(0, remaining))[0]:
            pytest.fail("MCP did not display its terminal approval prompt")
        prompt.extend(os.read(master, 4096))
    shown = prompt.decode("utf-8")
    assert arguments["session_id"] in shown
    assert arguments["expected_preview_hash"] in shown
    match = re.search(r"Type exactly: ([^\r\n]+)", shown)
    assert match is not None
    os.write(master, f"{match.group(1)}\n".encode())
    worker.join(timeout=2)
    os.close(master)
    os.close(slave)
    assert not worker.is_alive()
    assert result == [None]


def test_schedule_approval_rejects_a_wrong_terminal_challenge_and_missing_tty(monkeypatch):
    master, slave = pty.openpty()
    real_open = os.open
    monkeypatch.setattr(
        mcp.os,
        "open",
        lambda path, flags, *args, **kwargs: (
            os.dup(slave) if path == "/dev/tty" else real_open(path, flags, *args, **kwargs)
        ),
    )
    arguments = {"session_id": "hz-01M62DMG48VTMF92SK1KN5QZWN", "request_id": "wrong", "action": "activate"}
    errors: list[Exception] = []

    def decline() -> None:
        try:
            mcp._confirm_schedule_on_tty(arguments)
        except HeinzelError as exc:
            errors.append(exc)
        else:
            errors.append(HeinzelError("unexpected approval"))

    worker = threading.Thread(target=decline)
    worker.start()
    prompt = bytearray()
    deadline = time.monotonic() + 2
    while b"> " not in prompt:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([master], [], [], max(0, remaining))[0]:
            pytest.fail("MCP did not display its terminal approval prompt")
        prompt.extend(os.read(master, 4096))
    os.write(master, b"no\n")
    worker.join(timeout=2)
    os.close(master)
    os.close(slave)
    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], HeinzelError)
    assert "not confirmed" in str(errors[0])

    monkeypatch.setattr(mcp.os, "open", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("no tty")))
    with pytest.raises(HeinzelError, match="controlling terminal"):
        mcp._confirm_schedule_on_tty(arguments)


def test_chat_server_fails_closed_for_non_stdio_transport_before_exposing_or_invoking_tools(kernel, ran):
    gate = kernel.resolve(ActionGate)
    with pytest.raises(HeinzelError, match="served over stdio only"):
        mcp.build(kernel, "streamable-http", chat=open_chat(kernel))
    # No server or privileged confirmation consumer is returned for model callers on the unauthenticated port.
    assert gate.calls == []
    assert ran == []


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
