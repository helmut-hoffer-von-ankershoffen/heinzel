"""The owned Unix peer, real MCP wire and private CLI using one durable grant journal."""

import json
import os
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, suppress
from queue import Queue
from threading import Event

import anyio
import pytest

from heinzel import Module
from heinzel.heads.cli import main as cli_main
from heinzel.heads.mcp import _ipc_bridge
from heinzel.heads.mcp._heinzel_tools import serve_admitted_heinzel_connection
from heinzel.heads.mcp._ipc_admission import McpGeneration, admit_owned_stream
from heinzel.heads.mcp._ipc_approval import PrivateMcpPlan
from heinzel.heads.mcp._ipc_bridge import BridgeHandshake
from heinzel.heads.mcp._ipc_descriptors import local_stream_pair
from heinzel.heads.mcp._ipc_provider import McpBackendLimit, McpContractBackend
from heinzel.heads.mcp._ipc_supervisor import owned_mcp_supervisor
from heinzel.heads.mcp._ipc_work import McpWorkJournal, McpWorkRequest, owned_private_dispatch
from heinzel.kernel import (
    MCP_HEINZEL_INBOX,
    CurrentUser,
    HeinzelError,
    HeinzelInput,
    HeinzelInvocations,
    HeinzelTarget,
    Kernel,
    Settings,
)
from heinzel.kernel._approval import arguments_hash
from heinzel.kernel._identity import MCP_HEINZEL_CONTRACT
from heinzel.kernel._logging import record_fields
from heinzel.kernel._session_store import SessionStore
from heinzel.modules.heinzel._draft_workflow_journal import DraftJournal
from heinzel.modules.heinzel._session_crud import SessionCrud

pytestmark = pytest.mark.unit
_TARGET = HeinzelTarget(kind="personal", identity="hz-" + "0" * 26)
_UNREVIEWED = HeinzelTarget(kind="personal", identity="hz-" + "1" * 26)
_CONTRACT = HeinzelTarget(kind="contract", identity="team:probe", source_digest="sha256:" + "a" * 64)
_BACKEND = "sha256:" + "b" * 64
_OTHER_PRINCIPAL = "u-" + "f" * 32
_TIMEOUT = 3


class SyntheticProvider(HeinzelInvocations):
    def __init__(self, current, target=_TARGET):
        self.current = current
        self.target = target
        self.calls = []

    def require_current(self, target):
        identity = self.current.context()
        assert identity.authentication in {"mcp-private-cli", "mcp-private-wire"}
        assert identity.resource == target.key
        assert identity.audience == "hz-private-mcp"
        assert identity.permissions == {MCP_HEINZEL_INBOX if target.kind == "personal" else MCP_HEINZEL_CONTRACT}
        assert target == self.target

    def execute(self, target, request, check_active):
        self.require_current(target)
        check_active()
        self.calls.append((request.request_id, self.current.context().authentication))
        return {"request_id": request.request_id, "output": "Grüße aus der Probe"}


def _plan(  # noqa: PLR0913 - vary independently reviewed terms in negative fixtures
    name,
    *,
    dispatch=True,
    registration=None,
    principal=_OTHER_PRINCIPAL,
    target=_TARGET,
    backend=None,
):
    terms = {
        "dispatch": {target.key: [target.operation]} if dispatch else {},
        "heinzel_targets": [target.model_dump(mode="json")],
        "heinzel_principal": principal,
    }
    if backend is not None:
        terms["heinzel_backend"] = backend.model_dump(mode="json")
    return PrivateMcpPlan.capture(
        McpGeneration(registration or name, 1),
        f"grant-{name}",
        2,
        time.monotonic() + 60,
        terms,
    )


