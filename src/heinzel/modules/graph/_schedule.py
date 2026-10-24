"""When team contracts run: cron slots in the user's time zone, by the daemon's rules (ADR 0020).

The daemon (`system.daemon._schedule`) decides when a contract runs; the graph must agree with it, or it invents
promises a daemon never makes. Modules don't import each other, so its rules are repeated here; keep them in step:

- Cron fields mean wall-clock time in the zone, so slots are computed on naive local times, then placed in the zone.
- A wall-clock time that happens twice when the clocks go back runs once, at its first occurrence.
- A wall-clock time that doesn't exist when the clocks go forward runs shifted by the gap: 02:30 becomes 03:30.
- The zone is `TZ` when set, else the zone file /etc/localtime points at, else the current fixed offset.
"""

import os
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

LOCALTIME = Path("/etc/localtime")
MAX_RUNS = 5000
"""Runs of one schedule looked at, newest first: five weeks of a contract that runs every ten minutes."""


def local_zone(localtime: Path = LOCALTIME) -> tuple[tzinfo, str]:
    """The user's time zone with its rules, and its name, as the daemon finds it."""
    name = os.environ.get("TZ", "").removeprefix(":")  # the daemon's own rule: launchd and systemd pass TZ
    if name and not name.startswith("/"):
        try:
            return ZoneInfo(name), name
        except (ValueError, ZoneInfoNotFoundError):
            pass
    resolved = localtime.resolve()
    if "zoneinfo" in resolved.parts:
        key = "/".join(resolved.parts[resolved.parts.index("zoneinfo") + 1 :])
        try:
            return ZoneInfo(key), key
        except (ValueError, ZoneInfoNotFoundError):
            pass
    if localtime.is_file():
        try:
            with localtime.open("rb") as handle:
                return ZoneInfo.from_file(handle, key="localtime"), "localtime"
        except (OSError, ValueError):
            pass
    fixed = datetime.now(UTC).astimezone().tzinfo
    return (fixed, str(fixed)) if fixed is not None else (UTC, "UTC")


def _place(naive: datetime, zone: tzinfo) -> datetime:
    return naive.replace(tzinfo=zone, fold=0).astimezone(UTC).astimezone(zone)


def _naive(moment: datetime, zone: tzinfo) -> datetime:
    return moment.astimezone(zone).replace(tzinfo=None)


def runs(schedule: str, since: datetime, until: datetime, zone: tzinfo, limit: int = MAX_RUNS) -> list[datetime]:
    """Runs `r` with `since < r <= until`, oldest first; the newest `limit` of them when there are more."""
    found: list[datetime] = []
    # croniter's get_prev excludes its start, so start just past `until` to include a run that falls on it.
    iterator = croniter(schedule, _naive(until, zone) + timedelta(seconds=1))
    while len(found) < limit:
        run = _place(iterator.get_prev(datetime), zone)
        if run <= since:
            break
        if run <= until and (not found or run < found[-1]):
            found.append(run)
    return found[::-1]


def next_run(schedule: str, after: datetime, zone: tzinfo) -> datetime:
    """The first run after `after`."""
    iterator = croniter(schedule, _naive(after, zone))
    run = _place(iterator.get_next(datetime), zone)
    while run <= after:
        run = _place(iterator.get_next(datetime), zone)
    return run
