"""A Heinzel's own turn calls its tools in-process, and what waits goes to its actions chat (ADR 0162, G1 F5)."""

import asyncio
import json
import os
import re
from types import SimpleNamespace

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

import heinzel.kernel._chat_tools as chat_tools_module
from heinzel import ActionGate, CurrentUser, HeinzelError, Module, ProposedCall
from heinzel.heads import mcp
from heinzel.kernel import ChatToolError, chat_tools, found_tools
from heinzel.kernel._agent_writes import AgentWriteLog, Caller, WriteBudget, calling
from heinzel.kernel._browser import BrowserBridge
from heinzel.kernel._chat_services import FoundMcpServers, FoundMcpTool
from heinzel.modules.heinzel._direct_tools import activity_name, turn_toolbox
from heinzel.modules.heinzel._user_chat import UserChatService
from heinzel.modules.system.runner.chat._service import ChatService
from heinzel.modules.ui.browser._relay import NO_TURN
from heinzel.modules.ui.browser._relay import handle as relay_handle
from tests._fake_browser import FakeDesktop

pytestmark = pytest.mark.unit

HEINZEL = "hz-0123456789ABCDEFGHJKMNPQRS"
OTHER = "hz-ZZZZZZZZZZZZZZZZZZZZZZZZZZ"
ACTIVITY = re.compile(r"[a-z][a-z0-9_.-]{0,127}:[a-z][a-z0-9-]{0,127}")
"""The names the turn's activity accepts (`_turn_activity.py`); any other fails the whole projection."""


def desk(ran: list[str]) -> Module:
    module = Module("desk", "Test-only operations for a Heinzel's turn.")

    @module.operation(audience="everyday")
    def look() -> str:
        """Look at the desk."""
        ran.append("look")
        return "tidy"

    @module.operation(read_only=False, audience="everyday")
    def publish(title: str) -> str:
        """Publish a note; no marker, so it is proposed."""
        ran.append("publish")
        return "published"

    @module.operation(read_only=False, audience="everyday", agents="direct")
    def pin(session_id: str) -> str:
        """Pin the session's note; direct for agents."""
        ran.append("pin")
        return "pinned"

    return module


@pytest.fixture
def ran() -> list[str]:
    return []


@pytest.fixture
def kernel(make_kernel, ran):
    return make_kernel(builtins=True, desk=desk(ran))


@pytest.fixture
def owner(kernel) -> str:
    return kernel.resolve(CurrentUser).owner().id


def store(kernel):
    return kernel.resolve(ChatService)._store


def actions_chat(kernel) -> str:
    with kernel.resolve(CurrentUser).local_read("cli"):
        return store(kernel).heinzel_actions(HEINZEL)


def tool(kernel, actions: str, name: str):
    return next(item for item in chat_tools(kernel, actions, attended=True) if item.name == name)


def test_the_actions_chat_is_bound_to_the_owner_made_once_and_never_the_check_in(kernel):
    first = actions_chat(kernel)
    assert actions_chat(kernel) == first
    with kernel.resolve(CurrentUser).local_read("cli"):
        chats = store(kernel)
        chat = chats.load(first)
        assert (chat.context.kind, chat.context.ref, chat.personal_session_id) == ("heinzel", HEINZEL, HEINZEL)
        assert chats.heinzel_of(first) == HEINZEL
        assert chats.heinzel_of(kernel.resolve(ChatService).open(None).id) is None
        assert chats.find_personal_chat(HEINZEL) is None  # the check-in flow still finds only its own chat
        assert [chat.id for chat in chats.heinzel_actions_chats(HEINZEL)] == [first]
        assert chats.heinzel_actions_chats(OTHER) == []
        with pytest.raises(HeinzelError, match="not a Heinzel"):
            chats.heinzel_actions("../elsewhere")


def test_the_actions_chat_never_holds_a_review_that_has_its_own_surface(kernel):
    actions = actions_chat(kernel)
    call = ProposedCall(
        session=actions,
        module="heinzel",
        operation="schedule-control",
        tool="heinzel_schedule_control",
        arguments={"session_id": HEINZEL, "action": "activate"},
        command="hz heinzel schedule-control",
    )
    with kernel.resolve(CurrentUser).local_read("cli"), pytest.raises(HeinzelError, match="its own"):
        store(kernel).propose(call)


