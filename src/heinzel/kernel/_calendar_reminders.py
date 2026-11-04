"""Pure planning for calendar reminders; no provider access or background work occurs here."""

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from heinzel.kernel._notifications import NoticeKind, Notifications
from heinzel.kernel._protocols import Event
from heinzel.kernel._text import clean_text


class Agenda(Protocol):
    """The generic event selection and provider status used for reminder planning."""

    current: Event | None
    next: Event | None
    calendar: str | None
    problem: str | None


LEAD = timedelta(minutes=5)
MAX_POLL = timedelta(seconds=30)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Reminder:
    """A boundary and its exclusive expiry, identified independently of event title."""

    key: str
    title: str
    kind: str
    due: datetime
    expires: datetime


def plan(events: list[Event], now: datetime, *, provider: str = "") -> list[Reminder]:
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
        identity = f"{provider}\0{event.id}\0{event.start.astimezone(UTC).isoformat()}"
        if kind == "end":
            identity += f"\0{event.end.astimezone(UTC).isoformat()}"
        identity += f"\0{kind}"
        key = "calendar:" + hashlib.sha256(identity.encode()).hexdigest()
        reminders.append(Reminder(key=key, title=event.title, kind=kind, due=boundary - LEAD, expires=boundary))
    return reminders


def agenda_plan(agenda: "Agenda", now: datetime) -> list[Reminder]:
    """Plan only a successfully read, bound calendar; an error never becomes a free calendar."""
    if agenda.problem is not None or agenda.calendar is None:
        return []
    events = [event for event in (agenda.current, agenda.next) if event is not None]
    return plan(events, now, provider=agenda.calendar)


def reconcile(agenda: "Agenda", notifications: "Notifications", now: datetime) -> list[Reminder]:
    """Withdraw obsolete pending reminders and atomically record due ones.

    Call only after the user has enabled calendar reminders. An unavailable provider suppresses pending reminders.
    """
    reminders = agenda_plan(agenda, now)
    active = {reminder.key for reminder in reminders}
    for notice in notifications.recent(200):
        if notice.kind is NoticeKind.CALENDAR and notice.delivered_by is None and notice.key not in active:
            notifications.withdraw(notice.key)
    instant = now.astimezone(UTC)
    for reminder in reminders:
        if reminder.due <= instant < reminder.expires:
            notifications.emit_once(
                NoticeKind.CALENDAR,
                "Meeting starts soon" if reminder.kind == "start" else "Meeting ends soon",
                " ".join(clean_text(reminder.title).split())[:120] or "(no title)",
                route="/",
                command="hz org agenda",
                key=reminder.key,
                expires=reminder.expires,
            )
    return reminders


def withdraw_pending(notifications: Notifications) -> None:
    """Suppress outstanding calendar notices when consent or provider state is unavailable."""
    for notice in notifications.recent(200):
        if notice.kind is NoticeKind.CALENDAR and notice.delivered_by is None:
            notifications.withdraw(notice.key)


def cycle(read: Callable[[], Agenda], notifications: Notifications, now: datetime, *, enabled: bool) -> timedelta:
    """Refresh, reconcile and attempt delivery; return the delay until the next refresh or deadline."""
    if not enabled:
        withdraw_pending(notifications)
        return MAX_POLL
    try:
        agenda = read()
    except Exception:  # noqa: BLE001 - a failed provider cannot keep stale notices deliverable
        logger.warning("calendar reminder source unavailable")
        withdraw_pending(notifications)
        return MAX_POLL
    reminders = reconcile(agenda, notifications, now)
    if agenda.problem is None:
        notifications.post_native()
    upcoming = [reminder.due - now.astimezone(UTC) for reminder in reminders if reminder.due > now.astimezone(UTC)]
    return min([MAX_POLL, *upcoming])