@contextmanager
def _approved_connection(plan, authority, serve=None):
    owned, client = local_stream_pair()
    published = Queue()
    release = Event()

    def worker():
        with admit_owned_stream(
            lambda: owned, lambda: plan.generation, lambda _: plan.generation, authority.publish
        ) as lease:
            published.put(lease)
            if serve is not None:
                serve(lease)
            else:
                assert release.wait(_TIMEOUT)

    with owned, client, ThreadPoolExecutor(max_workers=1) as workers:
        client.settimeout(_TIMEOUT)
        running = workers.submit(worker)
        try:
            with authority._changed:
                assert authority._changed.wait_for(authority.pending_reviews, timeout=_TIMEOUT)
                (review,) = authority.pending_reviews()
                assert review.plan is plan
                authority.approve(review)
            yield client, published.get(timeout=_TIMEOUT)
        finally:
            release.set()
            with suppress(OSError):
                client.shutdown(socket.SHUT_RDWR)
            running.result(timeout=_TIMEOUT)


def _rpc(client, wire, method, request_id, **params):
    request = {"jsonrpc": "2.0", "method": method, "id": request_id, "params": params}
    client.sendall(json.dumps(request).encode() + b"\n")
    response = json.loads(wire.readline())
    assert response["id"] == request_id
    return response["result"]


def _initialize(client, wire):
    initialized = _rpc(
        client,
        wire,
        "initialize",
        1,
        protocolVersion="2025-11-25",
        capabilities={},
        clientInfo={"name": "isolated-probe", "version": "1"},
    )
    assert initialized["serverInfo"]["name"] == "heinzel-private"
    client.sendall(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')


def _mcp_invoke(client, wire, request_id, *, target=_TARGET):
    return _rpc(
        client, wire, "tools/call", 3, name=target.tool_name, arguments={"request_id": request_id, "message": "Probe"}
    )


def _cli_invoke(tools, request_id, monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "hz",
            "system",
            "mcp",
            "tools",
            "invoke",
            _TARGET.tool_name,
            "--request-id",
            request_id,
            "--message",
            "Probe",
        ],
    )
    with pytest.raises(SystemExit) as finished:
        cli_main(private_heinzel_tools=tools)
    return finished.value.code, capsys.readouterr().out