def test_the_tools_call_as_the_heinzel_named_by_the_chat_s_binding(kernel):
    actions = actions_chat(kernel)
    publish = tool(kernel, actions, "desk_publish")
    assert (publish.caller.kind, publish.caller.id, publish.caller.session) == ("heinzel", HEINZEL, HEINZEL)
    assert publish.caller.attended
    names = {item.name for item in chat_tools(kernel, actions)}
    assert "system_runner_chat_confirm_authorized_mcp" not in names  # its proposals are confirmed on the card only


class Allowed(AgentWriteLog):
    """The owner allowed the test module's direct writes, as a plugin's need (`hz system write allow`)."""

    def acknowledged(self, distribution: str, module: str, operation: str) -> bool:
        return True


def test_a_direct_write_runs_for_its_own_heinzel_only(kernel, ran):
    kernel.bind(AgentWriteLog, Allowed())
    actions = actions_chat(kernel)
    pin = tool(kernel, actions, "desk_pin")
    assert "pinned" in pin.call({"session_id": HEINZEL})
    with pytest.raises(ChatToolError, match="only for its own session"):
        pin.call({"session_id": OTHER})
    assert ran == ["pin"]


def test_a_proposal_waits_in_the_actions_chat_and_runs_on_the_person_s_yes(kernel, ran):
    actions = actions_chat(kernel)
    text = tool(kernel, actions, "desk_publish").call({"title": "Week 43"})
    assert str(text).startswith("Not run: `hz desk publish --title 'Week 43'`")
    assert ran == []
    user = kernel.resolve(CurrentUser)
    with user.local("cli"):
        [action] = store(kernel).load(actions).actions
        assert (action.module, action.operation, action.status) == ("desk", "publish", "pending")
        confirmed = kernel.resolve(ChatService).confirm(actions, action.id)
    assert ran == ["publish"]
    assert confirmed.actions[0].status == "done"


def test_no_tools_for_another_owner_or_without_the_chat_module(make_kernel, ran, kernel, owner):
    servers = LazyServers()
    kernel.bind(FoundMcpServers, servers)
    assert turn_toolbox(kernel, HEINZEL, "u-" + "0" * 32, attended=True) is None
    assert (servers.directories, servers.listings, servers.called) == (0, 0, [])
    assert turn_toolbox(make_kernel(desk=desk(ran)), HEINZEL, owner, attended=True) is None


def test_the_turn_records_each_call_by_name_and_outcome_and_holds_its_chat(kernel, owner, ran):
    box = turn_toolbox(kernel, HEINZEL, owner, attended=True)
    assert box is not None
    assert box.actions == actions_chat(kernel)
    measured = []
    box.measure = lambda name, elapsed: measured.append((name, elapsed))
    seen: list[tuple[int, str, str]] = []
    tools = box.pydantic_tools(pictures=False, record=lambda call, name, status: seen.append((call, name, status)))
    calls = [("mcp__hz__desk_look", {}), ("mcp__hz__desk_publish", {})]

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        answered = sum(isinstance(part, ToolReturnPart) for message in messages for part in message.parts)
        if answered < len(calls):
            name, arguments = calls[answered]
            return ModelResponse(parts=[ToolCallPart(name, arguments)])
        return ModelResponse(parts=[TextPart("Done.")])

    with box.answering():
        assert store(kernel).turn_running(box.actions)  # the browser relay accepts this turn's calls
        # Not `run_sync`: it leaves its event loop open, and the loop's unclosed socket fails a later test.
        result = asyncio.run(Agent(FunctionModel(model), tools=tools).run("Look, then publish"))
    assert result.output == "Done."
    assert ran == ["look"]
    assert seen == [
        (1, "hz:desk-look", "requested"),
        (1, "hz:desk-look", "completed"),
        (2, "hz:desk-publish", "requested"),
        (2, "hz:desk-publish", "denied"),  # no title: refused by the policy, nothing ran
    ]
    assert [name for name, _elapsed in measured] == [name for name, _arguments in calls]
    assert all(elapsed >= 0 for _name, elapsed in measured)
    assert not store(kernel).turn_running(box.actions)


