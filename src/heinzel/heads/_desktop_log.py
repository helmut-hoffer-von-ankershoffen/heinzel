"""Structured log events for the desktop app flows (install, uninstall, open, recover).

Each step logs one start and one outcome event. The message is the stable event name (`desktop.<operation>.<step>`);
everything else is a field on the record (`extra`), never formatted into the message: operation, step, phase
(start or end), outcome, path, version, duration_ms, and for a failure the same two lines the person reads
(happened, action) plus the error type. No secrets: the flows handle only paths and version numbers.
"""

import logging
import time
from collections.abc import Generator
from contextlib import contextmanager

from heinzel.heads._desktop_messages import Problem

log = logging.getLogger("heinzel.desktop")


def problem_fields(problem: Problem | None) -> dict[str, object]:
    """The person's two lines as fields, so a failure event carries what the CLI printed."""
    return {} if problem is None else {"happened": problem.happened, "action": problem.action}


@contextmanager
def step(operation: str, name: str, **fields: object) -> Generator[dict[str, object]]:
    """Log the start of a step, then its outcome; the body adds outcome fields to the yielded dict.

    An exception is logged as outcome "failed" with its type and, when it carries one, its two-line problem, then
    re-raised unchanged.

    Yields:
        A dict the step fills with outcome fields (`outcome` defaults to "ok").
    """
    event = f"desktop.{operation}.{name}"
    base = {"event": event, "operation": operation, "step": name, **fields}
    log.info(event, extra={**base, "phase": "start"})
    started = time.monotonic()
    result: dict[str, object] = {}
    try:
        yield result
    except BaseException as exc:
        failure = {
            **base,
            **result,
            "phase": "end",
            "outcome": "failed",
            "error": type(exc).__name__,
            "duration_ms": round((time.monotonic() - started) * 1000),
            **problem_fields(getattr(exc, "problem", None)),
        }
        log.warning(event, extra=failure)
        raise
    outcome = {"outcome": "ok", **base, **result, "phase": "end"}
    log.info(event, extra={**outcome, "duration_ms": round((time.monotonic() - started) * 1000)})
