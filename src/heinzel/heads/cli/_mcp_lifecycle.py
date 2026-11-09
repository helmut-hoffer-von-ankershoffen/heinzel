"""Installed pre-discovery CLI routing for an already-owned foreground registration."""

import json
import sys
from dataclasses import asdict
from typing import TYPE_CHECKING

from heinzel.heads.mcp._ipc_lifecycle import REGISTRATION_ID, LifecycleAction
from heinzel.kernel import HeinzelError

if TYPE_CHECKING:
    from heinzel.heads.mcp._ipc_lifecycle import ForegroundMcpLifecycle


def main(action: LifecycleAction, arguments: list[str], *, host: "ForegroundMcpLifecycle | None") -> int:
    """Run the same exact host lifecycle action used by a trusted foreground native caller."""
    usage = f"Usage: hz system mcp {action} REGISTRATION_ID\n"
    if arguments in (["-h"], ["--help"]):
        sys.stdout.write(usage)
        return 0
    if len(arguments) != 1 or not REGISTRATION_ID.fullmatch(arguments[0]):
        sys.stderr.write(usage)
        return 2
    if host is None:
        sys.stderr.write("MCP lifecycle requires a registration from the trusted installed host.\n")
        return 1
    try:
        status = host.perform(action, arguments[0])
    except (HeinzelError, OSError, ValueError):
        sys.stderr.write("The reviewed MCP lifecycle action was declined or unavailable.\n")
        return 1
    sys.stdout.write(json.dumps(asdict(status), sort_keys=True) + "\n")
    # A running worker and a reachable socket do not satisfy the product Start or Check acceptance contract.
    return 0 if action == "stop" and status.worker == "stopped" else 1
