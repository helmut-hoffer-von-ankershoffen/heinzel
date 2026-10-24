"""Durations written the way people write them: `30s`, `5m`, `2h`, `1d`."""

import re
from datetime import timedelta

DURATION_PATTERN = r"^[1-9][0-9]*[smhd]$"
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


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
        return timedelta(**{_UNITS[value[-1]]: int(value[:-1])})
    except OverflowError as exc:
        msg = f"Duration {value!r} is too long; write something like `10m`"
        raise ValueError(msg) from exc
