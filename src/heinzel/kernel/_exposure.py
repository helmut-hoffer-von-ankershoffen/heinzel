"""What every head needs to know about the operations it exposes: their path and their public parameters."""

import inspect
from dataclasses import dataclass
from typing import Annotated, Any, get_args, get_origin

from pydantic import Field, TypeAdapter, create_model
from pydantic.fields import FieldInfo

from heinzel.kernel._kernel import Kernel, ModuleRecord
from heinzel.kernel._lets import OperationLet
from heinzel.kernel._paths import operation_path, public_parameters

MCP_STDIO = "mcp-stdio"
"""An operation's `heads` entry for MCP over stdio only: the client started the server as you, while streamable HTTP
MCP (head `mcp-http`) has no authentication, so whatever can reach its port could call it (ADR 0047)."""
MCP_HTTP = "mcp-http"
API_PRIVATE = "api-private"


def restricts(heads: frozenset[str] | None, head: str) -> bool:
    """Whether `heads` leaves `head` out. `mcp` in `heads` covers both MCP transports, `mcp-stdio` only stdio."""
    if heads is None or head in heads:
        return False
    if head == API_PRIVATE:
        return "api" not in heads
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
        if operation.private and head in {"api", MCP_HTTP}:
            continue
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


def input_schema(item: Exposed) -> dict[str, Any]:
    """The JSON Schema of what a caller supplies: the MCP tool's input schema, the REST request's shape.

    One model from the public parameters, each with its `Field(description=...)`, so every head and the documentation
    describe an operation's input the same way.
    """
    fields: dict[str, Any] = {}
    for parameter in item.parameters:
        annotation = parameter.annotation
        metadata = get_args(annotation)[1:] if get_origin(annotation) is Annotated else ()
        info = next((m for m in metadata if isinstance(m, FieldInfo)), None)
        description = info.description if info is not None else None
        default = ... if parameter.default is inspect.Parameter.empty else parameter.default
        fields[parameter.name] = (annotation, Field(default, description=description))
    model = create_model(item.name, **fields)
    return TypeAdapter(model).json_schema(mode="validation")
