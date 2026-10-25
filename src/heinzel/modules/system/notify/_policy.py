"""The one notification policy (ADR 0043): whether a notice pops up, when, and whether it repeats an earlier one.

Pure functions of the settings, the profile's working hours, the time and the notices already recorded, so every
rule is a unit test with a fixed clock.
"""

import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from heinzel import Notice
from heinzel.modules.system.notify._models import NotifySettings

DEFAULT_HOURS = (9, 18)
"""The profile's default working hours (`[org] working_hours`), when it sets none or something unreadable."""
WORKDAYS = frozenset(range(5))
"""Monday to Friday: the profile has working hours, not working days, yet."""
_HOURS = re.compile(r"(\d{1,2})\s*[-,]\s*(\d{1,2})")
RATE_WINDOW = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class Hours:
    """When you work: from `start` to `end` o'clock on workdays, in `zone`."""

    start: int
    end: int
    zone: tzinfo


@dataclass(frozen=True, slots=True)
class Decision:
    """What the policy decided for a new notice."""

    popup: bool
    """Whether it pops up at all."""
    held: str | None
    """Why it doesn't, in words; None when it does."""
    due: datetime
    """When it may pop up."""


def local_zone() -> tzinfo:
    """This machine's time zone with its daylight-saving rules: `TZ`, else what /etc/localtime points at.

    Falls back to the current fixed offset only when neither names a zone, e.g. in a bare container.
    """
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


def working_hours(org: Mapping[str, Any], fallback: tzinfo) -> Hours:
    """Your working hours from the profile's `[org]` table: `working_hours` and `timezone`, or the defaults.

    The notify module reads the table itself, as configuration, because modules don't import each other.
    """
    start, end = _pair(org.get("working_hours")) or DEFAULT_HOURS
    if not 0 <= start < end <= 24:  # noqa: PLR2004 - hours of a day
        start, end = DEFAULT_HOURS
    zone = fallback
    name = org.get("timezone")
    if isinstance(name, str) and name:
        try:
            zone = ZoneInfo(name)
        except (ValueError, ZoneInfoNotFoundError):
            zone = fallback
    return Hours(start=start, end=end, zone=zone)


def _pair(value: object) -> tuple[int, int] | None:
    match value:
        case str() if value.strip().startswith("["):
            try:
                return _pair(json.loads(value))
            except ValueError:
                return None
        case str():
            found = _HOURS.fullmatch(value.strip())
            return (int(found[1]), int(found[2])) if found else None
        case [int() as start, int() as end] if not isinstance(start, bool) and not isinstance(end, bool):
            return start, end
        case _:
            return None


def next_start(hours: Hours, now: datetime) -> datetime | None:
    """The next start of your working hours, or None while you are working (a workday, between start and end)."""
    local = now.astimezone(hours.zone)
    if local.weekday() in WORKDAYS and hours.start <= local.hour < hours.end:
        return None
    day = local.date()
    if local.hour >= hours.start or local.weekday() not in WORKDAYS:
        day += timedelta(days=1)
    while day.weekday() not in WORKDAYS:
        day += timedelta(days=1)
    return datetime(day.year, day.month, day.day, hours.start, tzinfo=hours.zone)


def decide(
    settings: NotifySettings, hours: Hours, now: datetime, *, urgent: bool, recent: Sequence[Notice]
) -> Decision:
    """Whether and when a new notice pops up.

    Quiet mode holds everything, urgent notices too: it is the switch a person flips to be left alone. The rate
    limit counts the pop-ups of the last hour. Outside working hours a notice that isn't urgent waits for the next
    start of them instead of being lost.
    """
    if settings.quiet:
        return Decision(popup=False, held="quiet mode is on: hz system config set system.notify.quiet false", due=now)
    popups = sum(1 for notice in recent if notice.popup and now - RATE_WINDOW < notice.at <= now)
    if popups >= settings.per_hour:
        return Decision(
            popup=False,
            held=f"more than {settings.per_hour} pop-ups this hour: hz system config set system.notify.per_hour N",
            due=now,
        )
    if settings.working_hours and not urgent and (start := next_start(hours, now)) is not None:
        return Decision(popup=True, held=None, due=start)
    return Decision(popup=True, held=None, due=now)


def repeated(recent: Sequence[Notice], key: str, now: datetime, window: timedelta) -> Notice | None:
    """The notice with the same `key` recorded within `window`, which a new one would only repeat; None if none."""
    return next((notice for notice in recent if notice.key == key and now - window < notice.at <= now), None)
