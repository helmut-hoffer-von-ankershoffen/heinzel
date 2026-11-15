"""A Heinzel's own turn calls its tools in-process, and what waits goes to its actions chat (ADR 0162, G1 F5)."""

import asyncio
import os
import re
from types import SimpleNamespace

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

import heinzel.kernel._chat_tools as chat_tools_module
from heinzel import CurrentUser, HeinzelError, Module, ProposedCall
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


def test_a_direct_write_runs_for_its_own_heinzel_only(kernel, ran):
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
    assert turn_toolbox(kernel, HEINZEL, "u-" + "0" * 32, attended=True) is None
    assert turn_toolbox(make_kernel(desk=desk(ran)), HEINZEL, owner, attended=True) is None


def test_the_turn_records_each_call_by_name_and_outcome_and_holds_its_chat(kernel, owner, ran):
    box = turn_toolbox(kernel, HEINZEL, owner, attended=True)
    assert box is not None
    assert box.actions == actions_chat(kernel)
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
        result = Agent(FunctionModel(model), tools=tools).run_sync("Look, then publish")
    assert result.output == "Done."
    assert ran == ["look"]
    assert seen == [
        (1, "hz:desk-look", "requested"),
        (1, "hz:desk-look", "completed"),
        (2, "hz:desk-publish", "requested"),
        (2, "hz:desk-publish", "denied"),  # no title: refused by the policy, nothing ran
    ]
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


def test_a_browser_input_takes_the_budget_and_leaves_one_audit_line_each_way(kernel, desktop, audited, monkeypatch):
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
        msg = "This agent used its write budget for the minute"
        raise HeinzelError(msg)

    monkeypatch.setattr(WriteBudget, "take", spent)
    with pytest.raises(ChatToolError, match="write budget"):
        click.call({"tab": TAB, "ref": "ref_1"})
    assert [line["outcome"] for line in audited] == ["ran", "proposed", "refused", "over-budget"]
    assert {line["caller"] for line in audited[:3]} == {f"heinzel:{HEINZEL}"}
    assert {line["policy"] for line in audited[:2]} == {"direct"}
    assert [command.actor for command in desktop.commands] == [HEINZEL] * len(desktop.commands)
    assert desktop.acted() == ["click"]  # the commit and both refusals never acted


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
        ("answer", Caller("person", "web-session"), True),
        ("answer", None, False),  # nobody established who calls: never attended
        ("answer", Caller("local-agent", "mcp-stdio"), False),
        ("scheduled", Caller("person", "web-session"), False),  # the schedule's dispatch shape
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
