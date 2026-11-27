"""Durations written the way people write them: `30s`, `5m`, `2h`, `1d`; and windows relative to a run, `next 7d`."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from heinzel.kernel._protocols import Window
from heinzel.spec import _duration as _plain_duration

DURATION_PATTERN = _plain_duration.DURATION_PATTERN
_UNITS = _plain_duration.DURATION_UNITS
format_duration = _plain_duration.format_duration
parse_duration = _plain_duration.parse_duration

MAX_WINDOW = timedelta(days=366)
"""The longest window a read may ask for, back or ahead: a year, so one read can't page through a whole history."""
WINDOW_PATTERN = r"^(?:(?:last|next) )?[1-9][0-9]*[smhd]$"
"""A window relative to a run, as contract inputs and discovery hints write it (ADR 0021, ADR 0037)."""


@dataclass(frozen=True, slots=True)
class Span:
    """A time window relative to a run: `last` ends when the run starts, `next` begins then."""

    direction: Literal["last", "next"]
    length: timedelta

    def at(self, run: datetime) -> Window:
        """The window of a run at `run`."""
        if self.direction == "next":
            return Window(since=run, until=run + self.length)
        return Window(since=run - self.length, until=run)

    def __str__(self) -> str:
        """As written: `7d` for the last seven days, `next 7d` for the coming seven."""
        length = format_duration(self.length)
        return f"next {length}" if self.direction == "next" else length


def parse_window(value: object) -> Span:
    """`7d` or `last 7d` (back from the run), or `next 7d` (ahead of it), at most `MAX_WINDOW` either way.

    Raises:
        ValueError: If it isn't written that way, or is longer than `MAX_WINDOW`.
    """
    if isinstance(value, Span):
        return value
    if not isinstance(value, str) or not re.fullmatch(WINDOW_PATTERN, value, re.ASCII):
        msg = f"window {value!r} must be a duration back from the run, e.g. `7d` or `last 7d`, or ahead, e.g. `next 7d`"
        raise ValueError(msg)
    direction, _, length = value.rpartition(" ")
    span = Span(direction="next" if direction == "next" else "last", length=parse_duration(length))
    if span.length > MAX_WINDOW:
        msg = f"window {value!r} is longer than {format_duration(MAX_WINDOW)}; narrow it"
        raise ValueError(msg)
    return span
