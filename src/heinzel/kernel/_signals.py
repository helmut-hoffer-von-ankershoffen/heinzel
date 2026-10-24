"""Signals: what the world around the person is like now, one vocabulary for everything that reacts to it (ADR 0058).

A soundscape's layer binds its gain to a signal; a dynamic theme may bind its look to the same ones. Each signal has
one definition here, so a theme and a soundscape never disagree about when it is evening. `daypart` is computed from
the local time; `weather.rain` and `weather.wind` (0 to 1) are published by a plugin and absent until one does.
"""

from datetime import datetime
from typing import Literal, get_args

Daypart = Literal["night", "morning", "afternoon", "evening"]
"""The part of the day: `night` before 06:00 and from 22:00, `morning` until 12:00, `afternoon` until 18:00."""
DAYPARTS: tuple[Daypart, ...] = get_args(Daypart)
Signal = Literal["daypart", "weather.rain", "weather.wind"]
"""A signal by name: `daypart`, or a number from 0 to 1 a weather plugin publishes."""
SIGNALS: tuple[Signal, ...] = get_args(Signal)
NUMERIC_SIGNALS: frozenset[str] = frozenset({"weather.rain", "weather.wind"})
"""Signals whose value is a number from 0 to 1; a curve of points maps them to a gain."""
_MORNING, _AFTERNOON, _EVENING, _NIGHT = 6, 12, 18, 22


def daypart_of(moment: datetime) -> Daypart:
    """The part of the day at `moment`, in the time zone it carries (the machine's own when it is naive)."""
    hour = moment.hour
    if _MORNING <= hour < _AFTERNOON:
        return "morning"
    if _AFTERNOON <= hour < _EVENING:
        return "afternoon"
    if _EVENING <= hour < _NIGHT:
        return "evening"
    return "night"
