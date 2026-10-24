"""Signals: the few facts about now a theme's variants follow, the time of day and the weather (ADR 0058).

The vocabulary is closed: `daypart` (computed by `system.theme` from the person's time zone and, when a module knows
them, sunrise and sunset) and `weather` (published by a module). A module publishes signals with
`@module.signals(every=...)`, a read-only operation returning `ThemeSignals`; `system.theme` calls it at most once per
interval and hands the values to the web app. A signal is a word from its list, never free text, so it can't carry
anything into CSS.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any, Literal, get_args

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from heinzel.kernel._lets import Let

Daypart = Literal["dawn", "morning", "day", "dusk", "evening", "night"]
"""The time of day, computed by the core: dawn and dusk around sunrise and sunset, night from late evening."""
Weather = Literal["clear", "clouds", "rain", "drizzle", "thunder", "snow", "fog", "wind", "heat", "cold"]
"""The weather now, published by a module such as a weather plugin."""
DAYPARTS: tuple[Daypart, ...] = get_args(Daypart)
WEATHERS: tuple[Weather, ...] = get_args(Weather)
SIGNALS = {"daypart": DAYPARTS, "weather": WEATHERS}
"""Every signal a theme can name, with its values."""
MIN_EVERY = timedelta(minutes=1)
MAX_EVERY = timedelta(days=1)
DEFAULT_SUNRISE = time(6, 30)
DEFAULT_SUNSET = time(18, 30)
"""Sunrise and sunset when no module knows them: the dayparts still move through the day."""
_DAWN = timedelta(minutes=45)
_DUSK_BEFORE = timedelta(minutes=60)
_DUSK_AFTER = timedelta(minutes=30)
_MORNING_UNTIL = time(11, 0)
_NIGHT_FROM = time(22, 30)
_EVENING = timedelta(minutes=30)


class ThemeSignals(BaseModel):
    """What a module knows about now, for themes; every field is optional, unknown ones are refused."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    weather: Weather | None = Field(default=None, description="The weather now, e.g. `rain`.")
    sunrise: AwareDatetime | None = Field(default=None, description="Today's sunrise where the person is.")
    sunset: AwareDatetime | None = Field(default=None, description="Today's sunset where the person is.")


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


def _at(day: datetime, moment: time) -> datetime:
    return day.replace(hour=moment.hour, minute=moment.minute, second=0, microsecond=0)


def daypart(now: datetime, sunrise: datetime | None = None, sunset: datetime | None = None) -> Daypart:
    """The daypart of `now` (aware, in the person's zone), from today's sunrise and sunset or the defaults.

    Dawn is 45 minutes either side of sunrise, morning until 11:00 (or dawn's end), day until an hour before sunset,
    dusk until half an hour after it, evening until 22:30 (or half an hour more), night until dawn. Sun times that
    don't fit that order within the day (a polar day, a provider's mistake) fall back to the defaults.
    """
    rise = sunrise.astimezone(now.tzinfo) if sunrise else None
    down = sunset.astimezone(now.tzinfo) if sunset else None
    rise = _at(now, rise.time()) if rise else _at(now, DEFAULT_SUNRISE)
    down = _at(now, down.time()) if down else _at(now, DEFAULT_SUNSET)
    # In a northern summer the sun sets late and rises early: the day stretches, night starts after the evening.
    start_day = max(_at(now, _MORNING_UNTIL), rise + _DAWN)
    evening = down + _DUSK_AFTER
    night = max(_at(now, _NIGHT_FROM), evening + _EVENING)
    edges = [rise - _DAWN, rise + _DAWN, start_day, down - _DUSK_BEFORE, evening]
    if edges != sorted(edges) or night.date() != now.date() or edges[0].date() != now.date():
        return daypart(now)
    start_dawn, start_morning, start_day, start_dusk, start_evening = edges
    if now < start_dawn or now >= night:
        return "night"
    parts: tuple[tuple[datetime, Daypart], ...] = (
        (start_evening, "evening"),
        (start_dusk, "dusk"),
        (start_day, "day"),
        (start_morning, "morning"),
    )
    for start, part in parts:
        if now >= start:
            return part
    return "dawn"
