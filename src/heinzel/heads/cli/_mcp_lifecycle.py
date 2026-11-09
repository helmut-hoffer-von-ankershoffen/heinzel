"""Installed pre-discovery CLI routing for an already-owned foreground registration."""

import json
import sys
from dataclasses import asdict
from typing import TYPE_CHECKING, Literal, cast

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
from heinzel.kernel._mcp_handoff import REGISTRATION_ID

if TYPE_CHECKING:
    from heinzel.heads.mcp._ipc_install_review import McpInstallReview
    from heinzel.heads.mcp._ipc_lifecycle import ForegroundMcpLifecycle, ForegroundMcpStatus, LifecycleAction

type LifecycleCommand = Literal["register", "recover", "start", "check", "stop"]
COMMANDS = ("register", "recover", "start", "check", "stop")


def review_install(arguments: list[str], *, host: "McpInstallReview | None") -> int:
    """Review an installed client's exact plan without performing installation."""
    match arguments:
        case [registration, "--handoff", handoff] if REGISTRATION_ID.fullmatch(registration):
            pass
        case _:
            sys.stderr.write("Usage: hz system mcp review-install REGISTRATION_ID --handoff PLAN\n")
            return 2
    if host is not None:
        try:
            host.review(registration, handoff)
        except (HeinzelError, OSError, ValueError):
            pass
        else:
            sys.stdout.write('{"review":"accepted","installation":"not-applied"}\n')
            return 0
    sys.stderr.write("The local MCP Install review was declined or unavailable.\n")
    return 1


def main(action: LifecycleCommand, arguments: list[str], *, host: "ForegroundMcpLifecycle | None") -> int:
    """Resolve the verified installed host or use a pre-existing trusted foreground handle."""
    can_handoff = action in {"start", "check", "stop"}
    usage = f"Usage: hz system mcp {action} REGISTRATION_ID{' [--handoff PLAN]' if can_handoff else ''}\n"
    if arguments in (["-h"], ["--help"]):
        sys.stdout.write(usage)
        return 0
    handoff: str | None
    match arguments:
        case [id_, "--handoff", handoff] if can_handoff and REGISTRATION_ID.fullmatch(id_):
            pass
        case [id_] if REGISTRATION_ID.fullmatch(id_):
            handoff = None
        case _:
            sys.stderr.write(usage)
            return 2
    try:
        if host is None:
            return _installed(action, id_, handoff)
        if action in {"register", "recover"}:
            return 1
        return _report(host.perform(cast("LifecycleAction", action), id_, handoff=handoff))
    except (HeinzelError, OSError, ValueError):
        sys.stderr.write("The reviewed MCP lifecycle action was declined or unavailable.\n")
        return 1


def _installed(action: LifecycleCommand, id_: str, handoff: str | None) -> int:
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
                return _report(host.perform("check", id_, handoff=handoff))
        if not sys.stdin.isatty():
            sys.stderr.write("MCP Start needs a foreground terminal for Check and Stop.\n")
            return 1
        status = host.perform("start", id_, handoff=handoff)
        _report(status)
        return _foreground(host, id_) if status.worker == "running" else 1
    finally:
        host.close()


def _foreground(host: "ForegroundMcpLifecycle", id_: str) -> int:
    try:
        while True:
            sys.stderr.write("MCP foreground session. Enter check or stop.\n")
            sys.stderr.flush()
            command = sys.stdin.readline(96)
            if not command:
                return 1
            if not command.endswith("\n"):
                return 1
            match command.split():
                case [("check" | "stop") as action, "--handoff", handoff]:
                    pass
                case [("check" | "stop") as action]:
                    handoff = None
                case _:
                    continue
            try:
                status = host.perform(action, id_, handoff=handoff)
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