def _exercise_inherited_tool_bridge(directory, phase):  # noqa: PLR0915 - keep ownership and teardown in one scope
    """Exercise a private synthetic tool through the owned bridge, without a vendor process."""
    kernel = Kernel(Settings(state_dir=directory / "profile"), [])
    provider = SyntheticProvider(kernel.resolve(CurrentUser))
    rejected = []
    if phase == "provider-failure":

        def reject_provider(target, request, check_active):
            provider.require_current(target)
            check_active()
            rejected.append(request.request_id)
            msg = "Synthetic provider refused"
            raise HeinzelError(msg)

        provider.execute = reject_provider
    kernel.bind(HeinzelInvocations, provider)
    plan = _plan("bridge", principal=provider.current.owner().id)
    marker = b"fixture-admitted\n"
    acknowledge = BridgeHandshake.acknowledge
    retire = _ipc_bridge.OwnedMcpChild.retire
    reaped = []

    def fixture_ack(handshake, channel):
        acknowledge(handshake, channel)
        channel.sendall(marker)

    def fixture_retire(child, *args):
        retire(child, *args)
        reaped.append(child.pid)

    with (
        pytest.MonkeyPatch.context() as patcher,
        owned_mcp_supervisor(directory / "state", plan) as supervisor,
    ):
        patcher.setattr(BridgeHandshake, "acknowledge", fixture_ack)
        patcher.setattr(_ipc_bridge.OwnedMcpChild, "retire", fixture_retire)
        # Fork the owned bridge before starting any test worker threads.
        with supervisor.launch_bridge() as (front, transport), ThreadPoolExecutor(max_workers=1) as workers:
            front.settimeout(_TIMEOUT)
            running = workers.submit(transport.serve_heinzel, kernel)
            try:
                with supervisor._authority._changed:
                    assert supervisor._authority._changed.wait_for(
                        supervisor._authority.pending_reviews, timeout=_TIMEOUT
                    )
                    (review,) = supervisor._authority.pending_reviews()
                    if phase == "admission-failure":
                        # The bridge refuses all client bytes before exact admission.
                        front.sendall(b"unapproved input")
                    elif phase != "review-failure":
                        supervisor._authority.approve(review)
                if phase == "review-failure":
                    supervisor.revoke()
                if phase in {"review-failure", "admission-failure"}:
                    with pytest.raises(HeinzelError):
                        running.result(timeout=_TIMEOUT)
                    assert provider.calls == []
                    assert front.recv(1) == b""
                    supervisor.revoke()
                    with pytest.raises(HeinzelError), transport.admit():
                        pytest.fail("A failed connection was admitted again")
                else:
                    with front.makefile("rb") as wire:
                        assert wire.readline() == marker
                        _initialize(front, wire)
                        listed = _rpc(front, wire, "tools/list", 2)
                        assert [tool["name"] for tool in listed["tools"]] == [_TARGET.tool_name]
                        assert _mcp_invoke(front, wire, "unreviewed", target=_UNREVIEWED)["isError"]
                        assert provider.calls == []
                        result = _mcp_invoke(front, wire, "bridge-call")
                        if phase == "provider-failure":
                            assert result["isError"]
                            assert len(rejected) == 1
                            assert provider.calls == []
                        else:
                            recorded = json.loads(result["content"][0]["text"])["request_id"]
                            assert provider.calls == [(recorded, "mcp-private-wire")]
                        supervisor.revoke()
                        assert wire.readline() == b""
                    running.result(timeout=_TIMEOUT)
                    with pytest.raises(HeinzelError):
                        transport.serve_heinzel(kernel)
            finally:
                supervisor.revoke()
                with suppress(OSError):
                    front.shutdown(socket.SHUT_RDWR)
        assert len(reaped) == 1
        with pytest.raises(ChildProcessError):
            os.waitpid(reaped[0], os.WNOHANG)


@pytest.mark.parametrize("phase", ["success", "review-failure", "admission-failure", "provider-failure"])
def test_owned_bridge_serves_only_approved_synthetic_heinzel_then_revokes(tmp_path, phase):
    run = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            (
                "import runpy,sys; from pathlib import Path; "
                "runpy.run_path(sys.argv[1])['_exercise_inherited_tool_bridge'](Path(sys.argv[2]),sys.argv[3])"
            ),
            __file__,
            str(tmp_path),
            phase,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert run.returncode == 0, run.stderr


