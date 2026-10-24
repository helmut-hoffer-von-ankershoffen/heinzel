"""What every head needs to know about the operations it exposes: their path and their public parameters."""

import inspect
from dataclasses import dataclass
from typing import Any, get_origin

from heinzel.kernel import Kernel, ModuleRecord, OperationLet

MCP_STDIO = "mcp-stdio"
"""An operation's `heads` entry for MCP over stdio only: the client started the server as you, while streamable HTTP
MCP (head `mcp-http`) has no authentication, so whatever can reach its port could call it (ADR 0047)."""
MCP_HTTP = "mcp-http"


def restricts(heads: frozenset[str] | None, head: str) -> bool:
    """Whether `heads` leaves `head` out. `mcp` in `heads` covers both MCP transports, `mcp-stdio` only stdio."""
    if heads is None or head in heads:
        return False
    if head == MCP_HTTP:
        return "mcp" not in heads
    if head == "mcp":
        return MCP_STDIO not in heads
    return True


@dataclass(frozen=True, slots=True)
class Exposed:
    """An operation as a head sees it."""

    record: ModuleRecord
    operation: OperationLet
    path: tuple[str, ...]
    """`("system", "doctor")`: the CLI words, the MCP tool name parts, the REST path segments."""
    parameters: tuple[inspect.Parameter, ...]
    """Parameters a caller supplies; injected ones (services, settings, the kernel) are left out."""
    returns: Any
    """The return annotation, for output schemas."""

    @property
    def name(self) -> str:
        """A flat name for heads without hierarchy: `system_doctor`, `system_config_get`."""
        return "_".join(self.path).replace("-", "_")


def exposed(kernel: Kernel, head: str) -> list[Exposed]:
    """Every operation `head` should expose, in module order.

    An operation is left out when it needs a service only a disabled module provides, or when its `heads` restrict it
    to other heads. An operation that shares its module's name takes the module's own path (`owns_path`).
    """
    result: list[Exposed] = []
    for record, operation in kernel.lets(OperationLet):
        if not kernel.available(operation.func) or restricts(operation.heads, head):
            continue
        signature = operation.signature()
        result.append(
            Exposed(
                record=record,
                operation=operation,
                path=record.path if owns_path(kernel, record, operation) else (*record.path, operation.name),
                parameters=public_parameters(kernel, operation),
                returns=signature.return_annotation,
            )
        )
    return result


def public_parameters(kernel: Kernel, operation: OperationLet) -> tuple[inspect.Parameter, ...]:
    """The parameters a caller supplies; injected ones (services, settings, the kernel) are left out."""
    return tuple(
        p
        for p in operation.signature().parameters.values()
        if not kernel.provides(get_origin(p.annotation) or p.annotation)
    )


def owns_path(kernel: Kernel, record: ModuleRecord, operation: OperationLet) -> bool:
    """Whether `operation` is what its module's own path does: `hz system changelog`, `hz system expert`.

    It must share the module's name. A module whose only operation it is, without submodules, collapses into it. Beside
    other operations or submodules it is what the group does by itself (`hz system expert`, `GET /system/expert`, next
    to `hz system expert on`), but only when every parameter has a default: a required one would take a subcommand's
    name as its value, so such an operation keeps its own name instead (`hz tool tool ARG`).
    """
    if operation.name != record.module.name:
        return False
    if collapses(kernel, record):
        return True
    return all(p.default is not inspect.Parameter.empty for p in public_parameters(kernel, operation))


def collapses(kernel: Kernel, record: ModuleRecord) -> bool:
    """Whether a module is just one operation of its own name, so heads show it as a command, not a group."""
    operations = [let for owner, let in kernel.lets(OperationLet) if owner.qualname == record.qualname]
    return len(operations) == 1 and operations[0].name == record.module.name and not kernel.children(record.qualname)
