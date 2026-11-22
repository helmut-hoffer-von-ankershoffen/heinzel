"""The owner's record of agents' writes and their yes for a plugin's direct writes (system.write, ADR 0162)."""

import json
import stat

import pytest
from rich.console import Console

from heinzel import ActionGate, Caller, HeinzelError, Kernel, Module, ModuleRecord, Status
from heinzel.heads import mcp
from heinzel.heads._exposure import exposed
from heinzel.kernel import (
    AgentWriteLog,
    OperationLet,
    Origin,
    chat_tools,
    declared_agent_writes,
    discover,
    restricts,
)
from heinzel.kernel.ai._agent_writes import arguments_digest
from heinzel.kernel.session._chat_tools import write_log, write_policy
from heinzel.modules.system import write
from heinzel.modules.system.runner.chat._service import ChatService
from heinzel.modules.system.write import WriteLog, health, render_plugins, render_writes
from heinzel.modules.system.write._service import KEEP, TRIM_BYTES

pytestmark = pytest.mark.unit

PLUGIN = Origin(distribution="heinzel-crm", version="1.0.0", builtin=False)


class Gate(ActionGate):
    def __init__(self) -> None:
        self.calls = []

    def propose(self, call):  # type: ignore[override]
        self.calls.append(call)
        return f"a{len(self.calls)}"


def crm(ran: list[str]) -> Module:
    module = Module("crm", "A plugin's writes.")

    @module.operation(read_only=False, requires=["tracker:write"], agents="direct", audience="everyday")
    def create(title: str) -> str:
        """Create a contact."""
        ran.append(title)
        return "created"

    @module.operation(read_only=False, requires=["mail:write"], agents="direct", audience="everyday")
    def send(to: str) -> str:
        """Send a mail."""
        ran.append(to)
        return "sent"

    @module.operation(read_only=False, audience="everyday")
    def legacy() -> str:
        """A write nobody marked."""
        return "done"

    return module


@pytest.fixture
def ran() -> list[str]:
    return []


@pytest.fixture
def kernel(settings, tmp_path, ran):
    owned = settings.model_copy(update={"state_dir": tmp_path / "state", "data_dir": tmp_path / "data"})
    records = discover(plugins=False)[0]
    records.append(ModuleRecord(qualname="crm", module=crm(ran), origin=PLUGIN))
    kernel = Kernel(owned, records, config=None)
    kernel.bind(ActionGate, Gate())
    return kernel


@pytest.fixture
def log(kernel) -> WriteLog:
    return kernel.resolve(WriteLog)


def text(renderable) -> str:
    console = Console(width=160, force_terminal=False)
    with console.capture() as capture:
        console.print(renderable)
    return capture.get()


def chat_tool(kernel, name: str):
    session = kernel.resolve(ChatService).open(None).id
    return next(tool for tool in chat_tools(kernel, session, "all", attended=True) if tool.name == name)


def test_the_kernel_records_and_asks_through_the_module(kernel, log):
    assert isinstance(write_log(kernel), WriteLog)
    assert AgentWriteLog().acknowledged("heinzel-crm", "crm", "create") is False  # no provider: nothing is allowed
    AgentWriteLog().record(None, "crm", "create", {}, policy="direct", outcome="ran")  # and nothing recorded


def test_every_write_an_agent_asks_for_is_one_private_line_without_its_arguments(kernel, log, tmp_path):
    caller = Caller("token", "k1a2b3")
    log.record(caller, "system.bookmark", "add", {"url": "https://example.com/secret"}, policy="direct", outcome="ran")
    log.record(caller, "system.bookmark", "remove", {"entry": "b-1a2b3c4d"}, policy="approve", outcome="refused")
    log.record(None, "crm", "create", {}, policy="propose", outcome="failed")
    directory = tmp_path / "state/agent-writes"
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((directory / "journal.jsonl").stat().st_mode) == 0o600
    assert "example.com" not in (directory / "journal.jsonl").read_text()
    listed = log.listing(None, unattended=False, limit=50)
    assert [(w.caller, w.operation, w.target, w.outcome) for w in listed.writes] == [
        ("unknown", "create", None, "failed"),
        ("token:k1a2b3", "remove", "b-1a2b3c4d", "refused"),
        ("token:k1a2b3", "add", None, "ran"),
    ]
    assert listed.writes[0].attended is False  # an unknown caller is never counted as watched
    token = next(line for line in listed.callers if line.caller == "token:k1a2b3")
    assert (token.ran, token.ran_today, token.refused) == (1, 1, 1)
    assert token.last == listed.writes[2].at
    assert [w.operation for w in log.listing("token:k1a2b3", unattended=False, limit=1).writes] == ["remove"]


