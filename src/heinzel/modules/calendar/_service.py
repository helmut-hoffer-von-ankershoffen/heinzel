"""What the calendar operations do, over the bound `calendar` capability: read, count down, find free time, write.

Writes show what they would do and change nothing until they are run again with `confirm` (ADR 0059); in a chat,
the `ActionGate` holds them until the person confirms. Each one reads the event first, so what is shown is the
event as the calendar has it now.
"""

import contextlib
import json
import math
import shlex
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path

from heinzel import (
    Calendar,
    Event,
    EventChange,
    EventDraft,
    HeinzelError,
    TimeSpan,
    Window,
    free_busy_people,
)
from heinzel.modules.calendar._models import CalendarAgenda, CalendarChange, Countdown, FreeTime
from heinzel.modules.calendar._time import Profile, parse_when

LOOKAHEAD_DAYS = 2
"""The countdown looks to the end of tomorrow."""
SOON = 60
"""Minutes within which the countdown counts minutes; later meetings show their clock time."""


def meetings(events: Sequence[Event]) -> list[Event]:
    """The events that are meetings: not all-day, not cancelled, not declined."""
    return [e for e in events if not e.all_day and e.status != "cancelled" and e.response != "declined"]


def agenda(calendar: Calendar, profile: Profile, since: datetime, until: datetime) -> CalendarAgenda:
    """Your events in `[since, until)`, cancelled ones left out.

    Raises:
        HeinzelError: If the calendar can't be read.
    """
    events = calendar.events(Window(since=since, until=until))
    return CalendarAgenda(
        since=since,
        until=until,
        now=profile.now(),
        events=[e for e in events if e.status != "cancelled"],
    )


def day(calendar: Calendar, profile: Profile, on: date | None = None) -> CalendarAgenda:
    """Your events on `on` (today when not given), in your time zone.

    Raises:
        HeinzelError: If the calendar can't be read.
    """
    since, until = profile.day(on or profile.now().date())
    return agenda(calendar, profile, since, until)


def week(calendar: Calendar, profile: Profile) -> CalendarAgenda:
    """Your events from today's midnight for seven days.

    Raises:
        HeinzelError: If the calendar can't be read.
    """
    since, _ = profile.day(profile.now().date())
    return agenda(calendar, profile, since, profile.day(since.date() + timedelta(days=6))[1])


def _minutes(delta: timedelta) -> int:
    return max(1, math.ceil(delta.total_seconds() / 60))


def _clock(moment: datetime, now: datetime) -> str:
    when = moment.astimezone(now.tzinfo)
    return f"{when:%H:%M}" if when.date() == now.date() else f"{when:%a %H:%M}"


def countdown_line(current: Event | None, upcoming: Event | None, now: datetime) -> str:
    """`Standup · ends in 12 min`, `Next: 1:1 in 25 min`, `Next: Review at 15:00` or `No more meetings today`."""
    if current is not None:
        left = current.end - now
        ends = (
            f"ends in {_minutes(left)} min" if left <= timedelta(minutes=SOON) else f"until {_clock(current.end, now)}"
        )
        then = ""
        if upcoming is not None and upcoming.start - now <= timedelta(minutes=SOON):
            then = f" · next: {upcoming.title} in {_minutes(upcoming.start - now)} min"
        return f"{current.title} · {ends}{then}"
    if upcoming is None:
        return "No meetings until the end of tomorrow"
    wait = upcoming.start - now
    if wait <= timedelta(minutes=SOON):
        return f"Next: {upcoming.title} in {_minutes(wait)} min"
    return f"Next: {upcoming.title} at {_clock(upcoming.start, now)}"


def countdown(events: Sequence[Event], now: datetime) -> Countdown:
    """The meeting you are in and the next one among `events`, as the footer shows them."""
    found = meetings(events)
    running = [e for e in found if e.start <= now < e.end]
    current = max(running, key=lambda e: e.start, default=None)
    upcoming = min((e for e in found if e.start > now), key=lambda e: e.start, default=None)
    return Countdown(now=now, current=current, next=upcoming, line=countdown_line(current, upcoming, now))


def read_countdown(calendar: Calendar, profile: Profile) -> Countdown:
    """The countdown from now to the end of tomorrow; a calendar that fails comes back as `problem`, never raises."""
    now = profile.now()
    _, until = profile.day(now.date() + timedelta(days=LOOKAHEAD_DAYS - 1))
    try:
        events = calendar.events(Window(since=now - timedelta(hours=12), until=until))
    except HeinzelError as exc:
        return Countdown(now=now, line="Calendar unavailable", problem=str(exc))
    except Exception as exc:  # noqa: BLE001 - isolation boundary: a broken adapter must not break the footer
        return Countdown(
            now=now, line="Calendar unavailable", problem=f"Reading your calendar failed: {type(exc).__name__}"
        )
    return countdown(events, now)


