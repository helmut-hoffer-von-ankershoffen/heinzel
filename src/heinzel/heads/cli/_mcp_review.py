"""Host-only terminal review entry point, before project and plugin discovery."""

import re
import sys
from typing import TYPE_CHECKING

from heinzel.kernel import HeinzelError

if TYPE_CHECKING:
    from heinzel.heads.mcp._ipc_supervisor import OwnedMcpSupervisor

_USAGE = "Usage: hz system mcp review PENDING_ID\n"
_INSTANCE = re.compile(r"[A-Za-z0-9_-]{32}\Z", re.ASCII)


def main(arguments: list[str], *, supervisor: "OwnedMcpSupervisor | None") -> int:
    """Review one exact pending connection through the native caller's already-owned supervisor."""
    if arguments in (["-h"], ["--help"]):
        sys.stdout.write(_USAGE + "Review a private MCP connection in the owned local runtime.\n")
        return 0
    if len(arguments) != 1 or not _INSTANCE.fullmatch(arguments[0]):
        sys.stderr.write(_USAGE)
        return 2
    if supervisor is None:
        sys.stderr.write("Local MCP review requires the active owned supervisor.\n")
        return 1
    try:
        supervisor.review(arguments[0])
    except (HeinzelError, OSError, ValueError):
        sys.stderr.write("Local MCP review was declined or unavailable.\n")
        return 1
    sys.stdout.write("The broker accepted the selected MCP connection review.\n")
    return 0
