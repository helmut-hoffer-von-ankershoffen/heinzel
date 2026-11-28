"""Parse and format a duration without referring to kernel window models."""

import re
from datetime import timedelta

DURATION_PATTERN = r"^[1-9][0-9]*[smhd]$"
DURATION_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def parse_duration(value: object) -> timedelta:
    """Turn `30s`, `5m`, `2h` or `1d` into a timedelta; a timedelta is returned as it is.

    Raises:
        ValueError: If the value is not text, or not a positive whole number followed by s, m, h or d.
    """
    if isinstance(value, timedelta):
        return value
    if not isinstance(value, str):  # e.g. a YAML number: `10` means nothing without a unit
        msg = f"Duration {value!r} must be text such as 10m; write `10m`"
        raise ValueError(msg)  # noqa: TRY004 - pydantic validators turn ValueError into a validation error
    if not re.fullmatch(DURATION_PATTERN, value, re.ASCII):
        msg = f"Duration {value!r} must be a positive whole number followed by s, m, h or d, e.g. 5m"
        raise ValueError(msg)
    try:
        return timedelta(**{DURATION_UNITS[value[-1]]: int(value[:-1])})
    except OverflowError as exc:
        msg = f"Duration {value!r} is too long; write something like `10m`"
        raise ValueError(msg) from exc


def format_duration(value: timedelta) -> str:
    """A timedelta as `parse_duration` reads it, in the largest whole unit: `7d`, `90m`, `45s`."""
    seconds = int(value.total_seconds())
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds % size == 0:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


# Keep the names used by pickles and older installed callers.
parse_duration.__module__ = "heinzel.kernel._duration"
format_duration.__module__ = "heinzel.kernel._duration"
