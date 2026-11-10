"""Exercise the pre-admission bridge boundary with inert Unix socket pairs."""

import select
import socket
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from heinzel.heads.mcp import _ipc_bridge as bridge
from heinzel.heads.mcp._ipc_descriptors import receive_exact

pytestmark = pytest.mark.unit

_ACK = b"admitted"


def _exercise_early_write():
    broker, channel = socket.socketpair()
    handshake = bridge.BridgeHandshake(b"fixture greeting", _ACK)
    with broker, channel, bridge.launch_inherited_bridge(channel, handshake) as front:
        assert receive_exact(broker, len(handshake.hello), time.monotonic() + 1) == handshake.hello
        front.sendall(b"must not reach the broker")
        broker.settimeout(1)
        assert broker.recv(1) == b""


def test_forked_bridge_retires_the_broker_channel_on_early_write():
    if sys.platform not in {"darwin", "linux"}:
        pytest.skip("The bridge requires the isolated Unix fork path")
    run = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import runpy, sys; runpy.run_path(sys.argv[1])['_exercise_early_write']()",
            __file__,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=6,
    )
    assert run.returncode == 0, run.stderr


@pytest.fixture
def streams():
    front, client = socket.socketpair()
    channel, broker = socket.socketpair()
    with front, client, channel, broker:
        yield front, client, channel, broker


@pytest.mark.parametrize("ack", [b"", _ACK[:3], _ACK], ids=["before-ack", "partial-ack", "simultaneous"])
@pytest.mark.parametrize("closure", [False, True], ids=["early-input", "early-eof"])
def test_refuses_front_readiness_even_with_broker_ack(streams, ack, closure):
    front, client, channel, broker = streams
    broker.sendall(ack)
    if closure:
        client.shutdown(socket.SHUT_WR)
    else:
        client.sendall(b"early")
    assert not bridge._await_acknowledgement(front, channel, _ACK, time.monotonic() + 1)


@pytest.mark.parametrize("ack", [b"", _ACK[:3], b"admXXted"], ids=["no-ack", "unfinished-ack", "wrong-ack"])
def test_requires_complete_exact_ack_within_deadline(streams, ack):
    front, _, channel, broker = streams
    broker.sendall(ack)
    assert not bridge._await_acknowledgement(front, channel, _ACK, time.monotonic() + 0.05)


def test_refuses_front_input_arriving_after_the_last_readiness_snapshot(streams, monkeypatch):
    front, client, channel, broker = streams
    broker.sendall(_ACK)
    readiness = select.poll()

    def poll(timeout):
        events = readiness.poll(timeout)
        if events == [(channel.fileno(), select.POLLIN)]:
            client.sendall(b"ambiguous")
        return events

    monkeypatch.setattr(
        bridge.select,
        "poll",
        lambda: SimpleNamespace(register=readiness.register, unregister=readiness.unregister, poll=poll),
    )
    assert not bridge._await_acknowledgement(front, channel, _ACK, time.monotonic() + 1)


def test_accepts_fragmented_exact_ack_without_consuming_following_bytes(streams, monkeypatch):
    front, _, channel, broker = streams
    broker.sendall(_ACK[:3])
    readiness = select.poll()
    waits = 0

    def poll(timeout):
        nonlocal waits
        if timeout:
            waits += 1
            if waits == 2:
                broker.sendall(_ACK[3:] + b"ready")
        return readiness.poll(timeout)

    monkeypatch.setattr(
        bridge.select,
        "poll",
        lambda: SimpleNamespace(register=readiness.register, unregister=readiness.unregister, poll=poll),
    )
    assert bridge._await_acknowledgement(front, channel, _ACK, time.monotonic() + 1)
    assert waits == 2
    assert channel.recv(5) == b"ready"
