"""What heads may use of the update context: the schedule the web head serves.

Heads import this file, never a private one (the "Heads only know what the kernel discovered" contract). It loads
the schedule on first use, like the package entry, so a published process that imports it after an in-place update
never asks its old kernel for new names (`tests/unit/cross_version_imports_test.py`).
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
