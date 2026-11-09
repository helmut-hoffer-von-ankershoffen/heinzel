"""Individual Heinzel tools through the actual private MCP protocol and trusted CLI seam."""

import asyncio
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from threading import Event, Lock
from typing import cast

import anyio
import pytest
from mcp import ClientSession, types
from mcp.shared.message import SessionMessage

from heinzel import Module
from heinzel.heads._heinzel_tools import Boundary, ContractEffects, Disposition, HeinzelTools, HeinzelWork
from heinzel.heads.cli import main as cli_main
from heinzel.heads.mcp._heinzel_tools import build_admitted_heinzel_server
from heinzel.kernel import CurrentUser, HeinzelError
from heinzel.kernel._heinzel_invocation import HeinzelInput, HeinzelInvocations, HeinzelTarget
from heinzel.kernel._logging import record_fields
from heinzel.modules.heinzel._session_crud import SessionCrud

pytestmark = pytest.mark.unit
_DIGEST = "sha256:" + "a" * 64
_PERSONAL = HeinzelTarget(kind="personal", identity="hz-" + "0" * 26)
_CONTRACT = HeinzelTarget(kind="contract", identity="team:daily", source_digest=_DIGEST)


class Broker:
    """Minimal in-memory stand-in for the admitted broker's owning journal."""

    def __init__(self):
        self.active = True
        self.work: dict[tuple[str, str], tuple[HeinzelWork, Disposition]] = {}
        self.lock = Lock()

    def check_active(self):
        if not self.active:
            msg = "revoked"
            raise HeinzelError(msg)

    @contextmanager
    def access(self, _target):
        yield self.check_active

    def record(self, work: HeinzelWork, boundary: Boundary) -> Disposition:
        key = work.target, work.request_id
        with self.lock:
            prior, state = self.work.get(key, (work, "queued"))
            if not self.active or prior != work or (boundary == "start" and state != "queued"):
                msg = "stale or changed"
                raise HeinzelError(msg)
            if boundary == "start":
                state = "running"
            if boundary == "complete":
                assert state == "running"
                state = "completed"
            self.work[key] = (work, state)
            return state


class Provider(HeinzelInvocations):
    def __init__(self):
        self.calls = []
        self.stale = set()
        self.uncertain = False

    def require_current(self, target):
        if target.key in self.stale:
            msg = "removed or replaced"
            raise HeinzelError(msg)

    def execute(self, target, request, check_active):
        check_active()
        self.calls.append((target.key, request.request_id, request.message))
        if self.uncertain:
            msg = "interrupted after effect"
            raise HeinzelError(msg)
        return {"request_id": request.request_id, "target": target.key, "reply": "Grüße"}


@pytest.fixture
def scoped_tools(make_kernel, tmp_path):
    provider = Provider()
    broker = Broker()
    module = Module("example", "Synthetic provider")
    module.service(HeinzelInvocations, lambda: provider)
    kernel = make_kernel(example=module)

    def tools():
        return HeinzelTools(
            kernel,
            [_PERSONAL, _CONTRACT],
            scope="grant-revision-1",
            root=tmp_path / "results",
            access=broker.access,
            record=broker.record,
            contracts=ContractEffects(provider, lambda _key, check: nullcontext(check)),
        )

    return tools, provider, broker


async def _mcp_round_trip(tools, name, arguments):
    server = build_admitted_heinzel_server(tools)
    to_server, server_read = anyio.create_memory_object_stream[SessionMessage | Exception](1)
    to_client, client_read = anyio.create_memory_object_stream[SessionMessage](1)
    async with anyio.create_task_group() as group:
        group.start_soon(server.run, server_read, to_client, server.create_initialization_options())
        async with to_server, client_read, ClientSession(client_read, to_server) as client:
            await client.initialize()
            listed = await client.list_tools()
            response = await client.call_tool(name, arguments)
        group.cancel_scope.cancel()
    return listed, response


def test_mcp_and_cli_share_durable_identity_and_exact_result(scoped_tools, monkeypatch, capsys):
    tools, provider, broker = scoped_tools
    args = {"request_id": "same-1", "message": "Draft the summary"}
    listed, response = asyncio.run(_mcp_round_trip(tools(), _PERSONAL.tool_name, args))
    assert {tool.name for tool in listed.tools} == {_PERSONAL.tool_name, _CONTRACT.tool_name}
    assert not response.is_error
    retained = cast("types.TextContent", response.content[0]).text
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "hz",
            "system",
            "mcp",
            "tools",
            "invoke",
            _PERSONAL.tool_name,
            "--request-id",
            "same-1",
            "--message",
            "Draft the summary",
        ],
    )
    with pytest.raises(SystemExit) as finished:
        cli_main(private_heinzel_tools=tools())
    assert finished.value.code == 0
    assert capsys.readouterr().out == retained
    assert len(provider.calls) == 1
    assert json.loads(retained)["request_id"] == provider.calls[0][1]
    assert broker.work[(_PERSONAL.key, "same-1")][1] == "completed"