def search(calendar: Calendar, profile: Profile, query: str, days: int) -> CalendarAgenda:
    """Your events within `days` before and after today whose title, location or people's names hold `query`.

    Raises:
        HeinzelError: If the query is empty or the calendar can't be read.
    """
    words = query.casefold().split()
    if not words:
        msg = 'Search for some words, e.g. `hz calendar search "planning"`.'
        raise HeinzelError(msg)
    today = profile.now().date()
    found = agenda(
        calendar, profile, profile.day(today - timedelta(days=days))[0], profile.day(today + timedelta(days=days))[1]
    )

    def text(event: Event) -> str:
        people = [p.name or "" for p in (*event.attendees, *([event.organizer] if event.organizer else []))]
        return " ".join([event.title, event.location or "", *people]).casefold()

    found.events = [e for e in found.events if all(word in text(e) for word in words)]
    return found


def find(calendar: Calendar, profile: Profile, event: str, days: int) -> Event:
    """The event with id `event` within `days` before and after today.

    Raises:
        HeinzelError: If it isn't there, or the calendar can't be read.
    """
    today = profile.now().date()
    since, until = profile.day(today - timedelta(days=days))[0], profile.day(today + timedelta(days=days))[1]
    for found in calendar.events(Window(since=since, until=until)):
        if found.id == event:
            return found
    msg = f"No event {event!r} within {days} days of today; `hz calendar week` lists event ids."
    raise HeinzelError(msg)


def free(calendar: Calendar, profile: Profile, on: date | None, people: str, minutes: int) -> FreeTime:
    """When you and `people` are free for at least `minutes` within your working hours on `on` (today).

    Raises:
        HeinzelError: If an address is malformed or the calendar can't be read.
    """
    when = on or profile.now().date()
    start, end = profile.workday(when)
    start = max(start, profile.now()) if when == profile.now().date() else start
    try:
        asked = free_busy_people(people)
    except ValueError as exc:
        msg = f"Name people by address, comma-separated, e.g. --people ada@example.com,bo@example.com: {exc}."
        raise HeinzelError(msg) from None
    if start >= end:
        return FreeTime(since=end, until=end, minutes=minutes)
    calendars = calendar.free_busy(",".join(asked), Window(since=start, until=end))
    busy = sorted((span for c in calendars if c.known for span in c.busy), key=lambda span: span.start)
    slots: list[TimeSpan] = []
    cursor = start
    for span in busy:
        if span.start - cursor >= timedelta(minutes=minutes):
            slots.append(TimeSpan(start=cursor, end=span.start))
        cursor = max(cursor, span.end)
    if end - cursor >= timedelta(minutes=minutes):
        slots.append(TimeSpan(start=cursor, end=end))
    unknown = [c.who.email for c in calendars[1:] if not c.known and c.who.email]
    return FreeTime(since=start, until=end, minutes=minutes, slots=slots, calendars=calendars, unknown=unknown)


def _command(words: Sequence[str]) -> str:
    return " ".join(shlex.quote(w) for w in ["hz", "calendar", *words, "--confirm"])


def _span(event: Event | EventDraft, profile: Profile) -> str:
    start, end = event.start.astimezone(profile.zone), event.end.astimezone(profile.zone)
    if event.all_day:
        return f"{start:%a %d %b}" + (
            "" if end - start <= timedelta(days=1) else f" to {end - timedelta(days=1):%a %d %b}"
        )
    return (
        f"{start:%a %d %b %H:%M}-{end:%H:%M}"
        if start.date() == end.date()
        else f"{start:%a %d %b %H:%M} to {end:%a %d %b %H:%M}"
    )


def create(
    calendar: Calendar,
    profile: Profile,
    draft: EventDraft,
    command: Sequence[str],
    *,
    confirm: bool,
) -> CalendarChange:
    """Create `draft` with `confirm`; without it, say what would be created.

    Raises:
        HeinzelError: If the calendar refuses or can't be written.
    """
    who = f", inviting {', '.join(draft.attendees)}" if draft.attendees else ""
    told = "" if draft.notify or not draft.attendees else " without telling them"
    video = ", with a video call" if draft.conference else ""
    what = f"{draft.title!r} on {_span(draft, profile)}{who}{told}{video}"
    if not confirm:
        return CalendarChange(done=False, summary=f"Would create {what}.", confirm=_command(command))
    return CalendarChange(done=True, summary=f"Created {what}.", event=calendar.create_event(draft))


