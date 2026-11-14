"""The agent write policy per operation (ADR 0162, amends 0045): marker, ceiling, caller, budget, audit, paths."""

import logging
from typing import Annotated

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field

from heinzel import ActionGate, ByArguments, Caller, HeinzelError, Module
from heinzel.heads import mcp
from heinzel.heads._exposure import exposed
from heinzel.kernel import (
    ChatToolError,
    OperationLet,
    WriteBudget,
    agent_writes,
    calling,
    chat_tools,
    current_caller,
    declared_agent_writes,
    restricts,
)
from heinzel.kernel._agent_writes import (
    ATTENDED,
    HUMAN_AUTHENTICATIONS,
    UNATTENDED,
    arguments_digest,
    bound_to_caller,
    person_caller,
    stricter,
    target_of,
)
from heinzel.kernel._chat_tools import write_budget
from heinzel.modules.heinzel import _connected_grants, _connected_read_set_preview, _connected_read_sets
from heinzel.modules.system.bookmark import BookmarkService
from heinzel.modules.system.runner.chat._service import ChatService

pytestmark = pytest.mark.unit

AGENT_HEADS = ("mcp", "mcp-http", "mcp-check-in")
STRICT = {"direct": 0, "propose": 1, "approve": 2, "never": 3}


class Gate(ActionGate):
    def __init__(self) -> None:
        self.calls = []

    def propose(self, call):  # type: ignore[override]
        self.calls.append(call)
        return f"a{len(self.calls)}"


def desk(ran: list[tuple[str, object]]) -> Module:
    module = Module("desk", "Test-only writes, one per marker.")

    @module.operation(read_only=False, audience="everyday", agents="direct")
    def jot(caller: Caller, text: Annotated[str, Field(description="Words.")]) -> dict[str, str]:
        """Write a line."""
        ran.append(("jot", caller))
        return {"text": text, "by": str(caller), "mail": "mira.sato@example.org"}

    @module.operation(read_only=False, audience="everyday", agents="approve")
    def send(to: str) -> str:
        """Send it to someone."""
        ran.append(("send", to))
        return "sent"

    @module.operation(read_only=False, audience="everyday", agents="never")
    def confirm(action: str) -> str:
        """The person's own yes."""
        ran.append(("confirm", action))
        return "confirmed"

    @module.operation(read_only=False, audience="everyday")
    def legacy(text: str) -> str:
        """A write nobody marked."""
        ran.append(("legacy", text))
        return "done"

    @module.operation(
        read_only=False,
        audience="everyday",
        agents=ByArguments("direct", "approve", lambda a: a.get("url") is not None, "when it changes the address"),
    )
    def rename(entry: str, url: str | None = None) -> str:
        """Rename or repoint."""
        ran.append(("rename", entry))
        return "renamed"

    @module.operation(read_only=False, audience="everyday", agents="direct")
    def broken(text: str) -> str:
        """Fail, naming someone."""
        msg = f"No access for mira.sato@example.org to {text}"
        raise HeinzelError(msg)

    return module


@pytest.fixture
def ran() -> list[tuple[str, object]]:
    return []


@pytest.fixture
def kernel(make_kernel, ran, settings, tmp_path):
    owned = settings.model_copy(update={"data_dir": tmp_path / "data", "cache_dir": tmp_path / "cache"})
    kernel = make_kernel(builtins=True, settings_=owned, desk=desk(ran))
    kernel.bind(ActionGate, Gate())
    return kernel


def tool(kernel, name: str, *, attended: bool = True):
    session = kernel.resolve(ChatService).open(None).id
    found = {item.name: item for item in chat_tools(kernel, session, "all", attended=attended)}
    return found[name], session


def operation(kernel, module: str, name: str) -> OperationLet:
    return next(op for record, op in kernel.lets(OperationLet) if record.qualname == module and op.name == name)


def audit_lines(caplog) -> list[dict[str, object]]:
    return [record.__dict__ for record in caplog.records if record.getMessage().startswith("agent.write")]


# --- the marker ---


