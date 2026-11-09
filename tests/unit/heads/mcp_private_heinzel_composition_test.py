"""The owned Unix peer, real MCP wire and private CLI using one durable grant journal."""

import json
import socket
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
from heinzel.heads.mcp._heinzel_tools import serve_admitted_heinzel_connection
from heinzel.heads.mcp._ipc_admission import McpGeneration, admit_owned_stream
from heinzel.heads.mcp._ipc_approval import PrivateMcpPlan
from heinzel.heads.mcp._ipc_descriptors import local_stream_pair
from heinzel.heads.mcp._ipc_work import owned_private_dispatch
from heinzel.kernel import CurrentUser, HeinzelError, HeinzelInvocations, HeinzelTarget

pytestmark = pytest.mark.unit
_TARGET = HeinzelTarget(kind="personal", identity="hz-" + "0" * 26)
_UNREVIEWED = HeinzelTarget(kind="personal", identity="hz-" + "1" * 26)
_TIMEOUT = 3


class SyntheticProvider(HeinzelInvocations):
    def __init__(self, current):
        self.current = current
        self.calls = []

    def require_current(self, target):
        assert self.current.context().authentication in {"cli", "mcp-stdio"}
        assert target == _TARGET

    def execute(self, target, request, check_active):
        self.require_current(target)
        check_active()
        self.calls.append((request.request_id, self.current.context().authentication))
        return {"request_id": request.request_id, "output": "Grüße aus der Probe"}


def _plan(name, *, dispatch=True, registration=None):
    return PrivateMcpPlan.capture(
        McpGeneration(registration or name, 1),
        f"grant-{name}",
        2,
        time.monotonic() + 60,
        {
            "dispatch": {_TARGET.key: [_TARGET.operation]} if dispatch else {},
            "heinzel_targets": [_TARGET.model_dump(mode="json")],
        },
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


def test_protocol_and_cli_share_durable_results_across_leases_and_broker_restart(
    make_kernel,
    tmp_path,
    monkeypatch,
    capsys,
):
    plan = _plan("first")
    module = Module("example", "Private probe")
    module.service(HeinzelInvocations, lambda: provider)
    kernel = make_kernel(example=module)
    provider = SyntheticProvider(kernel.resolve(CurrentUser))
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
            tools = dispatch.bind_heinzel_tools(kernel, cli_lease, authentication="cli")
            assert cli_lease.id != protocol_lease.id
            monkeypatch.setattr(sys, "argv", ["hz", "system", "mcp", "tools", "list"])
            with pytest.raises(SystemExit) as listed_from_cli:
                cli_main(private_heinzel_tools=tools)
            assert listed_from_cli.value.code == 0
            assert [tool["name"] for tool in json.loads(capsys.readouterr().out)] == [_TARGET.tool_name]
            assert _cli_invoke(tools, "shared", monkeypatch, capsys) == (0, first)
            assert provider.calls == [(json.loads(first)["request_id"], "mcp-stdio")]
            status, second = _cli_invoke(tools, "from-cli", monkeypatch, capsys)
            assert status == 0
            assert provider.calls[-1] == (json.loads(second)["request_id"], "cli")

            other = _plan("other", registration=plan.generation.registration_id)
            with (
                owned_private_dispatch(tmp_path.resolve() / "other", other) as (other_authority, other_dispatch),
                _approved_connection(other, other_authority) as (_, other_lease),
            ):
                with pytest.raises(HeinzelError):
                    other_dispatch.bind_heinzel_tools(kernel, cli_lease, authentication="cli")
                other_tools = other_dispatch.bind_heinzel_tools(kernel, other_lease, authentication="cli")
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
        tools = dispatch.bind_heinzel_tools(kernel, cli_lease, authentication="cli")
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