def test_argument_digests_are_keyed_with_a_private_salt_of_this_installation(kernel, log, tmp_path):
    salt = log.salt()
    path = tmp_path / "state/agent-writes/salt"
    assert len(salt) == 16
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert WriteLog(kernel).salt() == salt  # made once, the same for every process of this installation
    log.record(Caller("token", "k1"), "crm", "create", {"title": "Ada"}, policy="direct", outcome="ran")
    (entry,) = log.listing(None, unattended=False, limit=1).writes
    assert entry.arguments == arguments_digest({"title": "Ada"}, salt)
    assert entry.arguments != arguments_digest({"title": "Ada"}, b"0" * 16)
    path.write_bytes(b"")  # emptied: no guessable key, this process uses its own
    assert len(WriteLog(kernel).salt()) == 16


def test_the_digest_counts_what_ran_with_nobody_watching(kernel, log):
    watched, unwatched = Caller("heinzel", "s1", session="s1", attended=True), Caller("heinzel", "s2", session="s2")
    assert log.listing(None, unattended=True, limit=5).unattended_ran == 0
    assert [check.name for check in health(log)] == ["direct writes of heinzel-crm"]  # the plugin waits
    log.record(watched, "system.bookmark", "add", {}, policy="direct", outcome="ran")
    log.record(unwatched, "system.bookmark", "add", {}, policy="direct", outcome="ran")
    log.record(unwatched, "system.bookmark", "remove", {}, policy="approve", outcome="proposed")
    listed = log.listing(None, unattended=True, limit=5)
    assert listed.unattended_ran == 1
    assert {w.caller for w in listed.writes} == {"heinzel:s2"}
    digest = next(check for check in health(log) if check.name == "unwatched writes")
    assert digest.status is Status.UP  # a note for the owner, never a fault
    assert "1 writes ran" in digest.detail
    assert "hz system write list --unattended" in digest.detail
    assert "(unwatched)" in text(render_writes(listed))
    assert "No agent has asked" in text(render_writes(type(listed)()))