@pytest.mark.parametrize(
    ("name", "shown"),
    [
        ("mcp__hz__ui_browser_open", "hz:ui-browser-open"),
        ("mcp__Linear_Tracker__create_issue", "mcp:linear-tracker-create-issue"),
        ("mcp__9x__Ä", "mcp:t-9x"),
        ("mcp__a__", "mcp:a"),
    ],
)
def test_every_activity_name_is_one_the_activity_accepts(name, shown):
    assert activity_name(name) == shown
    assert ACTIVITY.fullmatch(activity_name(name))


# --- review conditions (RUNNER-REVIEW.md C1 to C3, N1 to N3) ---

TAB = "t-0000000a"


@pytest.fixture
def desktop(kernel):
    desktop = FakeDesktop()
    bridge = kernel.resolve(BrowserBridge)
    bridge.connect(desktop)
    yield desktop
    bridge.disconnect(desktop)


@pytest.fixture
def audited(monkeypatch) -> list[dict]:
    lines: list[dict] = []

    def keep(caller, module, operation, arguments, **fields):
        lines.append({"caller": str(caller), "module": module, "operation": operation, **fields})

    monkeypatch.setattr(chat_tools_module, "audit_write", keep)
    return lines


def test_a_browser_input_follows_its_marker_and_leaves_one_audit_line_each_way(kernel, desktop, audited, monkeypatch):
    actions = actions_chat(kernel)
    desktop.seed(HEINZEL, TAB)  # the Heinzel's own tabs, not the actions chat's
    click = tool(kernel, actions, "ui_browser_click")
    assert "Not run" not in str(click.call({"tab": TAB, "ref": "ref_1"}))
    assert str(click.call({"tab": TAB, "ref": "ref_2"})).startswith("Not run: ")  # "Send" is a commit
    monkeypatch.setattr(chat_tools_module, "write_policy", lambda *_args: "approve")
    with pytest.raises(ChatToolError, match="not marked for agents"):
        click.call({"tab": TAB, "ref": "ref_1"})
    monkeypatch.undo()
    monkeypatch.setattr(chat_tools_module, "audit_write", lambda *a, **k: audited.append({"outcome": k["outcome"]}))

    def spent(self, caller):
        msg = "a browser input never draws on the write budget"
        raise AssertionError(msg)

    monkeypatch.setattr(WriteBudget, "take", spent)
    click.call({"tab": TAB, "ref": "ref_1"})  # the browser's own limits count it
    assert [line["outcome"] for line in audited] == ["ran", "proposed", "refused", "ran"]
    assert {line["caller"] for line in audited[:3]} == {f"heinzel:{HEINZEL}"}
    assert {line["policy"] for line in audited[:2]} == {"direct"}
    assert [command.actor for command in desktop.commands] == [HEINZEL] * len(desktop.commands)
    assert desktop.acted() == ["click", "click"]  # the commit and the refusal never acted


class Servers(FoundMcpServers):
    def __init__(self) -> None:
        self.called: list[str] = []

    def tools(self) -> list[FoundMcpTool]:
        return [
            FoundMcpTool("srv", "srv", "send", "mcp__srv__send", "Send a message.", {"type": "object"}, read_only=False)
        ]

    def call(self, server, tool, arguments):
        self.called.append(tool)
        return "sent"


class LazyServers(Servers):
    def __init__(self):
        super().__init__()
        self.enabled = True
        self.listings = 0
        self.directories = 0

    def names(self):
        self.directories += 1
        return ["srv"] if self.enabled else []

    def tools(self):
        self.listings += 1
        return (
            [
                FoundMcpTool("srv", "srv", "read", "mcp__srv__read", "Read data.", {"type": "object"}, read_only=True),
                *super().tools(),
            ]
            if self.enabled
            else []
        )


