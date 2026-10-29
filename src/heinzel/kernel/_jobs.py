"""Jobs compiled from data rather than declared in code: workflow contracts (ADR 0021)."""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta

from heinzel.kernel._lets import JobLet


@dataclass(frozen=True, kw_only=True, slots=True)
class CompiledJobs:
    """Jobs compiled from data the module reads, rather than declared in code, e.g. workflow contracts (ADR 0021).

    One module provides it, as a service whose factory compiles them (`module.service(CompiledJobs, compile)`): in
    the core, `workflow`, the single compiler of every contract source. The daemon runs them beside the declared
    `JobLet`s as `<providing module>:<job>`, each job acting as a `Requester` of kind `job` of that module, which
    `Kernel.handle_for` serves with read handles only.
    """

    jobs: tuple[JobLet, ...] = ()


@dataclass(frozen=True, kw_only=True, slots=True)
class JobInactive:
    """A job with no configured work: report a skipped tick without persisting a receipt (ADR 0081)."""

    reason: str


_tick_interval: ContextVar[timedelta | None] = ContextVar("hz_daemon_tick_interval", default=None)


@contextmanager
def using_tick_interval(interval: timedelta) -> Generator[None]:
    """The foreground head's actual tick cadence, scoped to that loop and its calls.

    Yields:
        Nothing; the previous cadence is restored when the loop exits.
    """
    token = _tick_interval.set(interval)
    try:
        yield
    finally:
        _tick_interval.reset(token)


def tick_interval() -> timedelta | None:
    """The foreground loop's actual cadence, or None for an independently supervised tick."""
    return _tick_interval.get()