def move(  # noqa: PLR0913 - what a move is
    calendar: Calendar,
    profile: Profile,
    event: Event,
    change: EventChange,
    command: Sequence[str],
    *,
    confirm: bool,
) -> CalendarChange:
    """Change `event` as `change` says with `confirm`; without it, say what would change.

    Raises:
        HeinzelError: If the event isn't yours to change, or the calendar refuses.
    """
    if not event.organized_by_user:
        msg = f"You don't organize {event.title!r}: ask its organizer to move it, or answer it instead."
        raise HeinzelError(msg)
    start = change.start or event.start
    end = change.end or (start + (event.end - event.start))
    moved = event.model_copy(update={"start": start, "end": end, "title": change.title or event.title})
    told = "" if change.notify else ", without telling the guests"
    what = f"{event.title!r} from {_span(event, profile)} to {_span(moved, profile)}{told}"
    if not confirm:
        return CalendarChange(done=False, summary=f"Would move {what}.", confirm=_command(command), event=event)
    return CalendarChange(done=True, summary=f"Moved {what}.", event=calendar.update_event(event.id, change))


def cancel(  # noqa: PLR0913 - what a cancellation is
    calendar: Calendar,
    profile: Profile,
    event: Event,
    command: Sequence[str],
    *,
    notify: bool,
    confirm: bool,
) -> CalendarChange:
    """Cancel `event` for everyone with `confirm`; without it, say what would be cancelled.

    Raises:
        HeinzelError: If the event isn't yours to cancel (decline it instead), or the calendar refuses.
    """
    if not event.organized_by_user:
        msg = f"You don't organize {event.title!r}: decline it with `hz calendar respond {event.id} declined`."
        raise HeinzelError(msg)
    guests = [
        p.email or p.name or ""
        for p in event.attendees
        if p.email != (event.organizer.email if event.organizer else None)
    ]
    told = f", telling {len(guests)} guest(s)" if notify and guests else ""
    what = f"{event.title!r} on {_span(event, profile)} for everyone{told}"
    if not confirm:
        return CalendarChange(done=False, summary=f"Would cancel {what}.", confirm=_command(command), event=event)
    calendar.cancel_event(event.id, notify)
    return CalendarChange(done=True, summary=f"Cancelled {what}.")


def respond(  # noqa: PLR0913 - what an answer is
    calendar: Calendar,
    profile: Profile,
    event: Event,
    response: str,
    command: Sequence[str],
    *,
    confirm: bool,
) -> CalendarChange:
    """Answer the invitation to `event` with `response` with `confirm`; without it, say what would be answered.

    Raises:
        HeinzelError: If you aren't invited, or the calendar refuses.
    """
    if event.response is None:
        msg = f"You aren't among the guests of {event.title!r}, so there is no invitation to answer."
        raise HeinzelError(msg)
    organizer = event.organizer.name or event.organizer.email if event.organizer else None
    told = f", telling {organizer}" if organizer else ""
    what = f"{event.title!r} on {_span(event, profile)} with {response}{told}"
    if not confirm:
        return CalendarChange(done=False, summary=f"Would answer {what}.", confirm=_command(command), event=event)
    return CalendarChange(done=True, summary=f"Answered {what}.", event=calendar.respond(event.id, response))


def when(text: str | None, profile: Profile) -> datetime | None:
    """`text` as a time in your zone, or None for none.

    Raises:
        HeinzelError: If it isn't a time hz reads.
    """
    return None if text is None else parse_when(text, profile.zone, profile.now())


def on(text: str | None, profile: Profile) -> date | None:
    """The day `text` names: `today`, `tomorrow` or an ISO date; None for none.

    Raises:
        HeinzelError: If it names no day.
    """
    if text is None:
        return None
    word = text.strip().lower()
    today = profile.now().date()
    if word in {"today", "tomorrow"}:
        return today + timedelta(days=1 if word == "tomorrow" else 0)
    try:
        return date.fromisoformat(word)
    except ValueError:
        msg = f"{text!r} names no day; give `today`, `tomorrow` or a date such as 2026-10-26."
        raise HeinzelError(msg) from None


MAX_LOOK_AHEAD = timedelta(hours=1)
"""The longest look ahead for reminders: the daemon's longest tick interval."""


def look_ahead(state: Path, now: datetime, minutes: int) -> timedelta:
    """How far the reminder looks: `minutes`, or the time since its last look if longer (at most an hour).

    So no meeting falls between two ticks of a daemon that ticks less often than `minutes`; the look is recorded in
    `state`.
    """
    ahead = timedelta(minutes=minutes)
    try:
        last = datetime.fromisoformat(json.loads(state.read_text(encoding="utf-8"))["looked"])
    except (OSError, ValueError, KeyError, TypeError):
        last = None  # the first look, or a damaged record: the setting alone
    if last is not None and last.tzinfo is not None and last < now:
        ahead = max(ahead, min(now - last, MAX_LOOK_AHEAD))
    with contextlib.suppress(OSError):  # without the record, the next look uses the setting alone
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"looked": now.isoformat()}), encoding="utf-8")
    return ahead
