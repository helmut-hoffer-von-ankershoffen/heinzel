"""Short-lived, supervisor-managed calendar reminder worker."""

import time
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime

from heinzel import NoticeKind, Notifications
from heinzel.kernel._calendar_reminders import Agenda, cycle

LIFETIME = 300.0


@dataclass(frozen=True)
class WorkerReads:
    read: Callable[[], Agenda]
    guard: Callable[[], AbstractContextManager[None]]


def run(
    read: Callable[[], Agenda] | WorkerReads,
    notifications: Notifications,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    """Read only with current consent, refresh at deadlines and exit periodically to pick up updates."""
    source = read if isinstance(read, WorkerReads) else WorkerReads(read, nullcontext)
    end = monotonic() + LIFETIME
    while end - monotonic() > 0:
        began = monotonic()
        with source.guard():
            delay = cycle(
                source.read, notifications, clock(), enabled=notifications.enabled(NoticeKind.CALENDAR), clock=clock
            )
        sleep(max(0.01, min(delay.total_seconds() - (monotonic() - began), end - monotonic())))
