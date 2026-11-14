"""A Heinzel's own turn calls its tools in-process, and what waits goes to its actions chat (ADR 0162, G1 F5)."""

import re

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from heinzel import CurrentUser, HeinzelError, Module, ProposedCall
from heinzel.kernel import ChatToolError, chat_tools
from heinzel.modules.heinzel._direct_tools import activity_name, turn_toolbox
from heinzel.modules.system.runner.chat._service import ChatService

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