def test_external_gateway_defers_discovery_and_retains_read_write_policy(kernel, owner):
    servers = LazyServers()
    kernel.bind(FoundMcpServers, servers)
    box = turn_toolbox(kernel, HEINZEL, owner, attended=True)
    assert box is not None
    tools = {tool.name: tool for tool in box.pydantic_tools(pictures=False, record=lambda *_: None)}
    assert servers.listings == 0
    assert "srv" in tools["mcp__external__servers"].function()
    assert servers.listings == 0
    listed = tools["mcp__external__tools"].function(server="srv")
    assert "mcp__srv__read" in listed
    assert "mcp__srv__send" in listed
    assert "untrusted data" in listed
    schema = tools["mcp__external__schema"].function(server="srv", name="mcp__srv__read")
    assert "untrusted data" in schema
    assert "chunk" in schema
    called = tools["mcp__external__call"].function(server="srv", name="mcp__srv__read", arguments={})
    assert "untrusted data" in called
    assert servers.called == ["read"]
    write = tools["mcp__external__call"].function(server="srv", name="mcp__srv__send", arguments={"to": "team"})
    assert write.startswith("Not run:")
    assert servers.called == ["read"]
    with kernel.resolve(CurrentUser).local_read("cli"):
        [action] = store(kernel).load(box.actions).actions
    assert (action.module, action.operation, action.arguments) == ("mcp:srv", "send", {"to": "team"})
    servers.enabled = False
    assert tools["mcp__external__call"].function(server="srv", name="mcp__srv__read", arguments={}).startswith("Error:")
    assert servers.called == ["read"]


@pytest.mark.parametrize(
    ("method", "arguments"),
    [
        ("call", {"server": "srv", "name": "missing", "arguments": {}}),
        ("schema", {"server": "srv", "name": "missing"}),
        *[
            ("call", {"server": "srv", "name": "mcp__srv__send", "arguments": value})
            for value in (None, [], "text", 42)
        ],
        *[
            (method, {"server": value, "name": "mcp__srv__send", "arguments": {}})
            for method in ("tools", "schema", "call")
            for value in (None, [], 42, "../srv", "SRV")
        ],
        *[
            (method, {"server": "srv", "name": value, "arguments": {}})
            for method in ("schema", "call")
            for value in (None, [], 42, "../send", "mcp__other__send")
        ],
        *[
            (method, {"server": "srv", "name": "mcp__srv__send", "offset": value})
            for method in ("servers", "tools", "schema")
            for value in (-1, True, 1.5, "0", None)
        ],
    ],
)
def test_external_gateway_rejects_invalid_requests_without_calls_or_proposals(kernel, owner, method, arguments):
    servers = LazyServers()
    kernel.bind(FoundMcpServers, servers)
    box = turn_toolbox(kernel, HEINZEL, owner, attended=True)
    assert box is not None
    tools = {tool.name: tool for tool in box.pydantic_tools(pictures=False, record=lambda *_: None)}
    assert tools[f"mcp__external__{method}"].function(**arguments).startswith("Error:")
    assert servers.called == []
    with kernel.resolve(CurrentUser).local_read("cli"):
        assert store(kernel).load(box.actions).actions == []


@pytest.mark.parametrize("method", ["servers", "tools", "schema"])
@pytest.mark.parametrize("offset", [-1, True, 1.5, "0", None])
@pytest.mark.parametrize("compatibility", [False, True])
def test_external_invalid_offsets_never_discover(kernel, owner, method, offset, compatibility):
    class NoDiscovery(FoundMcpServers):
        def tools(self):
            raise AssertionError

        def names(self):
            if compatibility:
                return super().names()
            raise AssertionError

        def tools_for(self, namespace):
            raise AssertionError

    kernel.bind(FoundMcpServers, NoDiscovery())
    box = turn_toolbox(kernel, HEINZEL, owner, attended=True)
    assert box is not None
    tools = {tool.name: tool for tool in box.pydantic_tools(pictures=False, record=lambda *_: None)}
    result = tools[f"mcp__external__{method}"].function(server="srv", name="mcp__srv__read", offset=offset)
    assert result == "Error: offset must be a nonnegative integer."