def test_private_discovery_and_dispatch_refuse_stale_target_and_revoked_lease(scoped_tools):
    tools, provider, broker = scoped_tools
    provider.stale.add(_CONTRACT.key)
    listed, response = asyncio.run(_mcp_round_trip(tools(), _CONTRACT.tool_name, {"request_id": "request-1"}))
    assert [item.name for item in listed.tools] == [_PERSONAL.tool_name]
    assert response.is_error
    assert not provider.calls
    broker.active = False
    listed, response = asyncio.run(
        _mcp_round_trip(tools(), _PERSONAL.tool_name, {"request_id": "request-1", "message": "Hi"})
    )
    assert not listed.tools
    assert response.is_error
    assert not provider.calls


def test_uncertain_effect_and_changed_input_are_not_replayed(scoped_tools):
    tools, provider, broker = scoped_tools
    provider.uncertain = True
    for message in ("Hi", "Hi", "Changed"):
        with pytest.raises(HeinzelError):
            tools().call(_PERSONAL.tool_name, HeinzelInput(request_id="first", message=message))
    assert len(provider.calls) == 1
    assert broker.work[(_PERSONAL.key, "first")][1] == "running"


def test_a_concurrent_retry_cannot_run_a_second_effect(scoped_tools, monkeypatch, caplog):
    tools, provider, _broker = scoped_tools
    original = provider.execute
    started, finish = Event(), Event()
    request = HeinzelInput(request_id="concurrent-1", message="Private fixture message")

    def blocked(target, scoped_request, check_active):
        started.set()
        assert finish.wait(timeout=2)
        return original(target, scoped_request, check_active)

    monkeypatch.setattr(provider, "execute", blocked)
    caplog.set_level("INFO", logger="heinzel.mcp")
    with ThreadPoolExecutor(max_workers=1) as workers:
        first = workers.submit(tools().call, _PERSONAL.tool_name, request)
        try:
            assert started.wait(timeout=2)
            with pytest.raises(HeinzelError):
                tools().call(_PERSONAL.tool_name, request)
        finally:
            finish.set()
        assert first.result(timeout=2) == tools().call(_PERSONAL.tool_name, request)
    assert len(provider.calls) == 1
    events = [record_fields(record) for record in caplog.records if record.getMessage() == "mcp.heinzel.invoke"]
    assert [event["outcome"] for event in events if event["phase"] == "end"] == ["error", "ok", "ok"]
    assert request.message not in repr(events)
    assert request.request_id not in repr(events)


def test_standalone_cli_does_not_boot_an_unapproved_project(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["hz", "system", "mcp", "tools", "list"])
    with pytest.raises(SystemExit) as refused:
        cli_main()
    assert refused.value.code == 1
    assert "unavailable" in capsys.readouterr().err


def test_stable_names_distinguish_namespaces_and_ignore_revisions():
    personal_names = {_PERSONAL.tool_name}
    for namespace in ("me", "team"):
        target = HeinzelTarget(kind="contract", identity=f"{namespace}:daily", source_digest=_DIGEST)
        revised = HeinzelTarget(kind="contract", identity=target.identity, source_digest="sha256:" + "b" * 64)
        assert target.tool_name == revised.tool_name
        assert len(target.tool_name) <= 64
        personal_names.add(target.tool_name)
    assert len(personal_names) == 3


def test_builtin_provider_accepts_the_personal_inbox_without_running_a_model(make_kernel):
    kernel = make_kernel(builtins=True)
    current = kernel.resolve(CurrentUser)
    provider = kernel.resolve(HeinzelInvocations)
    with current.local("mcp-stdio"):
        session = kernel.resolve(SessionCrud).new_chat("create-personal", "Fixture")
        target = HeinzelTarget(kind="personal", identity=session.session_id)
        provider.require_current(target)
        request = HeinzelInput(request_id="one-exact", message="Read the notes")
        assert provider.execute(target, request, lambda: None) == provider.execute(target, request, lambda: None)
        snapshot = kernel.resolve(SessionCrud).snapshot(session.session_id)
    assert len(snapshot.messages) == 1
    assert snapshot.messages[0].text == "Read the notes"
