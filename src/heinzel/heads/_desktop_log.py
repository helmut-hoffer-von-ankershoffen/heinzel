"""Structured log events for the desktop app flows (install, uninstall, open, recover).

Built on `heinzel.kernel._logging.log_event` and correlation (ADR 0138), each step logs one start and one outcome
event named `desktop.<operation>.<step>`, with operation, step, phase, outcome (ok or error), duration_ms, and for a
failure the error type and the same two lines the person reads (happened, action). What the step itself reports (an
installed, removed or deferred result) is the `state` field. No secrets: the flows handle only paths and versions.
"""

import logging
import time
from collections.abc import Generator
from contextlib import contextmanager, nullcontext

from heinzel.heads._desktop_messages import Problem
from heinzel.kernel import _logging

log = _logging.get_logger("desktop")


def problem_fields(problem: Problem | None) -> dict[str, object]:
    """The person's two lines as fields, so a failure event carries what the CLI printed."""
    return {} if problem is None else {"happened": problem.happened, "action": problem.action}


@contextmanager
def step(operation: str, name: str, **fields: object) -> Generator[dict[str, object]]:
    """Log the start of a desktop step, then its outcome; the body adds outcome fields to the yielded dict.

    A `outcome` the body puts in the dict is the step's result and is logged as `state`. An exception is the error
    outcome with its type and, when it carries one, its two-line problem, and is re-raised unchanged.

    Yields:
        A dict the step fills with outcome fields.
    """
    event = f"desktop.{operation}.{name}"
    base = {"operation": operation, "step": name, **fields}
    context = nullcontext() if _logging.correlation_id() else _logging.correlate()
    with context:
        _event(event, base | {"phase": "start"})
        started = time.monotonic()
        result: dict[str, object] = {}
        try:
            yield result
        except BaseException as exc:
            failure = (
                base
                | _reported(result)
                | {
                    "phase": "end",
                    "outcome": "error",
                    "error_type": type(exc).__name__,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                }
                | problem_fields(getattr(exc, "problem", None))
            )
            _event(event, failure, logging.ERROR)
            raise
        outcome = (
            base
            | _reported(result)
            | {
                "phase": "end",
                "outcome": "ok",
                "duration_ms": round((time.monotonic() - started) * 1000),
            }
        )
        _event(event, outcome)


def _event(name: str, fields: dict[str, object], level: int = logging.INFO) -> None:
    _logging.log_event(log, name, level=level, **fields)  # type: ignore[arg-type]  # no level or exc_info fields


def _reported(result: dict[str, object]) -> dict[str, object]:
    return {("state" if key == "outcome" else key): value for key, value in result.items()}