def page_data(result):
    return json.loads(result.split("<untrusted-data>\n", 1)[1].split("\n</untrusted-data>", 1)[0])


def test_external_directory_pagination_lists_every_server_and_tool_once(kernel, owner):
    class ManyServers(LazyServers):
        def names(self):
            return [f"srv{index}" for index in range(12)]

        def tools_for(self, namespace):
            self.listings += 1
            return [
                FoundMcpTool(
                    namespace,
                    namespace,
                    f"read{index}",
                    f"mcp__{namespace}__read{index}",
                    "Read.",
                    {"type": "object"},
                    read_only=True,
                )
                for index in range(13)
            ]

    servers = ManyServers()
    kernel.bind(FoundMcpServers, servers)
    box = turn_toolbox(kernel, HEINZEL, owner, attended=True)
    assert box is not None
    tools = {tool.name: tool for tool in box.pydantic_tools(pictures=False, record=lambda *_: None)}
    for method, arguments, expected in (
        ("servers", {}, servers.names()),
        ("tools", {"server": "srv0"}, [f"mcp__srv0__read{index}" for index in range(13)]),
    ):
        items, offset, offsets = [], 0, []
        while offset is not None:
            assert offset not in offsets
            offsets.append(offset)
            page = page_data(tools[f"mcp__external__{method}"].function(**arguments, offset=offset))
            assert 0 < len(page["items"]) <= 5
            items.extend(page["items"])
            offset = page["next_offset"]
        assert offsets == [0, 5, 10]
        assert [item["name"] if isinstance(item, dict) else item for item in items] == expected
        if method == "servers":
            assert servers.listings == 0
    assert servers.called == []
    with kernel.resolve(CurrentUser).local_read("cli"):
        assert store(kernel).load(box.actions).actions == []


def test_namespace_filtered_gateway_refuses_both_duplicate_tool_titles(kernel, owner):
    class CollidingServers(LazyServers):
        def tools(self):
            return [
                FoundMcpTool("srv", "srv", "first", "mcp__srv__READ", "First.", {"type": "object"}, read_only=True),
                FoundMcpTool("srv", "srv", "second", "mcp__srv__read", "Second.", {"type": "object"}, read_only=False),
                *super().tools(),
            ]

    servers = CollidingServers()
    kernel.bind(FoundMcpServers, servers)
    box = turn_toolbox(kernel, HEINZEL, owner, attended=True)
    assert box is not None
    assert [tool.name for tool in found_tools(kernel, box.actions, namespace="srv")] == ["mcp__srv__send"]
    assert box.external is not None
    assert [item["name"] for item in page_data(box.external.tools("srv"))["items"]] == ["mcp__srv__send"]
    for name in ("mcp__srv__READ", "mcp__srv__read"):
        assert box.external.schema("srv", name).startswith("Error:")
        assert box.external.call("srv", name, {}).startswith("Error:")
    assert servers.called == []
    with kernel.resolve(CurrentUser).local_read("cli"):
        assert store(kernel).load(box.actions).actions == []


def test_model_discovers_and_calls_external_tool_on_demand(kernel, owner):
    servers = LazyServers()
    kernel.bind(FoundMcpServers, servers)
    box = turn_toolbox(kernel, HEINZEL, owner, attended=True)
    assert box is not None
    tools = box.pydantic_tools(pictures=False, record=lambda *_: None)
    calls = [
        ("mcp__external__servers", {}),
        ("mcp__external__tools", {"server": "srv"}),
        ("mcp__external__schema", {"server": "srv", "name": "mcp__srv__read"}),
        ("mcp__external__call", {"server": "srv", "name": "mcp__srv__read", "arguments": {}}),
    ]

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        answered = sum(isinstance(part, ToolReturnPart) for message in messages for part in message.parts)
        if answered < len(calls):
            name, arguments = calls[answered]
            assert name in {tool.name for tool in info.function_tools}
            return ModelResponse(parts=[ToolCallPart(name, arguments)])
        return ModelResponse(parts=[TextPart("Done.")])

    with box.answering():
        assert asyncio.run(Agent(FunctionModel(model), tools=tools).run("Read the external data")).output == "Done."
    assert servers.called == ["read"]


