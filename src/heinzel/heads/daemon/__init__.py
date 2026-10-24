"""The daemon head: `hz system serve daemon` runs the tick in a foreground loop (ADR 0020).

launchd and systemd start `hz system daemon tick` themselves; this loop is for development, containers and CI. It
finds the tick through the kernel, like every head finds operations, and never imports the daemon module.
"""

import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from heinzel.kernel import HeinzelError, Kernel, OperationLet

TICK_MODULE = "system.daemon"
TICK_OPERATION = "tick"


def tick_operation(kernel: Kernel) -> OperationLet:
    """The `hz system daemon tick` operation.

    Raises:
        HeinzelError: If the daemon module is disabled.
    """
    for record, operation in kernel.lets(OperationLet):
        if record.qualname == TICK_MODULE and operation.name == TICK_OPERATION and kernel.available(operation.func):
            return operation
    msg = f"The {TICK_MODULE} module is disabled; remove it from disabled_modules to serve the daemon."
    raise HeinzelError(msg)


def serve(
    kernel: Kernel,
    interval: timedelta,
    report: Callable[[Any], None],
    *,
    ticks: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Tick every `interval` until interrupted, or `ticks` times, handing each result or error to `report`.

    A failing tick is reported and the loop goes on, as the next launchd or systemd firing would.

    Raises:
        HeinzelError: If the daemon module is disabled.
    """
    operation = tick_operation(kernel)
    done = 0
    while ticks is None or done < ticks:
        try:
            report(kernel.invoke(operation))
        except HeinzelError as exc:
            report(exc)
        done += 1
        if ticks is None or done < ticks:
            sleep(interval.total_seconds())
