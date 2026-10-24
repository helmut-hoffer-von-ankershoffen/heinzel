"""What every head needs to know about the operations it exposes: their path and their public parameters."""

import inspect
from dataclasses import dataclass
from typing import Any

from heinzel.kernel import Kernel, ModuleRecord, OperationLet, collapses, operation_path, owns_path, public_parameters

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
                path=operation_path(kernel, record, operation),
                parameters=public_parameters(kernel, operation),
                returns=signature.return_annotation,
            )
        )
    return result


__all__ = ["MCP_HTTP", "MCP_STDIO", "Exposed", "collapses", "exposed", "owns_path", "public_parameters", "restricts"]
