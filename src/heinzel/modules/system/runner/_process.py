"""Runner compatibility names for the kernel's bounded process utility."""

from heinzel.kernel import (
    PROCESS_MAX_OUTPUT,
)
from heinzel.kernel import (
    ProcessExecute as Execute,
)
from heinzel.kernel import (
    ProcessOutcome as Outcome,
)
from heinzel.kernel import (
    run_bounded_process as run_bounded,
)
from heinzel.kernel import (
    signal_process_group as _signal_group,
)

__all__ = ["Execute", "Outcome", "_signal_group", "run_bounded"]

MAX_OUTPUT = PROCESS_MAX_OUTPUT


def whole_number(value: object) -> int | None:
    """`value` if it is an int and not a bool (JSON `true` is no token count), else None."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def amount(value: object) -> float | None:
    """`value` as a float if it is a number and not a bool, else None."""
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None
