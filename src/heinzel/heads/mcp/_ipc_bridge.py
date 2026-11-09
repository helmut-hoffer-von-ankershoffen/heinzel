"""An inherited-descriptor bridge launched by the already-trusted foreground supervisor."""

import os
import select
import signal
import socket
import sys
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field

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


def _bridge_child(front: socket.socket, channel: socket.socket, handshake: BridgeHandshake) -> None:
    status = 1
    try:
        kept = {front.fileno(), channel.fileno()}
        for entry in os.listdir("/dev/fd"):  # noqa: PTH208 - take a snapshot before closing inherited descriptors
            descriptor = int(entry)
            if descriptor not in kept:
                with suppress(OSError):  # Enumeration can include its own already-closed descriptor.
                    os.close(descriptor)
        channel.settimeout(_IO_SECONDS)
        channel.sendall(handshake.hello)
        if (
            receive_exact(channel, len(handshake.acknowledged), time.monotonic() + _ACK_SECONDS)
            == handshake.acknowledged
        ):
            _relay(front, channel)
            status = 0
    finally:
        os._exit(status)


def _exited(pid: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while True:
        if os.waitpid(pid, os.WNOHANG)[0] == pid:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.005)


def _retire_child(pid: int) -> None:
    if not _exited(pid, _IO_SECONDS):
        # This still-unreaped direct child is owned by the supervisor. There is no PID discovery.
        with suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
        if not _exited(pid, _IO_SECONDS):
            raise HeinzelError(_REFUSED)


@contextmanager
def launch_inherited_bridge(channel: socket.socket, handshake: BridgeHandshake) -> Generator[socket.socket]:
    """Give only the forked bridge a private peer and return the restricted front stream.

    Raises:
        HeinzelError: If this runtime cannot launch or retire its isolated direct child.

    Yields:
        A byte stream that forwards nothing to the broker before its exact acknowledgement.
    """
    if (
        sys.platform not in {"darwin", "linux"}
        or not sys.flags.isolated
        or threading.current_thread() is not threading.main_thread()
        or threading.active_count() != 1
        or signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL
    ):
        raise HeinzelError(_REFUSED)
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
            _retire_child(pid)
