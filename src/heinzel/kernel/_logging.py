"""Structured logging: stable event names with fields, one standard for every log Heinzelmännchen writes (ADR 0138).

An event's message is its name, `area.operation.step` (`desktop.install.download`); everything that varies goes
into fields, passed to stdlib logging as `extra`. `step` brackets one step of an operation with a start and an
outcome event and measures it; `correlate` ties every event of one operation together. `JsonFormatter` writes one
JSON object per line for log files, `TextFormatter` the same event and fields for a terminal. Field names that hold
secrets, tokens or email addresses are redacted, whatever their value.
"""

import json
import logging
import re
import time
import uuid
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, cast

type Outcome = Literal["ok", "error", "skipped"]
type LogFormat = Literal["text", "json"]

ROOT = "heinzel"
REDACTED = "[redacted]"

EVENT_NAME = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+")
"""Lower-case dotted segments, at least two: `area.operation` or `area.operation.step`."""

_AREA = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*")
_SECRET_KEY = re.compile(
    r"token|secret|passw(or)?d|api_?key|authorization|auth_header|cookie|credential|private_key|session_key|email",
    re.IGNORECASE,
)
_RECORD_ATTRIBUTES = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName"}

_correlation: ContextVar[str | None] = ContextVar("heinzel_log_correlation", default=None)


def get_logger(area: str) -> logging.Logger:
    """The logger of one area of Heinzelmännchen (`desktop`, `plugin.install`), named `heinzel.<area>`.

    Raises:
        ValueError: If `area` is not lower-case dotted words.
    """
    if not _AREA.fullmatch(area):
        msg = f"A log area is lower-case dotted words, like `desktop` or `plugin.install`, not {area!r}."
        raise ValueError(msg)
    return logging.getLogger(f"{ROOT}.{area}")


def redact(fields: Mapping[str, object]) -> dict[str, object]:
    """`fields` with the value of every key that names a secret, token or email address replaced, also nested."""
    return {key: REDACTED if _SECRET_KEY.search(key) else _redact_value(value) for key, value in fields.items()}


def _redact_value(value: object) -> object:
    if isinstance(value, Mapping):
        mapping = cast("Mapping[object, object]", value)  # any mapping; its keys are written as strings
        return redact({str(key): item for key, item in mapping.items()})
    if isinstance(value, list | tuple):
        items = cast("list[object] | tuple[object, ...]", value)
        return [_redact_value(item) for item in items]
    return value


def correlation_id() -> str | None:
    """The correlation id of the operation running in this context, if any."""
    return _correlation.get()


@contextmanager
def correlate(identifier: str | None = None) -> Generator[str]:
    """Tie every event logged inside to one operation: `identifier`, or a new id if none is given.

    Yields:
        The correlation id in effect inside.
    """
    token = _correlation.set(identifier or uuid.uuid4().hex[:12])
    try:
        yield _correlation.get() or ""
    finally:
        _correlation.reset(token)


def log_event(
    logger: logging.Logger,
    name: str,
    /,
    *,
    level: int = logging.INFO,
    exc_info: bool = False,
    **fields: object,
) -> None:
    """Log the event `name` with `fields`; fields that are None are left out, secret ones redacted.

    Raises:
        ValueError: If `name` is not `area.operation.step` or a field clashes with a log record attribute.
    """
    _emit(logger, name, level, fields, exc_info=exc_info)


def _emit(
    logger: logging.Logger, name: str, level: int, fields: Mapping[str, object], *, exc_info: bool = False
) -> None:
    if not EVENT_NAME.fullmatch(name):
        msg = f"An event name is `area.operation.step` in lower case, like `desktop.install.download`, not {name!r}."
        raise ValueError(msg)
    reserved = sorted(key for key in fields if key in _RECORD_ATTRIBUTES or key == "event")
    if reserved:
        msg = f"Log fields {reserved} clash with logging's own record attributes; name them differently."
        raise ValueError(msg)
    if not logger.isEnabledFor(level):
        return
    extra: dict[str, object] = {"event": name}
    if (current := _correlation.get()) is not None:
        extra["correlation_id"] = current
    extra |= redact({key: value for key, value in fields.items() if value is not None})
    logger.log(level, name, exc_info=exc_info, extra=extra)