def test_protocol_and_cli_share_durable_results_across_leases_and_broker_restart(
    make_kernel,
    tmp_path,
    monkeypatch,
    capsys,
):
    module = Module("example", "Private probe")
    module.service(HeinzelInvocations, lambda: provider)
    kernel = make_kernel(example=module)
    provider = SyntheticProvider(kernel.resolve(CurrentUser))
    principal = provider.current.owner().id
    plan = _plan("first", principal=principal)
    root = tmp_path.resolve() / "first"

    with owned_private_dispatch(root, plan) as (authority, dispatch):

        def serve(lease):
            anyio.run(serve_admitted_heinzel_connection, kernel, dispatch, lease)

        with (
            _approved_connection(plan, authority, serve) as (client, protocol_lease),
            client.makefile("rb") as wire,
            _approved_connection(plan, authority) as (_, cli_lease),
        ):
            _initialize(client, wire)
            listed = _rpc(client, wire, "tools/list", 2)
            assert [tool["name"] for tool in listed["tools"]] == [_TARGET.tool_name]
            assert _mcp_invoke(client, wire, "unknown", target=_UNREVIEWED)["isError"]
            first = _mcp_invoke(client, wire, "shared")["content"][0]["text"]
            tools = dispatch.bind_heinzel_tools(kernel, cli_lease, authentication="mcp-private-cli")
            assert cli_lease.id != protocol_lease.id
            monkeypatch.setattr(sys, "argv", ["hz", "system", "mcp", "tools", "list"])
            with pytest.raises(SystemExit) as listed_from_cli:
                cli_main(private_heinzel_tools=tools)
            assert listed_from_cli.value.code == 0
            assert [tool["name"] for tool in json.loads(capsys.readouterr().out)] == [_TARGET.tool_name]
            assert _cli_invoke(tools, "shared", monkeypatch, capsys) == (0, first)
            assert provider.calls == [(json.loads(first)["request_id"], "mcp-private-wire")]
            status, second = _cli_invoke(tools, "from-cli", monkeypatch, capsys)
            assert status == 0
            assert provider.calls[-1] == (json.loads(second)["request_id"], "mcp-private-cli")

            other = _plan("other", principal=principal, registration=plan.generation.registration_id)
            with (
                owned_private_dispatch(tmp_path.resolve() / "other", other) as (other_authority, other_dispatch),
                _approved_connection(other, other_authority) as (_, other_lease),
            ):
                with pytest.raises(HeinzelError):
                    other_dispatch.bind_heinzel_tools(kernel, cli_lease, authentication="mcp-private-cli")
                other_tools = other_dispatch.bind_heinzel_tools(kernel, other_lease, authentication="mcp-private-cli")
                status, other_first = _cli_invoke(other_tools, "shared", monkeypatch, capsys)
                assert status == 0
                assert json.loads(other_first)["request_id"] != json.loads(first)["request_id"]
    assert len(provider.calls) == 3
    with pytest.raises(HeinzelError), owned_private_dispatch(root, other):
        pytest.fail("Another grant reused the existing broker journal")

    with (
        owned_private_dispatch(root, plan) as (authority, dispatch),
        _approved_connection(plan, authority, serve) as (client, _),
        client.makefile("rb") as wire,
        _approved_connection(plan, authority) as (_, cli_lease),
    ):
        _initialize(client, wire)
        assert _mcp_invoke(client, wire, "from-cli")["content"][0]["text"] == second
        tools = dispatch.bind_heinzel_tools(kernel, cli_lease, authentication="mcp-private-cli")
        assert _cli_invoke(tools, "shared", monkeypatch, capsys) == (0, first)
        authority.revoke()
        assert wire.readline() == b""
        assert _cli_invoke(tools, "shared", monkeypatch, capsys) == (1, "")
    assert len(provider.calls) == 3
    with owned_private_dispatch(root, plan) as (authority, _), pytest.raises(HeinzelError):
        authority.require_activation()


def test_typed_target_must_also_have_its_operation_in_the_reviewed_grant(tmp_path):
    with (
        pytest.raises(HeinzelError),
        owned_private_dispatch(tmp_path.resolve() / "invalid", _plan("bad", dispatch=False)),
    ):
        pytest.fail("An unreviewed target was admitted")


def test_two_unbudgeted_contract_requests_cannot_reach_a_provider(make_kernel, tmp_path):
    kernel = make_kernel()
    current = kernel.resolve(CurrentUser)
    reviewed = McpBackendLimit(fingerprint=_BACKEND, launches=2)
    plan = _plan("unavailable", principal=current.owner().id, target=_CONTRACT, backend=reviewed)
    provider = SyntheticProvider(current, _CONTRACT)
    wrong = McpContractBackend(McpBackendLimit(fingerprint="sha256:" + "c" * 64, launches=2), provider)
    for backend in (None, wrong):
        with (
            owned_private_dispatch(tmp_path.resolve() / "unavailable", plan, provider=backend) as (authority, dispatch),
            _approved_connection(plan, authority) as (_, lease),
            ThreadPoolExecutor(max_workers=2) as workers,
        ):
            tools = dispatch.bind_heinzel_tools(kernel, lease, authentication="mcp-private-cli")
            assert tools.targets() == ()
            calls = [
                workers.submit(tools.call, _CONTRACT.tool_name, HeinzelInput(request_id=f"request-{i}"))
                for i in range(2)
            ]
            for call in calls:
                with pytest.raises(HeinzelError):
                    call.result(timeout=_TIMEOUT)
            raw = McpWorkRequest(
                target=_CONTRACT.key,
                operation="wake",
                request_id="raw",
                input_hash="a" * 64,
            )
            with pytest.raises(HeinzelError):
                dispatch.record(lease, raw, "reserve")
    assert provider.calls == []