def test_a_marker_is_for_writes_and_from_the_known_values():
    module = Module("m", "Test.")
    with pytest.raises(ValueError, match="only reads"):

        @module.operation(agents="direct")
        def read() -> None:
            """Read."""

    with pytest.raises(ValueError, match="use one of approve, direct, never, propose"):

        @module.operation(read_only=False, agents="sometimes")  # type: ignore[arg-type]
        def write() -> None:
            """Write."""

    with pytest.raises(ValueError, match="stricter case"):
        ByArguments("approve", "direct", lambda _a: True, "never")
    with pytest.raises(ValueError, match="stricter case"):
        ByArguments("direct", "direct", lambda _a: True, "never")
    with pytest.raises(ValueError, match="is one of"):
        ByArguments("direct", "always", lambda _a: True, "never")  # type: ignore[arg-type]


def test_no_marker_is_propose_and_never_direct(kernel):
    legacy = operation(kernel, "desk", "legacy")
    assert legacy.agents is None
    assert declared_agent_writes(legacy) == frozenset()
    assert agent_writes(legacy, {}, builtin=True) == "propose"
    assert agent_writes(legacy, {}, builtin=False) == "propose"


def test_a_marker_can_depend_on_the_arguments_and_fails_strict(kernel):
    rename = operation(kernel, "desk", "rename")
    assert declared_agent_writes(rename) == {"direct", "approve"}
    assert agent_writes(rename, {"entry": "b-1"}, builtin=True) == "direct"
    assert agent_writes(rename, {"entry": "b-1", "url": "https://example.com/"}, builtin=True) == "approve"

    def explode(_arguments):
        raise KeyError(_arguments)

    module = Module("m", "Test.")

    @module.operation(read_only=False, agents=ByArguments("direct", "never", explode, "always"))
    def fragile() -> None:
        """Write."""

    assert agent_writes(module.lets[0], {}, builtin=True) == "never"
    assert stricter("direct", "approve") == stricter("approve", "direct") == "approve"


@pytest.mark.parametrize("capability", ["mail", "chat", "calendar"])
def test_a_plugin_write_that_reaches_people_is_at_most_approve_whatever_it_declares(capability):
    module = Module("m", "Test.")

    @module.operation(read_only=False, requires=[f"{capability}:write"], agents="direct")
    def send() -> None:
        """Send."""

    @module.operation(read_only=False, requires=[f"{capability}:read,write"], agents="never")
    def forbid() -> None:
        """The person's."""

    send_let, forbid_let = module.lets
    assert agent_writes(send_let, {}, builtin=False) == "approve"
    assert agent_writes(forbid_let, {}, builtin=False) == "never"  # a ceiling never loosens
    assert agent_writes(send_let, {}, builtin=True) == "direct"  # core's own are reviewed with core


def test_a_plugin_may_create_issues_directly():
    module = Module("m", "Test.")

    @module.operation(read_only=False, requires=["tracker:write"], agents="direct")
    def create() -> None:
        """Create an issue."""

    assert agent_writes(module.lets[0], {}, builtin=False) == "direct"


# --- every core write is decided ---


def core_writes(kernel):
    return [(record, op) for record, op in kernel.lets(OperationLet) if not op.read_only and record.origin.builtin]


def test_every_core_write_an_agent_head_offers_has_an_explicit_marker(kernel):
    unmarked = [
        f"{record.qualname} {op.name}"
        for record, op in core_writes(kernel)
        if any(not restricts(op.heads, head) for head in AGENT_HEADS) and not declared_agent_writes(op)
    ]
    assert unmarked == [], "mark each with agents=, as strict as everything it can trigger (ADR 0162)"


def test_the_persons_own_operations_are_never_for_agents(kernel):
    own = {
        ("system.runner.chat", "confirm"),
        ("system.runner.chat", "decline"),
        ("system.runner.chat", "authorize-for-mcp"),
        ("heinzel", "approve-connected-read"),
        ("heinzel", "approve-connected-read-set"),
        ("heinzel", "approve-agent-schedule"),
        ("heinzel", "approve-model-grant"),
        ("heinzel", "accept-agent-contract"),
        ("heinzel", "execute-captured-write"),
        ("heinzel", "deliver-channel-item"),
        ("heinzel", "publish-draft-workflow"),
        ("heinzel", "share-draft-link"),
        ("system.bookmark", "favicon"),
    }
    found = {(r.qualname, op.name): declared_agent_writes(op) for r, op in core_writes(kernel)}
    assert {name: found.get(name) for name in own} == dict.fromkeys(own, frozenset({"never"}))