@dataclass(slots=True)
class Step:
    """One running step: add fields to its outcome event with `note`, or end it as skipped with `skip`."""

    fields: dict[str, object] = field(default_factory=dict[str, object])
    outcome: Outcome = "ok"

    def note(self, **fields: object) -> None:
        """Add `fields` to the outcome event (a version found, a path chosen)."""
        self.fields |= fields

    def skip(self, reason: str) -> None:
        """End the step as skipped, for `reason`, instead of ok."""
        self.outcome = "skipped"
        self.fields["reason"] = reason


@contextmanager
def step(
    logger: logging.Logger,
    name: str,
    /,
    *,
    clock: Callable[[], float] = time.monotonic,
    **fields: object,
) -> Generator[Step]:
    """Log `name` once at the start and once with its outcome and `duration_ms`; an exception is the error outcome.

    The outermost step starts a correlation id if no operation has one yet. The error outcome carries
    `error_type` and `error` (the exception's message, written for the person) and the exception goes on.

    Yields:
        The running step, to add fields to its outcome or mark it skipped.
    """
    with _correlation_for_step():
        running = Step()
        _emit(logger, name, logging.INFO, fields | {"phase": "start"})
        started = clock()
        try:
            yield running
        except BaseException as exc:
            failure = {
                "phase": "end",
                "outcome": "error",
                "duration_ms": _milliseconds(clock() - started),
                "error_type": type(exc).__name__,
                "error": str(exc) or None,
            }
            _emit(logger, name, logging.ERROR, fields | running.fields | failure)
            raise
        outcome = {"phase": "end", "outcome": running.outcome, "duration_ms": _milliseconds(clock() - started)}
        _emit(logger, name, logging.INFO, fields | running.fields | outcome)


@contextmanager
def _correlation_for_step() -> Generator[None]:
    if _correlation.get() is not None:
        yield
        return
    with correlate():
        yield


def _milliseconds(seconds: float) -> int:
    return round(seconds * 1000)


def record_fields(record: logging.LogRecord) -> dict[str, object]:
    """The structured fields of `record`: whatever was passed as `extra`, redacted."""
    return redact({key: value for key, value in record.__dict__.items() if key not in _RECORD_ATTRIBUTES})


class JsonFormatter(logging.Formatter):
    """One JSON object per line: `at` (UTC), `level`, `logger`, `event` and the event's fields."""

    def format(self, record: logging.LogRecord) -> str:
        """The record as one line of JSON."""
        fields = record_fields(record)
        line: dict[str, object] = {
            "at": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": fields.pop("event", None) or record.getMessage(),
        }
        line |= fields
        if record.exc_info:
            line["traceback"] = self.formatException(record.exc_info)
        return json.dumps(line, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """`event key=value ...` for a person reading a terminal or a plain log."""

    def format(self, record: logging.LogRecord) -> str:
        """The record as one line of text, plus a traceback when there is one."""
        fields = record_fields(record)
        event = fields.pop("event", None) or record.getMessage()
        text = " ".join([str(event), *(f"{key}={_text_value(value)}" for key, value in fields.items())])
        if record.exc_info:
            text += "\n" + self.formatException(record.exc_info)
        return text


def _text_value(value: object) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    return json.dumps(text, ensure_ascii=False) if isinstance(value, str) and (" " in text or not text) else text


def formatter(log_format: LogFormat) -> logging.Formatter:
    """The formatter for `log_format`, the `log_format` setting (`HZ_LOG_FORMAT`) of a log file or stream."""
    return JsonFormatter() if log_format == "json" else TextFormatter()


__all__ = [
    "EVENT_NAME",
    "REDACTED",
    "JsonFormatter",
    "LogFormat",
    "Outcome",
    "Step",
    "TextFormatter",
    "correlate",
    "correlation_id",
    "formatter",
    "get_logger",
    "log_event",
    "record_fields",
    "redact",
    "step",
]