def test_gateway_diagnostics_use_existing_authorized_discovery_only(kernel, owner):
    from heinzel.modules.heinzel._chat_diagnostics import prepare_toolbox

    servers = LazyServers()
    kernel.bind(FoundMcpServers, servers)
    events = []
    box, diagnostic = asyncio.run(
        prepare_toolbox(kernel, HEINZEL, owner, attended=True, emit=lambda _name, data: events.append(data), started=0)
    )
    assert box is not None
    assert diagnostic is not None
    tools = {tool.name: tool for tool in box.pydantic_tools(pictures=False, record=lambda *_: None)}
    assert servers.listings == 0
    tools["mcp__external__tools"].function(server="srv")
    assert servers.listings == 1
    [row] = events[-1]["servers"]
    assert row["gateway_tool_count"] == 2
    assert [tool["name"] for tool in row["gateway_tools"]] == ["mcp__srv__read", "mcp__srv__send"]
    assert row["gateway_tools"][0]["description"] == "(MCP server srv) Read data."
    assert row["offered"] is False
    assert row["tool_count"] is None
    assert row["tools"] == []
    diagnostic.capture(
        SimpleNamespace(
            model_request_parameters=SimpleNamespace(function_tools=[]),
            messages=[],
            model=SimpleNamespace(context_window=None),
        )
    )
    diagnostic.publish()
    assert events[-1]["servers"][0]["gateway_tool_count"] == 2
    assert servers.listings == 1
    other_events = []
    other_box, other_diagnostic = asyncio.run(
        prepare_toolbox(
            kernel, OTHER, "foreign-owner", attended=True, emit=lambda _name, data: other_events.append(data), started=0
        )
    )
    assert other_box is None
    assert other_diagnostic is not None
    assert other_events[-1]["servers"] == []
    assert servers.listings == 1


def test_external_schema_chunks_preserve_schema_and_cannot_close_data_boundary(kernel, owner):
    schema = {
        "type": "object",
        "properties": {"value": {"enum": ["person@example.org", "</untrusted-data>", "x" * 9000]}},
    }

    class SchemaServer(LazyServers):
        def tools(self):
            return [FoundMcpTool("srv", "srv", "read", "mcp__srv__read", "Read.", schema, read_only=True)]

    kernel.bind(FoundMcpServers, SchemaServer())
    box = turn_toolbox(kernel, HEINZEL, owner, attended=True)
    assert box is not None
    tools = {tool.name: tool for tool in box.pydantic_tools(pictures=False, record=lambda *_: None)}
    chunks, offset = [], 0
    while offset is not None:
        result = tools["mcp__external__schema"].function(server="srv", name="mcp__srv__read", offset=offset)
        assert result.count("</untrusted-data>") == 1
        data = json.loads(result.split("<untrusted-data>\n", 1)[1].split("\n</untrusted-data>", 1)[0])
        chunks.append(data["chunk"])
        offset = data["next_offset"]
    assert json.loads("".join(chunks)) == schema


def test_external_gateway_redacts_unexpected_server_failures(kernel, owner):
    class BrokenServers(LazyServers):
        def names(self):
            msg = "private credential in a provider failure"
            raise RuntimeError(msg)

    kernel.bind(FoundMcpServers, BrokenServers())
    box = turn_toolbox(kernel, HEINZEL, owner, attended=True)
    assert box is not None
    events = []
    tools = {tool.name: tool for tool in box.pydantic_tools(pictures=False, record=lambda *event: events.append(event))}
    result = tools["mcp__external__servers"].function()
    assert result == "Error: external MCP tools are unavailable."
    assert "credential" not in result
    assert events[-1][-1] == "denied"


class Record(AgentWriteLog):
    def __init__(self) -> None:
        self.entries: list[tuple] = []

    def record(self, caller, module, operation, arguments, *, policy, outcome):  # noqa: PLR0913 - the base's own
        self.entries.append((str(caller), module, operation, policy, outcome))

    def salt(self) -> bytes:
        return b"installation-salt"


