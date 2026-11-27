"""Published theme-signal names and the kernel registration let (ADR 0060)."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any, Literal, get_args

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from heinzel.kernel._lets import Let
from heinzel.services.look._theme_signals import (
    _DAWN,  # noqa: F401 - old module attribute
    _DUSK_AFTER,  # noqa: F401 - old module attribute
    _DUSK_BEFORE,  # noqa: F401 - old module attribute
    _EVENING,  # noqa: F401 - old module attribute
    _MORNING_UNTIL,  # noqa: F401 - old module attribute
    _NIGHT_FROM,  # noqa: F401 - old module attribute
    DEFAULT_SUNRISE,
    DEFAULT_SUNSET,
    SIGNALS,
    Daypart,
    ThemeSignals,
    Weather,
    daypart,
)
from heinzel.services.look._theme_signals import DAYPARTS as _SERVICE_DAYPARTS
from heinzel.services.look._theme_signals import WEATHERS as _SERVICE_WEATHERS
from heinzel.services.look._theme_signals import _at as _service_at

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
_at = _service_at


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
