"""Durations written the way people write them: `30s`, `5m`, `2h`, `1d`."""

import re
from datetime import timedelta

DURATION_PATTERN = r"^[1-9][0-9]*[smhd]$"
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def parse_duration(value: str | timedelta) -> timedelta:
    """Turn `30s`, `5m`, `2h` or `1d` into a timedelta; a timedelta is returned as it is.

    Raises:
        ValueError: If the text is not a positive whole number followed by s, m, h or d.
    """
    if isinstance(value, timedelta):
        return value
    if not re.fullmatch(DURATION_PATTERN, value):
        msg = f"Duration {value!r} must be a positive whole number followed by s, m, h or d, e.g. 5m"
        raise ValueError(msg)
    return timedelta(**{_UNITS[value[-1]]: int(value[:-1])})