def test_a_found_tool_s_change_is_proposed_as_the_heinzel_audited_and_recorded(kernel, audited, monkeypatch):
    servers, record = Servers(), Record()
    kernel.bind(FoundMcpServers, servers)
    kernel.bind(AgentWriteLog, record)
    salts: list[bytes] = []
    digest = chat_tools_module.arguments_digest
    monkeypatch.setattr(chat_tools_module, "arguments_digest", lambda a, salt: salts.append(salt) or digest(a, salt))
    actions = actions_chat(kernel)
    [send] = found_tools(kernel, actions, attended=True)
    assert str(send.call({"to": "team"})).startswith("Not run: ")
    assert servers.called == []
    assert salts == [b"installation-salt"]  # the mcp.found.call step's digest is salted (C1)
    assert [(line["caller"], line["module"], line["outcome"]) for line in audited] == [
        (f"heinzel:{HEINZEL}", "mcp:srv", "proposed")
    ]
    assert audited[0]["salt"] == b"installation-salt"
    assert record.entries == [(f"heinzel:{HEINZEL}", "mcp:srv", "send", "propose", "proposed")]
    with kernel.resolve(CurrentUser).local_read("cli"):
        [action] = store(kernel).load(actions).actions
    assert (action.module, action.operation, action.arguments) == ("mcp:srv", "send", {"to": "team"})


def _service(tools_kernel, seen: dict) -> UserChatService:
    repository = SimpleNamespace(control_read=lambda _session: SimpleNamespace(events=[]), principal_id="u-owner")
    service = UserChatService(SimpleNamespace(repository=repository), None, None, tools_kernel=tools_kernel)  # type: ignore[arg-type]
    service.plugin_selected = lambda _session=None: object()  # type: ignore[method-assign]

    async def plugin_model(*_args):
        return object(), lambda: True, None, object()

    service._plugin_model = plugin_model  # type: ignore[method-assign]
    service._owner = lambda *_args, **_kwargs: None  # type: ignore[method-assign]

    def toolbox(_kernel, session, owner, *, attended):
        seen.update(session=session, owner=owner, attended=attended)
        raise LookupError

    import heinzel.modules.heinzel._direct_tools as direct

    service._patch = (direct, toolbox)  # type: ignore[attr-defined]
    return service


@pytest.mark.parametrize(
    ("way", "caller", "attended"),
    [
        ("answer", Caller("person", "web-session", attended=True), True),
        ("answer", None, False),  # nobody established who calls: never attended
        ("answer", Caller("local-agent", "mcp-stdio"), False),
        ("scheduled", Caller("person", "web-session", attended=True), False),  # the schedule's dispatch shape
    ],
)
def test_only_a_person_s_own_message_is_an_attended_turn(monkeypatch, way, caller, attended):
    seen: dict = {}
    service = _service(object(), seen)
    direct, toolbox = service._patch  # type: ignore[attr-defined]
    monkeypatch.setattr(direct, "turn_toolbox", toolbox)

    async def turn():
        with calling(caller):
            if way == "scheduled":
                return await service.dispatch(HEINZEL, "occurrence", lambda: None, 4, 60, user_proposals=False)
            return await service.answer(HEINZEL, "request")

    with pytest.raises(LookupError):
        asyncio.run(turn())
    assert seen == {"session": HEINZEL, "owner": "u-owner", "attended": attended}


def test_an_answer_from_a_chat_app_is_not_attended(monkeypatch):
    service = _service(None, {})
    forwarded: dict = {}

    async def dispatch(*_args, **kwargs):
        forwarded.update(kwargs)
        raise LookupError

    monkeypatch.setattr(service, "dispatch", dispatch)
    with pytest.raises(LookupError):
        asyncio.run(service.answer_from_chat(HEINZEL, "request"))
    assert forwarded.get("attended", False) is False