def test_reviewed_backend_limits_concurrency_and_durable_launches_across_grants(make_kernel, tmp_path, monkeypatch):
    kernel = make_kernel()
    current = kernel.resolve(CurrentUser)
    principal = current.owner().id
    provider = SyntheticProvider(current, _CONTRACT)
    reviewed = McpBackendLimit(fingerprint=_BACKEND, launches=1)
    backend = McpContractBackend(reviewed.model_copy(update={"launches": 2}), provider)
    first = _plan("first", principal=principal, target=_CONTRACT, backend=reviewed)
    other = _plan("other", principal=principal, target=_CONTRACT, backend=reviewed)
    root = tmp_path.resolve() / "first"
    started, release = Event(), Event()
    execute = provider.execute

    def block_first(target, request, check_active):
        if not started.is_set():
            started.set()
            assert release.wait(_TIMEOUT)
        return execute(target, request, check_active)

    monkeypatch.setattr(provider, "execute", block_first)
    with (
        owned_private_dispatch(root, first, provider=backend) as (authority, dispatch),
        _approved_connection(first, authority) as (_, lease),
        owned_private_dispatch(tmp_path.resolve() / "other", other, provider=backend) as (
            other_authority,
            other_dispatch,
        ),
        _approved_connection(other, other_authority) as (_, other_lease),
        ThreadPoolExecutor(max_workers=1) as workers,
    ):
        tools = dispatch.bind_heinzel_tools(kernel, lease, authentication="mcp-private-cli")
        other_tools = other_dispatch.bind_heinzel_tools(kernel, other_lease, authentication="mcp-private-wire")
        assert tools.targets() == (_CONTRACT,)
        request = HeinzelInput(request_id="first")
        running = workers.submit(tools.call, _CONTRACT.tool_name, request)
        try:
            assert started.wait(_TIMEOUT)
            for selected in (tools, other_tools):
                with pytest.raises(HeinzelError):
                    selected.call(_CONTRACT.tool_name, HeinzelInput(request_id="parallel"))
        finally:
            release.set()
        retained = running.result(timeout=_TIMEOUT)
        with pytest.raises(HeinzelError):
            tools.call(_CONTRACT.tool_name, HeinzelInput(request_id="grant-exhausted"))
        other_tools.call(_CONTRACT.tool_name, HeinzelInput(request_id="parallel"))
    assert len(provider.calls) == 2

    third = _plan("third", principal=principal, target=_CONTRACT, backend=reviewed)
    for plan, name in ((first, "first"), (third, "third")):
        with (
            owned_private_dispatch(tmp_path.resolve() / name, plan, provider=backend) as (authority, dispatch),
            _approved_connection(plan, authority) as (_, lease),
        ):
            tools = dispatch.bind_heinzel_tools(kernel, lease, authentication="mcp-private-wire")
            if plan is first:
                assert tools.call(_CONTRACT.tool_name, request) == retained
            with pytest.raises(HeinzelError):
                tools.call(_CONTRACT.tool_name, HeinzelInput(request_id="exhausted"))
    assert len(provider.calls) == 2