@pytest.mark.parametrize(
    ("module", "name", "arguments", "policy"),
    [
        ("system.daemon", "tick", {}, "approve"),
        ("heinzel", "wake", {}, "approve"),
        ("heinzel", "prune", {}, "approve"),
        ("heinzel", "new", {"name": "digest"}, "direct"),
        ("heinzel", "new", {"name": "digest", "team": True}, "approve"),
        ("org", "sync", {}, "direct"),
        ("org", "sync", {"accept": "all"}, "approve"),
        ("report", "approved", {}, "approve"),
        ("report", "published", {}, "approve"),
        ("report", "drafted", {}, "direct"),
        ("system.notify", "ack", {}, "approve"),
        ("system.config", "set", {}, "approve"),
        ("system.plugin", "add", {}, "approve"),
        ("system.runner", "ask", {}, "approve"),
        ("system.state", "push", {}, "approve"),
        ("system.state", "reset", {}, "approve"),
        ("team", "add", {}, "approve"),
        ("system", "update", {}, "approve"),
        ("system.theme", "use", {}, "direct"),
        ("system.bookmark", "add", {"url": "https://example.com/"}, "direct"),
        ("system.bookmark", "folder", {"title": "F"}, "direct"),
        ("system.bookmark", "move", {"entry": "b-1"}, "direct"),
        ("system.bookmark", "restore", {}, "direct"),
        ("system.bookmark", "edit", {"entry": "b-1", "title": "T"}, "direct"),
        ("system.bookmark", "edit", {"entry": "b-1", "url": "https://example.org/"}, "approve"),
        ("system.bookmark", "remove", {"entry": "b-1"}, "approve"),
    ],
)
def test_core_policies(kernel, module, name, arguments, policy):
    assert agent_writes(operation(kernel, module, name), arguments, builtin=True) == policy


def test_a_marker_is_at_least_as_strict_as_what_the_operation_can_trigger(kernel):
    """The known chains: a tick runs any job, wake spends like ask, new can publish like the team operations."""

    def strictest(module: str, name: str) -> int:
        return max(STRICT[value] for value in declared_agent_writes(operation(kernel, module, name)))

    triggers = {
        ("system.daemon", "tick"): [("heinzel", "prune"), ("heinzel", "wake"), ("system.state", "push")],
        ("heinzel", "wake"): [("system.runner", "ask")],
        ("heinzel", "new"): [("team", "add")],
        ("org", "sync"): [("system.config", "set")],
    }
    for outer, inner in triggers.items():
        for reached in inner:
            assert strictest(*outer) >= strictest(*reached), f"{outer} can trigger {reached}"


def test_no_agent_authentication_is_a_human_one():
    human = {"cli", "api-bearer", "web-session", "web-device"}
    assert human == HUMAN_AUTHENTICATIONS
    assert human == _connected_grants._HEADS == _connected_read_sets._HEADS
    assert human == _connected_read_set_preview._HUMAN_HEADS
    assert person_caller("mcp-stdio") is None
    assert person_caller("daemon") is None
    assert person_caller(None) is None
    assert person_caller("web-session") == Caller("person", "web-session")


# --- which head offers which bookmark operation ---


def test_each_head_offers_exactly_its_bookmark_operations(kernel):
    def offered(head: str) -> set[str]:
        return {item.path[-1] for item in exposed(kernel, head) if item.path[:2] == ("system", "bookmark")}

    every = {"list", "add", "folder", "edit", "move", "remove", "restore"}
    assert offered("cli") == every
    assert offered("mcp") == every
    assert offered("web") == every | {"favicon"}
    assert offered("mcp-http") == set()  # until head F's token scopes cover it
    assert offered("api") == set()
    session = kernel.resolve(ChatService).open(None).id
    chat = {item.name for item in chat_tools(kernel, session, "all") if item.name.startswith("system_bookmark")}
    assert chat == {f"system_bookmark_{name}" for name in every}


