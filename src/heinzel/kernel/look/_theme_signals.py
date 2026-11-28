"""Published theme-signal names and the kernel registration let (ADR 0060)."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any, Literal, get_args

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from heinzel.kernel._lets import Let
from heinzel.services.look._theme_signals import (
    DAWN,
    DEFAULT_SUNRISE,
    DEFAULT_SUNSET,
    DUSK_AFTER,
    DUSK_BEFORE,
    EVENING,
    MORNING_UNTIL,
    NIGHT_FROM,
    SIGNALS,
    Daypart,
    ThemeSignals,
    Weather,
    at_local_time,
    daypart,
)
from heinzel.services.look._theme_signals import DAYPARTS as _SERVICE_DAYPARTS
from heinzel.services.look._theme_signals import WEATHERS as _SERVICE_WEATHERS

# Keep the names previously exposed by this module, including explicit private attributes above.
__all__ = [
    "DAYPARTS",
    "DEFAULT_SUNRISE",
    "DEFAULT_SUNSET",
    "MAX_EVERY",
    "MIN_EVERY",
    "SIGNALS",
    "WEATHERS",
    "Any",
    "AwareDatetime",
    "BaseModel",
    "Callable",
    "ConfigDict",
    "Daypart",
    "Field",
    "Let",
    "Literal",
    "SignalLet",
    "ThemeSignals",
    "Weather",
    "dataclass",
    "datetime",
    "daypart",
    "get_args",
    "time",
    "timedelta",
]

DAYPARTS: tuple[Daypart, ...] = _SERVICE_DAYPARTS
WEATHERS: tuple[Weather, ...] = _SERVICE_WEATHERS
MIN_EVERY = timedelta(minutes=1)
MAX_EVERY = timedelta(days=1)
_DAWN = DAWN
_DUSK_AFTER = DUSK_AFTER
_DUSK_BEFORE = DUSK_BEFORE
_EVENING = EVENING
_MORNING_UNTIL = MORNING_UNTIL
_NIGHT_FROM = NIGHT_FROM
_at = at_local_time


@dataclass(frozen=True, kw_only=True, slots=True)
class SignalLet(Let):
    """A read-only operation of a module that publishes `ThemeSignals` (`@module.signals`); read by `system.theme`."""

    func: Callable[..., Any]
    """The operation's function; called with the kernel's injection, returning `ThemeSignals`."""
    every: timedelta
    """How long its values hold: `system.theme` calls it at most once per interval."""

    def __post_init__(self) -> None:
        """Refuse an interval under a minute or over a day.

        Raises:
            ValueError: If `every` is out of range.
        """
        if not MIN_EVERY <= self.every <= MAX_EVERY:
            msg = f"Signals {self.name!r} refresh every 1m to 1d, not {self.every}"
            raise ValueError(msg)
