"""Unix transport bootstrap without filesystem socket names."""

import os
import secrets
import socket
import time

from heinzel.heads.mcp._ipc_peer import require_owner_peer
from heinzel.kernel import HeinzelError
from heinzel.kernel._logging import get_logger, step

_TIMEOUT = 2.0
_REFUSED = "The local MCP descriptor transport is unavailable"
log = get_logger("mcp")


def local_stream_pair() -> tuple[socket.socket, socket.socket]:
    """Create two non-inheritable Unix stream descriptors without a bind or a pathname.

    Raises:
        HeinzelError: If this platform cannot supply the descriptor transport.
    """
    try:
        return socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    except (AttributeError, OSError):
        raise HeinzelError(_REFUSED) from None


def _receive(connection: socket.socket, size: int, deadline: float) -> bytes:
    data = bytearray()
    while len(data) < size:
        connection.settimeout(max(0, deadline - time.monotonic()))
        chunk = connection.recv(size - len(data))
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)


def probe_streams(probe: socket.socket, owned: socket.socket) -> None:
    """Check both owner peers and exchange one bounded challenge with the retained descriptor.

    Raises:
        HeinzelError: If the two descriptors do not form the expected owned connection.
    """
    for connection in (probe, owned):
        connection.settimeout(_TIMEOUT)
        require_owner_peer(connection, owner_uid=os.getuid())
    challenge = secrets.token_bytes(32)
    deadline = time.monotonic() + _TIMEOUT
    probe.sendall(challenge)
    if _receive(owned, len(challenge), deadline) != challenge:
        raise HeinzelError(_REFUSED)
    owned.sendall(challenge)
    if _receive(probe, len(challenge), deadline) != challenge:
        raise HeinzelError(_REFUSED)


def probe_local_descriptors() -> None:
    """Exchange a real challenge over an independent owner-verified pair. No lease is issued.

    Raises:
        HeinzelError: If the Unix round-trip or either kernel peer check fails.
    """
    with step(log, "mcp.ipc.probe_transport", operation="ipc", step="probe_transport"):
        try:
            probe, owned = local_stream_pair()
            with probe, owned:
                probe_streams(probe, owned)
        except OSError:
            raise HeinzelError(_REFUSED) from None
