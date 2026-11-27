"""Signals a soundscape layer can follow (ADR 0058).

A layer binds its gain to a signal. `daypart` is the one theme variants follow too (`_theme_signals.py`, ADR 0060), so a
theme and a soundscape never disagree about when it is evening; `weather.rain` and `weather.wind` (0 to 1) are published
by a plugin and absent until one does.
"""

from typing import Literal, get_args

Signal = Literal["daypart", "weather.rain", "weather.wind"]
"""A signal by name: `daypart`, or a number from 0 to 1 a weather plugin publishes."""
SIGNALS: tuple[Signal, ...] = get_args(Signal)
NUMERIC_SIGNALS: frozenset[str] = frozenset({"weather.rain", "weather.wind"})
"""Signals whose value is a number from 0 to 1; a curve of points maps them to a gain."""