def test_the_relay_takes_an_actions_chat_only_while_its_turn_holds_it(kernel, owner, desktop):
    box = turn_toolbox(kernel, HEINZEL, owner, attended=True)
    assert box is not None
    desktop.seed(HEINZEL, TAB)
    assert relay_handle(kernel, "heinzel", box.actions, "ui_browser_read", {"tab": TAB}) == {"error": NO_TURN}
    with box.answering():
        answered = relay_handle(kernel, "heinzel", box.actions, "ui_browser_read", {"tab": TAB})
    assert "text" in answered
    assert desktop.commands[-1].actor == HEINZEL
    assert relay_handle(kernel, "heinzel", box.actions, "ui_browser_read", {"tab": TAB}) == {"error": NO_TURN}


def test_a_turn_starts_without_its_tools_when_a_card_holds_the_chat(kernel, owner):
    box = turn_toolbox(kernel, HEINZEL, owner, attended=True)
    assert box is not None
    with box.answering(), pytest.raises(HeinzelError, match="busy"), box.answering():
        pass  # what `InteractiveTurn.run` catches before `interactive.started`, then runs without the tools


def test_no_pipe_serves_an_actions_chat(kernel):
    with pytest.raises(HeinzelError, match="no pipe"):
        mcp.build(kernel, chat=actions_chat(kernel))


def test_a_failed_call_still_records_its_end(kernel, owner, monkeypatch):
    box = turn_toolbox(kernel, HEINZEL, owner, attended=True)
    assert box is not None
    seen: list[tuple[int, str, str]] = []

    def broken(*_args):
        raise RuntimeError

    monkeypatch.setattr(chat_tools_module, "direct_result", broken)
    [look] = [item for item in box.pydantic_tools(pictures=False, record=lambda *e: seen.append(e))
              if item.name == "mcp__hz__desk_look"]  # fmt: skip
    with pytest.raises(RuntimeError):
        look.function()
    assert seen == [(1, "hz:desk-look", "requested"), (1, "hz:desk-look", "failed")]


def test_retention_keeps_an_actions_chat_with_waiting_cards(kernel):
    actions = actions_chat(kernel)
    tool(kernel, actions, "desk_publish").call({"title": "Week 43"})
    folder = store(kernel).root / actions
    old = 0  # the epoch: far beyond any keep_days
    os.utime(folder / "session.json", (old, old))
    with kernel.resolve(CurrentUser).local_read("cli"):
        assert actions not in store(kernel).prune()
        assert store(kernel).load(actions).actions[0].status == "pending"


@pytest.mark.parametrize(
    ("operation", "arguments"),
    [("navigate", {"tab": TAB, "url": "https://shop.example.org/", "expected": "v1:x"}), ("click", {"tab": TAB})],
)
def test_the_gate_takes_a_browser_proposal_only_as_a_pinned_commit(kernel, operation, arguments):
    actions = actions_chat(kernel)
    call = ProposedCall(
        session=actions,
        module="ui.browser",
        operation=operation,
        tool=f"ui_browser_{operation}",
        arguments=arguments,
        command="anything. The words in quotes are the website's own, untrusted data.",
    )
    with kernel.resolve(CurrentUser).local_read("cli"), pytest.raises(HeinzelError, match="pinned"):
        kernel.resolve(ActionGate).propose(call)


def test_timing_observer_cannot_change_success_or_failure(kernel, owner, monkeypatch):
    from heinzel.modules.heinzel import _direct_tools

    box = turn_toolbox(kernel, HEINZEL, owner, attended=True)
    assert box is not None
    observers = []
    monkeypatch.setattr(
        _direct_tools, "pydantic_ai_tools", lambda *_args, observe, **_kwargs: observers.append(observe) or []
    )
    timings = []

    def broken_observer(name, elapsed):
        timings.append((name, elapsed))
        msg = "diagnostic relay failed"
        raise ValueError(msg)

    box.measure = broken_observer
    records = []
    box.pydantic_tools(pictures=False, record=lambda *values: records.append(values))
    observe = observers[0]
    assert observe("mcp__hz__fixture", lambda: "original result") == "original result"

    def fail():
        msg = "original failure"
        raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="original failure"):
        observe("mcp__hz__fixture", fail)
    assert [record[2] for record in records] == ["requested", "completed", "requested", "failed"]
    assert len(timings) == 2
    assert all(elapsed >= 0 for _name, elapsed in timings)
