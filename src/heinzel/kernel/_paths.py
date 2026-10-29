"""Where every head puts an operation: the CLI words, the MCP tool name parts and the REST path segments.

The kernel knows no head, but they all agree on one path per operation, and a module that builds a call another head
runs (`system.ui`'s server-built calls, ADR 0056) needs the same one.
"""

import inspect

from heinzel.kernel._kernel import Kernel, ModuleRecord
from heinzel.kernel._lets import OperationLet


def public_parameters(kernel: Kernel, operation: OperationLet) -> tuple[inspect.Parameter, ...]:
    """The parameters a caller supplies; injected ones (services, settings, the kernel) are left out."""
    return tuple(p for p in operation.signature().parameters.values() if not kernel.provides(p.annotation))


def collapses(kernel: Kernel, record: ModuleRecord) -> bool:
    """Whether a module is just one operation of its own name, so heads show it as a command, not a group."""
    operations = [let for owner, let in kernel.lets(OperationLet) if owner.qualname == record.qualname]
    return len(operations) == 1 and operations[0].name == record.module.name and not kernel.children(record.qualname)


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


def operation_path(kernel: Kernel, record: ModuleRecord, operation: OperationLet) -> tuple[str, ...]:
    """`("system", "doctor")`: the CLI words after `hz`, the MCP tool name parts, the REST path segments."""
    return record.path if owns_path(kernel, record, operation) else (*record.path, operation.name)
