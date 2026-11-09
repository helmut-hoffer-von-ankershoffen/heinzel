"""Installed pre-discovery CLI routing for an already-owned foreground registration."""

import json
import sys
from dataclasses import asdict
from typing import TYPE_CHECKING, Literal, cast

from heinzel.heads.mcp._ipc_lifecycle import REGISTRATION_ID, LifecycleAction
from heinzel.heads.mcp._ipc_registration import (
    InstalledMcpRegistry,
    UnverifiedMcpInventoryError,
    installed_mcp_authority,
)
from heinzel.kernel import (
    HeinzelError,
    HostServiceManager,
    Installation,
    Kernel,
    MaintenanceLocks,
    Settings,
    discover,
    host_maintenance_lock,
    native_mcp_scope,
)

if TYPE_CHECKING:
    from heinzel.heads.mcp._ipc_lifecycle import ForegroundMcpLifecycle, ForegroundMcpStatus

type LifecycleCommand = Literal["register", "recover", "start", "check", "stop"]
COMMANDS = ("register", "recover", "start", "check", "stop")


def main(action: LifecycleCommand, arguments: list[str], *, host: "ForegroundMcpLifecycle | None") -> int:
    """Resolve the verified installed host or use a pre-existing trusted foreground handle."""
    usage = f"Usage: hz system mcp {action} REGISTRATION_ID\n"
    if arguments in (["-h"], ["--help"]):
        sys.stdout.write(usage)
        return 0
    if len(arguments) != 1 or not REGISTRATION_ID.fullmatch(arguments[0]):
        sys.stderr.write(usage)
        return 2
    id_ = arguments[0]
    try:
        if host is None:
            return _installed(action, id_)
        if action in {"register", "recover"}:
            return 1
        return _report(host.perform(cast("LifecycleAction", action), id_))
    except (HeinzelError, OSError, ValueError):
        sys.stderr.write("The reviewed MCP lifecycle action was declined or unavailable.\n")
        return 1


def _installed(action: LifecycleCommand, id_: str) -> int:
    try:
        authority = installed_mcp_authority()
    except (HeinzelError, OSError, ValueError) as exc:
        message = (
            str(exc)
            if isinstance(exc, UnverifiedMcpInventoryError)
            else "MCP lifecycle requires a registration from the trusted installed host."
        )
        sys.stderr.write(message + "\n")
        return 1
    records, errors = discover(plugins=False)
    kernel = Kernel(Settings(state_dir=authority.runtime.state), records, errors)
    kernel.bind(Installation, authority.sdk)
    manager = kernel.resolve_builtin_service(HostServiceManager, "system.daemon")
    registry = InstalledMcpRegistry(authority, manager, native_mcp_scope())
    if action in {"register", "recover"}:
        registry.publish(action, id_)
        sys.stdout.write('{"registration":"committed","activation":"dormant"}\n')
        return 0
    if action == "stop":
        sys.stderr.write("Stop this MCP worker from its owning foreground session.\n")
        return 1
    host = registry.host(id_)
    try:
        if action == "check":
            # A separate CLI has no private channel for a running worker. Only prove a quiescent foreground scope.
            with MaintenanceLocks((host_maintenance_lock(authority.sdk.prefix),)):
                return _report(host.perform("check", id_))
        if not sys.stdin.isatty():
            sys.stderr.write("MCP Start needs a foreground terminal for Check and Stop.\n")
            return 1
        status = host.perform("start", id_)
        _report(status)
        return _foreground(host, id_) if status.worker == "running" else 1
    finally:
        host.close()


def _foreground(host: "ForegroundMcpLifecycle", id_: str) -> int:
    try:
        while True:
            sys.stderr.write("MCP foreground session. Enter check or stop.\n")
            sys.stderr.flush()
            command = sys.stdin.readline(16)
            if not command:
                return 1
            if command in {"check\n", "stop\n"}:
                action: LifecycleAction = "check" if command == "check\n" else "stop"
                try:
                    status = host.perform(action, id_)
                except (HeinzelError, OSError, ValueError):
                    sys.stderr.write("The reviewed MCP lifecycle action was declined or unavailable.\n")
                    continue
                result = _report(status)
                if action == "stop" and result == 0:
                    return 0
    except KeyboardInterrupt:
        return 1


def _report(status: "ForegroundMcpStatus") -> int:
    sys.stdout.write(json.dumps(asdict(status), sort_keys=True) + "\n")
    sys.stdout.flush()
    # A running worker and a reachable socket do not satisfy the product Start or Check acceptance contract.
    return 0 if status.action == "stop" and status.worker == "stopped" else 1
