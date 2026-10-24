"""Schedule arithmetic: cron slots in a window, through croniter rather than by hand (ADR 0020)."""

from datetime import UTC, datetime, timedelta

from croniter import CroniterBadDateError, croniter

MAX_SLOTS_PER_TICK = 100
"""`catch_up=all` replays at most this many slots per tick, oldest first; the next tick continues."""
FIELDS = 5


def problem(schedule: str) -> str | None:
    """Why a cron expression can't be used, or None if it can."""
    if not (schedule.startswith("@") or len(schedule.split()) == FIELDS) or not croniter.is_valid(schedule):
        return f"invalid schedule {schedule!r}: use five cron fields (minute hour day month weekday) or @daily"
    try:
        croniter(schedule, datetime(2000, 1, 1, tzinfo=UTC)).get_next(datetime)
    except CroniterBadDateError:
        return f"invalid schedule {schedule!r}: it never fires"
    return None


def slots(schedule: str, after: datetime, until: datetime, limit: int = MAX_SLOTS_PER_TICK) -> list[datetime]:
    """Slots `s` with `after < s <= until`, oldest first, at most `limit`."""
    found: list[datetime] = []
    iterator = croniter(schedule, after)
    while len(found) < limit:
        slot = iterator.get_next(datetime)
        if slot > until:
            break
        found.append(slot)
    return found


def latest(schedule: str, after: datetime, until: datetime) -> datetime | None:
    """The most recent slot with `after < slot <= until`, or None."""
    # croniter's get_prev excludes its start, so start just past `until` to include a slot that falls on it.
    slot = croniter(schedule, until + timedelta(seconds=1)).get_prev(datetime)
    return slot if slot > after else None


def next_slot(schedule: str, now: datetime) -> datetime:
    """The first slot after `now`."""
    return croniter(schedule, now).get_next(datetime)