def test_ambiguous_provider_effect_remains_fenced_after_broker_restart(make_kernel, tmp_path, monkeypatch):
    kernel = make_kernel()
    current = kernel.resolve(CurrentUser)
    provider = SyntheticProvider(current, _CONTRACT)
    reviewed = McpBackendLimit(fingerprint=_BACKEND, launches=2)
    backend = McpContractBackend(reviewed, provider)
    principal = current.owner().id
    first = _plan("first", principal=principal, target=_CONTRACT, backend=reviewed)
    second = _plan("second", principal=principal, target=_CONTRACT, backend=reviewed)
    execute = provider.execute

    def interrupt(target, request, check_active):
        execute(target, request, check_active)
        msg = "Lost response after provider admission"
        raise HeinzelError(msg)

    monkeypatch.setattr(provider, "execute", interrupt)
    for plan, name, request in ((first, "first", "once"), (first, "first", "once"), (second, "second", "another")):
        with (
            owned_private_dispatch(tmp_path.resolve() / name, plan, provider=backend) as (authority, dispatch),
            _approved_connection(plan, authority) as (_, lease),
        ):
            tools = dispatch.bind_heinzel_tools(kernel, lease, authentication="mcp-private-cli")
            with pytest.raises(HeinzelError):
                tools.call(_CONTRACT.tool_name, HeinzelInput(request_id=request))
    assert len(provider.calls) == 1


def test_real_inbox_retains_exact_restricted_provenance(make_kernel, tmp_path, monkeypatch, caplog):
    kernel = make_kernel(builtins=True)
    current = kernel.resolve(CurrentUser)
    service = kernel.resolve(SessionCrud)
    with current.local("cli"):
        first = service.new_chat("first", "First")
        second = service.new_chat("second", "Second")
    target = HeinzelTarget(kind="personal", identity=first.session_id)
    plan = _plan("real", principal=current.owner().id, target=target)
    root = tmp_path.resolve() / "real"
    require_inbox = service.require_inbox_target

    def observe(session_id):
        assert current.context().resource == target.key
        with pytest.raises(HeinzelError, match="does not allow this inbox"):
            service.snapshot(second.session_id)
        with pytest.raises(HeinzelError, match="only permits its exact inbox"):
            service.repository.budget_read(first.session_id)
        with pytest.raises(HeinzelError, match="only permits its exact inbox"):
            service.repository.reserved_session_ids()
        with pytest.raises(HeinzelError, match="cannot record a draft workflow transition"):
            DraftJournal(service).record(session_id, "draft.probe", "probe", {}, "Probe")
        require_inbox(session_id)

    monkeypatch.setattr(service, "require_inbox_target", observe)
    caplog.set_level("INFO", logger="heinzel.mcp")
    with (
        owned_private_dispatch(root, plan) as (authority, dispatch),
        _approved_connection(plan, authority) as (_, lease),
    ):
        tools = dispatch.bind_heinzel_tools(kernel, lease, authentication="mcp-private-cli")
        with pytest.raises(HeinzelError):
            dispatch.bind_heinzel_tools(kernel, lease, authentication="cli")
        assert tools.targets() == (target,)
        tools.call(target.tool_name, HeinzelInput(request_id="message", message="Private probe"))
    with current.local("cli"):
        events = service.repository.control_read(first.session_id).events
        (accepted,) = (event for event in events if event.kind == "message.accepted")
        assert accepted.data["mcp_head"] == "mcp-private-cli"
        assert service.snapshot(second.session_id).messages == []
    journal = McpWorkJournal(SessionStore(root / "work"), plan)
    (reserve,) = (event for event in journal._store.read(journal._session).events if event.kind == "mcp.reserve")
    expected = {
        "mcp_head": "mcp-private-cli",
        "grant": accepted.data["mcp_grant"],
        "principal_digest": arguments_hash(current.owner().id),
    }
    assert reserve.data["mcp_authority"] == expected
    logged = [record_fields(record) for record in caplog.records if record.getMessage() == "mcp.ipc.work_transition"]
    assert len(logged) == 6
    assert all(event["mcp_authority"] == expected for event in logged)
