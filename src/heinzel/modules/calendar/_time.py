"""Your time: the zone and working hours of your profile, and the times people type (`10:30`, `tomorrow 9:00`).

The profile is the `[org]` table of your configuration, read as configuration because modules don't import each
other (the notify module does the same): `timezone`, else this machine's zone, and `working_hours`, else 9 to 18,
the org module's default.
"""

import contextlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from heinzel import HeinzelError

DEFAULT_HOURS = (9, 18)
_CLOCK = re.compile(r"^(?:(?P<word>today|tomorrow)\s+)?(?P<hour>\d{1,2}):(?P<minute>\d{2})$")
EXAMPLES = "`10:30`, `tomorrow 9:00`, `2026-10-26 14:00` or `2026-10-26T14:00+01:00`"


@dataclass(frozen=True, slots=True)
class Profile:
    """Where and when you work: your zone, and the hours of a workday in it."""

    zone: tzinfo
    start: int
    end: int

    def now(self) -> datetime:
        """Now, in your zone."""
        return datetime.now(self.zone)

    def day(self, on: date) -> tuple[datetime, datetime]:
        """The midnights that start and end `on`, in your zone."""
        return (
            datetime.combine(on, time(), tzinfo=self.zone),
            datetime.combine(on + timedelta(days=1), time(), tzinfo=self.zone),
        )

    def workday(self, on: date) -> tuple[datetime, datetime]:
        """Your working hours on `on`, in your zone."""
        return (
            datetime.combine(on, time(self.start), tzinfo=self.zone),
            datetime.combine(on, time(0), tzinfo=self.zone) + timedelta(hours=self.end),
        )


def local_zone() -> tzinfo:
    """This machine's zone with its daylight-saving rules: `TZ`, else what /etc/localtime points at, else its offset."""
    name = os.environ.get("TZ", "").removeprefix(":")
    candidates = [name] if name and not name.startswith("/") else []
    resolved = Path("/etc/localtime").resolve()
    if "zoneinfo" in resolved.parts:
        candidates.append("/".join(resolved.parts[resolved.parts.index("zoneinfo") + 1 :]))
    for candidate in candidates:
        try:
            return ZoneInfo(candidate)
        except (ValueError, ZoneInfoNotFoundError):
            continue
    return datetime.now().astimezone().tzinfo or UTC


def profile(org: Mapping[str, Any]) -> Profile:
    """Your zone and working hours from the `[org]` table, or this machine's zone and 9 to 18."""
    zone = local_zone()
    name = org.get("timezone")
    if isinstance(name, str) and name:
        # A broken profile zone: org's doctor check names it; the calendar still shows, in this machine's zone.
        with contextlib.suppress(ValueError, ZoneInfoNotFoundError):
            zone = ZoneInfo(name)
    start, end = _hours(org.get("working_hours")) or DEFAULT_HOURS
    if not 0 <= start < end <= 24:  # noqa: PLR2004 - hours of a day
        start, end = DEFAULT_HOURS
    return Profile(zone=zone, start=start, end=end)


def _hours(value: object) -> tuple[int, int] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value) if value.strip().startswith("[") else value.split("-")
        except ValueError:
            return None
    if isinstance(value, list | tuple):
        pair = cast("list[object]", list(value))  # pyright: ignore[reportUnknownArgumentType] - from TOML or JSON
        if len(pair) != 2:  # noqa: PLR2004 - start and end
            return None
        try:
            return int(str(pair[0]).split(":")[0]), int(str(pair[1]).split(":")[0])
        except ValueError:
            return None
    return None


def parse_when(text: str, zone: tzinfo, now: datetime) -> datetime:
    """A time as people type it, in `zone` unless it names an offset: `10:30` (today), `tomorrow 9:00`, a date-time.

    Raises:
        HeinzelError: If it is none of these, with examples that work.
    """
    words = " ".join(text.strip().lower().split())
    if found := _CLOCK.fullmatch(words):
        hour, minute = int(found["hour"]), int(found["minute"])
        if hour > 23 or minute > 59:  # noqa: PLR2004 - a clock
            msg = f"{text!r} is no time of day; give one such as {EXAMPLES}."
            raise HeinzelError(msg)
        day = now.astimezone(zone).date() + timedelta(days=1 if found["word"] == "tomorrow" else 0)
        return datetime.combine(day, time(hour, minute), tzinfo=zone)
    try:
        moment = datetime.fromisoformat(text.strip())
    except ValueError:
        msg = f"{text!r} is no time hz reads; give one such as {EXAMPLES}."
        raise HeinzelError(msg) from None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=zone)