# --- the caller ---


def test_the_caller_is_injected_from_the_head_and_never_an_argument(kernel, ran):
    jot = operation(kernel, "desk", "jot")
    with pytest.raises(HeinzelError, match="needs to know who calls"):
        kernel.invoke(jot, text="x")  # no head said who: no fallback to the person
    with calling(Caller("person", "cli")):
        assert kernel.invoke(jot, text="x")["by"] == "person:cli"
        for forged in (Caller("person", "web-session"), {"kind": "person", "id": "cli"}, "person:cli"):
            with pytest.raises(HeinzelError, match="the caller is never an argument"):
                kernel.invoke(jot, text="x", caller=forged)
        with calling(None), pytest.raises(HeinzelError, match="needs to know who calls"):
            current_caller()
    assert [name for name, _ in ran] == ["jot"]


def test_no_head_lists_the_caller_as_a_parameter(kernel):
    for head in ("cli", "mcp", "web", "mcp-http"):
        jot = next(item for item in exposed(kernel, head) if item.path == ("desk", "jot"))
        assert [parameter.name for parameter in jot.parameters] == ["text"]


def test_callers_say_what_they_are():
    assert Caller("heinzel", "s1", session="s1").agent
    assert Caller("local-agent", "mcp-stdio").agent
    assert Caller("token", "k1").agent
    assert not Caller("person", "cli").agent
    assert not Caller("daemon", "tick").agent
    assert str(Caller("token", "k1")) == "token:k1"


# --- the budget ---