def test_the_journal_is_bounded_and_skips_torn_lines(kernel, log, tmp_path):
    caller = Caller("local-agent", "mcp-stdio")
    journal = tmp_path / "state/agent-writes/journal.jsonl"
    log.record(caller, "crm", "create", {}, policy="direct", outcome="ran")
    one = journal.read_bytes()
    # Past the size at which it is cut back, whatever one entry weighs.
    journal.write_bytes(b"{torn\n" + b"x" * 5000 + b"\n" + one * (TRIM_BYTES // len(one) + 50))
    log.record(caller, "crm", "create", {}, policy="direct", outcome="ran")
    assert len(journal.read_bytes().splitlines()) == KEEP
    assert stat.S_IMODE(journal.stat().st_mode) == 0o600
    assert len(log.listing(None, unattended=False, limit=500).writes) == 500


def test_a_plugins_direct_write_is_proposed_until_the_owner_allows_the_plugin(kernel, log, ran):
    create = next(item for item in exposed(kernel, "mcp") if item.path == ("crm", "create"))
    send = next(item for item in exposed(kernel, "mcp") if item.path == ("crm", "send"))
    assert write_policy(kernel, create, {}) == "propose"
    assert "waits for the person's confirmation" in chat_tool(kernel, "crm_create").call({"title": "Ada"})
    assert ran == []
    listed = log.plugin_writes()
    assert [(w.plugin, w.operation, w.allowed, w.capped) for w in listed.writes] == [
        ("heinzel-crm", "create", False, False),
        ("heinzel-crm", "send", False, True),
    ]
    assert listed.next == ["hz system write allow heinzel-crm"]
    waiting = next(check for check in health(log) if check.name == "direct writes of heinzel-crm")
    assert waiting.status is Status.UP
    assert "hz system write allow heinzel-crm" in waiting.detail
    assert "proposed" in text(render_plugins(listed))

    allowed = log.allow("crm", revoke=False)
    assert allowed.next == []
    assert write_policy(kernel, create, {}) == "direct"
    assert write_policy(kernel, send, {}) == "approve"  # the owner's yes lifts no ceiling: mail still asks
    assert "created" in chat_tool(kernel, "crm_create").call({"title": "Ada"})
    assert ran == ["Ada"]
    assert "runs directly" in text(render_plugins(allowed))
    assert "hz holds it" in text(render_plugins(allowed))

    log.allow("heinzel-crm", revoke=True)
    assert write_policy(kernel, create, {}) == "propose"


def test_an_update_that_turns_an_operation_direct_waits_for_a_new_yes(kernel, log, tmp_path):
    log.allow("crm", revoke=False)
    allowed = tmp_path / "state/agent-writes/allowed.json"
    assert json.loads(allowed.read_text()) == {"heinzel-crm": ["crm create [direct]", "crm send [direct]"]}
    assert stat.S_IMODE(allowed.stat().st_mode) == 0o600
    # The next version marks one more operation direct: the earlier yes named the operations, so it does not cover it.
    assert log.acknowledged("heinzel-crm", "crm", "create") is True
    assert log.acknowledged("heinzel-crm", "crm", "export") is False
    # Nor a marker that changed since the yes: the yes named the marker too.
    allowed.write_text(json.dumps({"heinzel-crm": ["crm create [direct|approve:when it repoints]", "crm send"]}))
    assert log.acknowledged("heinzel-crm", "crm", "create") is False
    assert log.acknowledged("heinzel-crm", "crm", "send") is False
    log.allow("crm", revoke=False)
    assert log.acknowledged("heinzel-other", "crm", "create") is False
    assert log.acknowledged("Heinzel_CRM", "crm", "create") is True  # one plugin, however its name is written


def test_a_damaged_allowance_file_allows_nothing(kernel, log, tmp_path):
    log.allow("crm", revoke=False)
    allowed = tmp_path / "state/agent-writes/allowed.json"
    for damaged in ("{not json", '["a list"]', '{"heinzel-crm": "crm create"}', '{"heinzel-crm": [1, 2]}'):
        allowed.write_text(damaged)
        assert log.acknowledged("heinzel-crm", "crm", "create") is False


def test_allowing_names_a_plugin_that_asks(kernel, log):
    with pytest.raises(HeinzelError, match="asks to run a write directly"):
        log.allow("clock", revoke=False)
    assert log.allow("clock", revoke=True).next == ["hz system write allow heinzel-crm"]  # nothing to take back
    assert "No installed plugin asks" in text(render_plugins(type(log.plugin_writes())()))


def test_the_record_and_the_yes_are_the_owners_and_no_agents(kernel):
    lets = {let.name: let for let in write.module.lets if isinstance(let, OperationLet)}
    assert set(lets) == {"list", "plugins", "allow"}
    for let in lets.values():
        assert let.heads == {"cli", "web"}
        for head in ("mcp", "mcp-http", "mcp-check-in", "api"):
            assert restricts(let.heads, head)
    assert declared_agent_writes(lets["allow"]) == {"never"}


def test_a_write_over_stdio_lands_in_the_record(kernel, log, ran):
    item = next(item for item in exposed(kernel, "mcp") if item.path == ("crm", "legacy"))
    assert mcp.tool(kernel, item)() == "done"
    (entry,) = log.listing("local-agent:mcp-stdio", unattended=False, limit=5).writes
    assert (entry.module, entry.operation, entry.policy, entry.outcome) == ("crm", "legacy", "propose", "ran")
