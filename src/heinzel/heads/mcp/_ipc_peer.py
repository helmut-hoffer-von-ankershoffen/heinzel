"""OS peer ownership check for a managed local MCP connection."""

import socket
import struct
import sys

from heinzel.kernel import HeinzelError
from heinzel.kernel._logging import get_logger, step

log = get_logger("mcp")
_LINUX_UCRED = struct.Struct("=iII")  # pid_t, uid_t, gid_t
_DARWIN_XUCRED_HEADER = struct.Struct("=II")  # layout version, effective uid
_DARWIN_SOL_LOCAL = 0  # sys/un.h, missing from Python's socket constants
_DARWIN_LOCAL_PEERCRED = 1  # sys/un.h, also available as socket.LOCAL_PEERCRED on newer Python
_DARWIN_XUCRED_VERSION = 0  # sys/ucred.h


def _peer_uid(connection: socket.socket) -> int:
    if sys.platform == "darwin":
        # Leave room for the advisory group list in xucred. Only its versioned header matters here.
        credentials = connection.getsockopt(_DARWIN_SOL_LOCAL, _DARWIN_LOCAL_PEERCRED, 256)
        version, uid = _DARWIN_XUCRED_HEADER.unpack_from(credentials)
        if version == _DARWIN_XUCRED_VERSION:
            return int(uid)
    elif sys.platform == "linux":
        credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, _LINUX_UCRED.size)
        _, uid, _ = _LINUX_UCRED.unpack(credentials)
        return int(uid)
    msg = "Local MCP peer credentials are unavailable"
    raise OSError(msg)


def require_owner_peer(connection: socket.socket, *, owner_uid: int) -> None:
    """Require the kernel-reported effective UID of a connected Unix stream to match the trusted owner.

    The broker supplies the owner UID from its verified local state. Keep the same socket for admission.
    This check does not establish socket path ownership, liveness, client identity or any MCP grant.

    Raises:
        HeinzelError: If the peer cannot be verified as the owner.
    """
    with step(log, "mcp.ipc.verify_peer", operation="ipc", step="verify_peer"):
        try:
            if (
                connection.family == socket.AF_UNIX
                and connection.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) == socket.SOCK_STREAM
                and isinstance(connection.getpeername(), (str, bytes))
                and _peer_uid(connection) == owner_uid
            ):
                return
        except (OSError, struct.error):
            pass  # Do not expose socket paths, OS errors or credentials.
        msg = "Local MCP requires a verified owner peer on a connected Unix stream"
        raise HeinzelError(msg)
