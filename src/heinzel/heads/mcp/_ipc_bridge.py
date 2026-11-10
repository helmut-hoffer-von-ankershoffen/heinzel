"""An inherited-descriptor bridge launched by the already-trusted foreground supervisor."""

import math
import os
import select
import socket
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Never

from heinzel.heads.mcp._ipc_child import OwnedMcpChild, close_unrelated_descriptors, require_isolated_fork
from heinzel.heads.mcp._ipc_descriptors import local_stream_pair, receive_exact
from heinzel.kernel import HeinzelError

_IO_SECONDS = 2.0
_ACK_SECONDS = 62.0  # The host review window plus time to send the acknowledgement.
_REFUSED = "The inherited local MCP bridge is unavailable"


@dataclass(frozen=True, slots=True)
class BridgeHandshake:
    """Versioned exact bytes for one private descriptor and one live supervisor incarnation."""

    hello: bytes = field(repr=False)
    acknowledged: bytes = field(repr=False)

    def require_hello(self, channel: socket.socket) -> None:
        """Verify only the bootstrap message. Protocol bytes are still forbidden.

        Raises:
            HeinzelError: If the private greeting differs from this channel's binding.
        """
        if receive_exact(channel, len(self.hello), time.monotonic() + _IO_SECONDS) != self.hello:
            raise HeinzelError(_REFUSED)

    def acknowledge(self, channel: socket.socket) -> None:
        """Signal admission on the private channel after the broker published the lease.

        Raises:
            HeinzelError: If the exact channel no longer accepts the acknowledgement.
        """
        try:
            channel.settimeout(_IO_SECONDS)
            channel.sendall(self.acknowledged)
            channel.settimeout(None)
        except OSError:
            raise HeinzelError(_REFUSED) from None


def _relay(front: socket.socket, channel: socket.socket) -> None:
    front.settimeout(_IO_SECONDS)
    channel.settimeout(_IO_SECONDS)
    client_reads = True
    while True:
        incoming = (channel, front) if client_reads else (channel,)
        ready, _, _ = select.select(incoming, (), ())
        for source in ready:
            data = source.recv(65536)
            if source is channel:
                if not data:
                    return  # Retiring the broker lease retires the bridge as well.
                front.sendall(data)
            elif data:
                channel.sendall(data)
            else:
                channel.shutdown(socket.SHUT_WR)
                client_reads = False


def _await_acknowledgement(front: socket.socket, channel: socket.socket, expected: bytes, deadline: float) -> bool:
    """Read only the private acknowledgement while refusing any front readiness."""
    if not expected:
        return False
    readiness = select.poll()
    readiness.register(front, select.POLLIN)
    readiness.register(channel, select.POLLIN)
    channel.settimeout(0.0)
    received = bytearray()
    while len(received) < len(expected):
        remaining = deadline - time.monotonic()
        # Only a readable, otherwise healthy broker channel is unambiguous.
        if remaining <= 0 or readiness.poll(math.ceil(remaining * 1000)) != [(channel.fileno(), select.POLLIN)]:
            return False
        chunk = channel.recv(len(expected) - len(received))
        if not chunk:
            return False
        received.extend(chunk)
        if not expected.startswith(received):
            return False
    readiness.unregister(channel)
    # Check the front again for arrivals between the last readiness snapshot and the acknowledgement read.
    return not readiness.poll(0) and time.monotonic() < deadline


def _bridge_child(front: socket.socket, channel: socket.socket, handshake: BridgeHandshake) -> Never:
    status = 1
    try:
        close_unrelated_descriptors(front.fileno(), channel.fileno())
        channel.settimeout(_IO_SECONDS)
        channel.sendall(handshake.hello)
        if _await_acknowledgement(front, channel, handshake.acknowledged, time.monotonic() + _ACK_SECONDS):
            _relay(front, channel)
            status = 0
    finally:
        os._exit(status)


@contextmanager
def launch_inherited_bridge(channel: socket.socket, handshake: BridgeHandshake) -> Generator[socket.socket]:
    """Give only the forked bridge a private peer and return the restricted front stream.

    Raises:
        HeinzelError: If this runtime cannot launch or retire its isolated direct child.

    Yields:
        A byte stream that refuses input and closure before the broker's exact acknowledgement.
    """
    require_isolated_fork(_REFUSED)
    front, child_front = local_stream_pair()
    with front, child_front:
        try:
            pid = os.fork()
        except OSError:
            raise HeinzelError(_REFUSED) from None
        if pid == 0:
            _bridge_child(child_front, channel, handshake)
        child_front.close()
        channel.close()
        try:
            yield front
        finally:
            front.close()
            OwnedMcpChild(pid).retire(_IO_SECONDS, _REFUSED)
