"""Schedule arithmetic: cron slots in a window, through croniter rather than by hand (ADR 0020).

Cron expressions mean wall-clock time in the user's time zone, so slots are computed on naive local times and then
placed in the real zone (a `ZoneInfo`, never a fixed offset, which would be wrong for half the year). Daylight saving
time follows two rules:

- A wall-clock time that happens twice when the clocks go back runs once, at its first occurrence.
- A wall-clock time that doesn't exist when the clocks go forward runs shifted by the gap: 02:30 becomes 03:30.
"""

import os
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import CroniterBadDateError, croniter

MAX_SLOTS_PER_TICK = 100
"""`catch_up=all` replays at most this many slots per tick, oldest first; the next tick continues."""
FIELDS = 5
LOCALTIME = Path("/etc/localtime")


def local_zone(localtime: Path = LOCALTIME) -> tzinfo:
    """The user's time zone with its DST rules: `TZ` if set, else what /etc/localtime points at.

    Falls back to the current fixed offset only when neither names a zone, e.g. in a bare container.
    """
    name = os.environ.get("TZ", "").removeprefix(":")
    if name:
        try:
            if not name.startswith("/"):
                return ZoneInfo(name)
            with Path(name).open("rb") as handle:
                return ZoneInfo.from_file(handle, key=name)
        except (OSError, ValueError, ZoneInfoNotFoundError):
            pass
    resolved = localtime.resolve()
    if "zoneinfo" in resolved.parts:
        key = "/".join(resolved.parts[resolved.parts.index("zoneinfo") + 1 :])
        try:
            return ZoneInfo(key)
        except (ValueError, ZoneInfoNotFoundError):
            pass
    if localtime.is_file():
        with localtime.open("rb") as handle:
            return ZoneInfo.from_file(handle, key="localtime")
    fixed = datetime.now(UTC).astimezone().tzinfo
    return fixed if fixed is not None else UTC


def problem(schedule: str) -> str | None:
    """Why a cron expression can't be used, or None if it can."""
    if not (schedule.startswith("@") or len(schedule.split()) == FIELDS) or not croniter.is_valid(schedule):
        return f"invalid schedule {schedule!r}: use five cron fields (minute hour day month weekday) or @daily"
    try:
        croniter(schedule, datetime(2000, 1, 1)).get_next(datetime)  # noqa: DTZ001 - wall-clock arithmetic is naive
    except CroniterBadDateError:
        return f"invalid schedule {schedule!r}: it never fires"
    return None


def _place(naive: datetime, zone: tzinfo) -> datetime:
    """A naive wall-clock time in `zone`: the first occurrence of a repeated time, a missing one shifted by the gap."""
    return naive.replace(tzinfo=zone, fold=0).astimezone(UTC).astimezone(zone)


def _naive(moment: datetime, zone: tzinfo) -> datetime:
    return moment.astimezone(zone).replace(tzinfo=None)


def slots(
    schedule: str, after: datetime, until: datetime, zone: tzinfo, limit: int = MAX_SLOTS_PER_TICK
) -> list[datetime]:
    """Slots `s` with `after < s <= until`, oldest first, at most `limit`, each wall-clock time once."""
    found: list[datetime] = []
    iterator = croniter(schedule, _naive(after, zone))
    while len(found) < limit:
        slot = _place(iterator.get_next(datetime), zone)
        if slot > until:
            break
        if slot > after and (not found or slot > found[-1]):
            found.append(slot)
    return found


def latest(schedule: str, after: datetime, until: datetime, zone: tzinfo) -> datetime | None:
    """The most recent slot with `after < slot <= until`, or None."""
    # croniter's get_prev excludes its start, so start just past `until` to include a slot that falls on it.
    iterator = croniter(schedule, _naive(until, zone) + timedelta(seconds=1))
    slot = _place(iterator.get_prev(datetime), zone)
    while slot > until:  # a slot just before a gap can land after `until` once shifted
        slot = _place(iterator.get_prev(datetime), zone)
    return slot if slot > after else None


def next_slot(schedule: str, now: datetime, zone: tzinfo) -> datetime:
    """The first slot after `now`."""
    iterator = croniter(schedule, _naive(now, zone))
    slot = _place(iterator.get_next(datetime), zone)
    while slot <= now:
        slot = _place(iterator.get_next(datetime), zone)
    return slot
