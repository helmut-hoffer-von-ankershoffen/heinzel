"""Pure planning for calendar reminders; no provider access or background work occurs here."""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from heinzel import Event

LEAD = timedelta(minutes=5)


@dataclass(frozen=True)
class Reminder:
    """A boundary and its exclusive expiry, identified independently of event title."""

    key: str
    title: str
    kind: str
    due: datetime
    expires: datetime


def plan(events: list[Event], now: datetime) -> list[Reminder]:
    """Plan the latest-starting current and earliest upcoming meeting at an aware instant.

    The caller must refresh the provider before acting and discard plans when the provider is unavailable.

    Raises:
        ValueError: If now is not timezone-aware.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        msg = "now must have a timezone"
        raise ValueError(msg)
    instant = now.astimezone(UTC)
    meetings = [
        event
        for event in events
        if not event.all_day
        and event.status.casefold() not in {"cancelled", "canceled"}
        and event.response != "declined"
        and event.start.astimezone(UTC) < event.end.astimezone(UTC)
    ]
    current = [event for event in meetings if event.start.astimezone(UTC) <= instant < event.end.astimezone(UTC)]
    upcoming = [event for event in meetings if event.start.astimezone(UTC) > instant]
    selected = [
        (max(current, key=lambda event: (event.start.astimezone(UTC), event.id), default=None), "end"),
        (min(upcoming, key=lambda event: (event.start.astimezone(UTC), event.id), default=None), "start"),
    ]
    reminders = []
    for event, kind in selected:
        if event is None:
            continue
        boundary = (event.end if kind == "end" else event.start).astimezone(UTC)
        identity = f"{event.id}\0{event.start.astimezone(UTC).isoformat()}\0{event.end.astimezone(UTC).isoformat()}"
        identity += f"\0{kind}"
        key = "calendar:" + hashlib.sha256(identity.encode()).hexdigest()
        reminders.append(Reminder(key=key, title=event.title, kind=kind, due=boundary - LEAD, expires=boundary))
    return reminders
