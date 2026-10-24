"""What every head needs to know about the operations it exposes: their path and their public parameters."""

import inspect
from dataclasses import dataclass
from typing import Any, get_origin

from heinzel.kernel import Kernel, ModuleRecord, OperationLet


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
    to other heads. An operation that shares its module's name takes the module's own path: a module whose only
    operation it is (and that has no submodules) collapses into it, and beside other operations it is what the
    module's path does by itself (`hz system expert`, `GET /system/expert`, next to `hz system expert on`).
    """
    result: list[Exposed] = []
    for record, operation in kernel.lets(OperationLet):
        if not kernel.available(operation.func) or (operation.heads is not None and head not in operation.heads):
            continue
        own = operation.name == record.module.name
        signature = operation.signature()
        result.append(
            Exposed(
                record=record,
                operation=operation,
                path=record.path if own else (*record.path, operation.name),
                parameters=tuple(
                    p
                    for p in signature.parameters.values()
                    if not kernel.provides(get_origin(p.annotation) or p.annotation)
                ),
                returns=signature.return_annotation,
            )
        )
    return result


def collapses(kernel: Kernel, record: ModuleRecord) -> bool:
    """Whether a module is just one operation of its own name, so heads show it as a command, not a group."""
    operations = [let for owner, let in kernel.lets(OperationLet) if owner.qualname == record.qualname]
    return len(operations) == 1 and operations[0].name == record.module.name and not kernel.children(record.qualname)
