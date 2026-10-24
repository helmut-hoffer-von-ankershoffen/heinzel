"""How the calendar looks in a terminal: one line per event, the countdown, free time, and what a write does.

Titles, names, places and links come from whoever wrote the invitation, so they go in as `Text`, never as markup:
`[link=...]` in a title stays text.
"""

from datetime import datetime

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from heinzel import Event
from heinzel.modules.calendar._models import CalendarAgenda, CalendarChange, Countdown, FreeTime, ShownEvent

ANSWERS = {"accepted": "yes", "declined": "no", "tentative": "maybe", "pending": "not answered"}


def _when(event: Event, now: datetime, *, days: bool) -> str:
    start, end = event.start.astimezone(now.tzinfo), event.end.astimezone(now.tzinfo)
    day = f"{start:%a %d %b} " if days else ""
    return f"{day}all day" if event.all_day else f"{day}{start:%H:%M}-{end:%H:%M}"


def render_agenda(agenda: CalendarAgenda) -> RenderableType:
    """A table of events: when, what, your answer, who, and the link to join."""
    if not agenda.events:
        return Text("Nothing in your calendar then.")
    days = (agenda.until - agenda.since).days > 1
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    for column in ("When", "What", "You", "Who", "Join"):
        table.add_column(column, overflow="fold")
    for event in agenda.events:
        names = [p.name or p.email or "" for p in event.attendees]
        who = ", ".join(names[:4]) + (f" +{len(names) - 4}" if len(names) > 4 else "")  # noqa: PLR2004 - a few names
        table.add_row(
            Text(_when(event, agenda.now, days=days)),
            Text(event.title) + Text(f" ({event.id})", style="dim"),
            Text(ANSWERS.get(event.response or "", "yours" if event.organized_by_user else "")),
            Text(who),
            Text(event.conference_url or ""),
        )
    return table


def render_event(shown: ShownEvent) -> RenderableType:
    """One event in full, in your time zone: when, where, who, your answer and its links."""
    event, now = shown.event, shown.now
    rows = [
        ("When", _when(event, now, days=True)),
        ("Where", event.location or ""),
        ("Organizer", (event.organizer.name or event.organizer.email or "") if event.organizer else ""),
        ("Guests", ", ".join(p.name or p.email or "" for p in event.attendees)),
        ("You", ANSWERS.get(event.response or "", "you organize it" if event.organized_by_user else "")),
        ("Join", event.conference_url or ""),
        ("Open", event.url or ""),
        ("Id", event.id),
    ]
    table = Table(title=Text(event.title), show_header=False, box=None)
    table.add_column("Field", style="dim")
    table.add_column("Value", overflow="fold")
    for name, value in rows:
        if value:
            table.add_row(name, Text(value))
    return table


def render_countdown(countdown: Countdown) -> RenderableType:
    """The countdown line, and the join link of the meeting it counts down to."""
    if countdown.problem is not None:
        fix = f" Run `{countdown.fix}`." if countdown.fix else ""
        return Text(f"{countdown.problem}{fix}")
    meeting = countdown.current or countdown.next
    join = f"\nJoin: {meeting.conference_url}" if meeting is not None and meeting.conference_url else ""
    return Text(countdown.line + join)


def render_free(free: FreeTime) -> RenderableType:
    """The free spans, one a line, and whose calendar was hidden."""
    if not free.slots:
        lines: list[RenderableType] = [Text(f"No free {free.minutes} minutes in your working hours then.")]
    else:
        zone = free.since.tzinfo
        lines = [Text(f"{s.start.astimezone(zone):%a %d %b %H:%M}-{s.end.astimezone(zone):%H:%M}") for s in free.slots]
    if free.unknown:
        lines.append(Text(f"Not known for {', '.join(free.unknown)}: their calendar isn't shown to you.", style="dim"))
    return Group(*lines)


def render_change(change: CalendarChange) -> RenderableType:
    """What it did, or what it would do and the command that does it."""
    if change.done or change.confirm is None:
        return Text(change.summary)
    return Group(Text(change.summary), Text(f"Nothing changed yet. To do it: {change.confirm}", style="bold"))
