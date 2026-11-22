"""Update plan, apply, schedule and retry (context runtime.update, package updater).

Importing this package must stay light. A published Heinzel that has just replaced its own files (`hz system
update`) keeps its old kernel in memory and then loads single files of this package from the new tree, for
example to render the result. An eager import of the schedule here would ask that old kernel for names it never
had. So the public entry loads the schedule on first use (`tests/unit/cross_version_imports_test.py` pins this).
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from heinzel.modules.system.updater._schedule import Activity, AutoUpdateStatus, Schedule, StateFiles

__all__ = ["Activity", "AutoUpdateStatus", "Schedule", "StateFiles"]


def __getattr__(name: str) -> Any:  # noqa: ANN401 - the PEP 562 hook returns whichever public name was asked for
    if name in __all__:
        from heinzel.modules.system.updater import _schedule  # noqa: PLC0415 - loaded on first use, see above

        return getattr(_schedule, name)
    message = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(message)