def test_the_write_budget_counts_per_caller_minute_and_day():
    now = [0.0]
    budget = WriteBudget(clock=lambda: now[0])
    watched, unwatched = Caller("heinzel", "a"), Caller("heinzel", "b", attended=False)
    for _ in range(UNATTENDED.minute):
        budget.take(unwatched)
    with pytest.raises(HeinzelError, match="write budget for the minute"):
        budget.take(unwatched)
    for _ in range(ATTENDED.minute):
        budget.take(watched)  # another caller has its own count, and a watched turn a larger one
    with pytest.raises(HeinzelError, match="write budget for the minute"):
        budget.take(watched)
    # A minute later there is room again, until the day's count is used up.
    for _ in range(UNATTENDED.day // UNATTENDED.minute - 1):
        now[0] += 61.0
        for _ in range(UNATTENDED.minute):
            budget.take(unwatched)
    now[0] += 61.0
    with pytest.raises(HeinzelError, match="write budget for the day"):
        budget.take(unwatched)
    now[0] += 86400.0
    budget.take(unwatched)  # a day later the count is free again


# --- the audit line ---


def test_audit_names_the_target_and_only_a_digest_of_the_arguments():
    arguments = {"entry": "b-1a2b3c4d", "title": "Secret plans for mira.sato@example.org"}
    assert target_of(arguments) == "b-1a2b3c4d"
    assert target_of({"title": "free text with spaces"}) is None
    assert target_of({"name": "x" * 200}) is None
    digest = arguments_digest(arguments)
    assert len(digest) == 16
    assert digest == arguments_digest(dict(reversed(arguments.items())))
    assert digest != arguments_digest({**arguments, "title": "Other"})


# --- a Heinzel's turn ---


def test_a_direct_write_runs_once_as_the_heinzel_and_is_audited(kernel, ran, caplog):
    jot, session = tool(kernel, "desk_jot")
    with caplog.at_level(logging.INFO, logger="heinzel.audit.agent"):
        answer = jot.call({"text": "milk", "caller": {"kind": "person", "id": "cli"}})
    assert ran == [("jot", Caller("heinzel", session, session=session, attended=True))]
    assert '"by": "heinzel:' in answer
    assert "example.org" not in answer  # redacted like a read
    assert kernel.resolve(ActionGate).calls == []
    (line,) = audit_lines(caplog)
    assert (line["caller"], line["module"], line["operation"], line["policy"], line["outcome"]) == (
        f"heinzel:{session}",
        "desk",
        "jot",
        "direct",
        "ran",
    )
    assert line["attended"] is True
    assert "milk" not in str(line["arguments"])


def test_a_direct_write_never_gets_private_write_rights(kernel):
    session = kernel.resolve(ChatService).open(None).id
    module = Module("vault", "Test.")

    @module.operation(read_only=False, private=True, agents="direct")
    def stash() -> str:
        """Write private data."""
        return "stashed"

    item = next(i for i in exposed(kernel, "mcp") if i.path == ("desk", "jot"))
    from dataclasses import replace

    from heinzel.kernel import ChatTool

    private = ChatTool(kernel, replace(item, operation=module.lets[0]), session)
    with pytest.raises(ChatToolError, match="does not permit the requested private operation"):
        private.call({})


def test_approve_and_unmarked_writes_are_proposed_and_do_not_run(kernel, ran, caplog):
    send, _ = tool(kernel, "desk_send")
    legacy, _ = tool(kernel, "desk_legacy")
    with caplog.at_level(logging.INFO, logger="heinzel.audit.agent"):
        assert "waits for the person's confirmation" in send.call({"to": "team"})
        assert "waits for the person's confirmation" in legacy.call({"text": "x"})
    assert ran == []
    assert [call.operation for call in kernel.resolve(ActionGate).calls] == ["send", "legacy"]
    assert [(line["policy"], line["outcome"]) for line in audit_lines(caplog)] == [
        ("approve", "proposed"),
        ("propose", "proposed"),
    ]


def test_the_persons_own_operation_is_refused_for_a_heinzel(kernel, ran, caplog):
    confirm, _ = tool(kernel, "desk_confirm")
    with (
        caplog.at_level(logging.INFO, logger="heinzel.audit.agent"),
        pytest.raises(ChatToolError, match="person's own"),
    ):
        confirm.call({"action": "a1"})
    assert ran == []
    assert kernel.resolve(ActionGate).calls == []
    assert [(line["policy"], line["outcome"]) for line in audit_lines(caplog)] == [("never", "refused")]


def test_the_arguments_decide_between_running_and_proposing(kernel, ran):
    rename, _ = tool(kernel, "desk_rename")
    rename.call({"entry": "b-1"})
    assert ran == [("rename", "b-1")]
    assert "waits for the person's confirmation" in rename.call({"entry": "b-1", "url": "https://example.com/"})
    assert ran == [("rename", "b-1")]


def test_a_heinzel_names_only_its_own_session_and_cannot_speak_for_another_heinzel(kernel, ran, caplog):
    session = kernel.resolve(ChatService).open(None).id
    own = Caller("heinzel", session, session=session)
    assert bound_to_caller("direct", own, {"session": session, "text": "x"}) == "direct"
    assert bound_to_caller("direct", own, {"heinzel": session}) == "direct"
    assert bound_to_caller("direct", own, {"heinzel": "team:weekly-digest"}) == "approve"
    assert bound_to_caller("approve", own, {"heinzel": "team:weekly-digest"}) == "approve"
    for name in ("session", "session_id"):
        with pytest.raises(HeinzelError, match="only for its own session"):
            bound_to_caller("direct", own, {name: "another-session"})
    # An outside agent and the person are not a chat: their arguments are theirs.
    assert bound_to_caller("direct", Caller("local-agent", "mcp-stdio"), {"session": "any", "heinzel": "x"}) == "direct"
    assert bound_to_caller("direct", Caller("person", "cli"), {"session_id": "any"}) == "direct"
    # On the path itself: telemetry in another Heinzel's name waits for the person, and nothing is logged as run.
    drafted, _ = tool(kernel, "report_drafted")
    with caplog.at_level(logging.INFO, logger="heinzel.audit.agent"):
        answer = drafted.call({"heinzel": "team:weekly-digest", "target": "https://example.com/doc"})
    assert "waits for the person's confirmation" in answer
    assert [(line["policy"], line["outcome"]) for line in audit_lines(caplog)] == [("approve", "proposed")]


def test_a_failed_direct_write_is_audited_without_its_message(kernel, caplog):
    broken, _ = tool(kernel, "desk_broken")
    with caplog.at_level(logging.INFO, logger="heinzel.audit.agent"), pytest.raises(ChatToolError) as refused:
        broken.call({"text": "plans"})
    assert "example.org" not in str(refused.value)
    (line,) = audit_lines(caplog)
    assert (line["outcome"], line["error"]) == ("failed", "HeinzelError")


def test_an_unattended_turn_has_the_smaller_budget(kernel, ran, caplog):
    jot, _ = tool(kernel, "desk_jot", attended=False)
    for _ in range(UNATTENDED.minute):
        jot.call({"text": "x"})
    with (
        caplog.at_level(logging.INFO, logger="heinzel.audit.agent"),
        pytest.raises(ChatToolError, match="write budget"),
    ):
        jot.call({"text": "x"})
    assert len(ran) == UNATTENDED.minute
    assert audit_lines(caplog)[-1]["outcome"] == "over-budget"
    assert write_budget(kernel) is write_budget(kernel)


def test_a_heinzel_turn_adds_a_bookmark_and_it_is_in_the_tree(kernel):
    add, _ = tool(kernel, "system_bookmark_add")
    remove, _ = tool(kernel, "system_bookmark_remove")
    edit, _ = tool(kernel, "system_bookmark_edit")
    add.call({"url": "https://example.com/docs", "title": "Docs"})
    service = kernel.resolve(BookmarkService)
    (entry,) = service.listing().bookmarks
    assert (entry.title, entry.url) == ("Docs", "https://example.com/docs")
    # Renaming runs; repointing and removing wait for the person.
    edit.call({"entry": entry.id, "title": "Handbook"})
    assert "waits for the person's confirmation" in edit.call({"entry": entry.id, "url": "https://example.net/"})
    assert "waits for the person's confirmation" in remove.call({"entry": entry.id})
    (kept,) = service.listing().bookmarks
    assert (kept.title, kept.url) == ("Handbook", "https://example.com/docs")
    assert [call.operation for call in kernel.resolve(ActionGate).calls] == ["edit", "remove"]


# --- an outside agent over stdio MCP ---


def stdio(kernel, path: tuple[str, ...]):
    item = next(item for item in exposed(kernel, "mcp") if item.path == path)
    return mcp.tool(kernel, item)


def test_stdio_runs_direct_writes_as_a_local_agent_not_the_person(kernel, ran, caplog):
    with caplog.at_level(logging.INFO, logger="heinzel.audit.agent"):
        assert stdio(kernel, ("desk", "jot"))(text="milk")["by"] == "local-agent:mcp-stdio"
        assert stdio(kernel, ("desk", "legacy"))(text="x") == "done"  # unmarked: as before the policy
    assert [(line["caller"], line["policy"], line["outcome"]) for line in audit_lines(caplog)] == [
        ("local-agent:mcp-stdio", "direct", "ran"),
        ("local-agent:mcp-stdio", "propose", "ran"),
    ]


def test_stdio_refuses_approve_with_the_persons_command_and_never_outright(kernel, ran, caplog):
    with caplog.at_level(logging.INFO, logger="heinzel.audit.agent"):
        with pytest.raises(ToolError, match=r"needs the person's approval.*hz desk send --to team"):
            stdio(kernel, ("desk", "send"))(to="team")
        with pytest.raises(ToolError, match="person's own"):
            stdio(kernel, ("desk", "confirm"))(action="a1")
        with pytest.raises(ToolError, match="hz system bookmark remove --entry b-00000000"):
            stdio(kernel, ("system", "bookmark", "remove"))(entry="b-00000000")
    assert ran == []
    assert [line["outcome"] for line in audit_lines(caplog)] == ["refused", "refused", "refused"]


def test_stdio_writes_share_the_budget(kernel, ran):
    jot = stdio(kernel, ("desk", "jot"))
    for _ in range(ATTENDED.minute):
        jot(text="x")
    with pytest.raises(ToolError, match="write budget"):
        jot(text="x")
