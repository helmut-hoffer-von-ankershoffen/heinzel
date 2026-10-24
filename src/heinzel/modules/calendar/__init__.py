"""The calendar module: your calendar in the CLI, for agents over MCP and in the web app; reminders before meetings.

It reads and writes through the bound `calendar` capability (ADR 0028, ADR 0059), whatever serves it (Google Calendar
through `heinzel-google`, or another adapter). Reads are tools for agents too; they show your meetings and the names
of the people in them, so they go to the CLI, MCP and the web head (behind its session token), never to the
unauthenticated REST head. Writes (`new`, `move`, `cancel`, `respond`) say what they would do and change nothing
until they are run again with `--confirm`; in a chat, the `ActionGate` holds them for the person to confirm. Your
time zone and working hours come from your profile (`[org]`).
"""

import math
import shlex
from datetime import timedelta
from typing import Annotated, Literal
from urllib.parse import quote

from pydantic import Field, ValidationError

from heinzel import (
    Calendar,
    Config,
    Event,
    EventChange,
    EventDraft,
    HeinzelError,
    Module,
    NoticeKind,
    Notifications,
    Settings,
    Window,
)
from heinzel.kernel import CapabilityView, Requirement
from heinzel.modules.calendar import _service as service
from heinzel.modules.calendar._models import (
    CalendarAgenda,
    CalendarChange,
    CalendarSettings,
    Countdown,
    FreeTime,
    Reminded,
    ShownEvent,
)
from heinzel.modules.calendar._render import (
    render_agenda,
    render_change,
    render_countdown,
    render_event,
    render_free,
)
from heinzel.modules.calendar._time import Profile, profile

module = Module(
    "calendar", "Your calendar: today, the week, free time, and events created or answered once you confirm."
)
module.settings(CalendarSettings)

READS = {"cli", "mcp", "web"}
"""Your meetings and who is in them: never the unauthenticated REST head."""
WRITES = {"cli", "mcp-stdio", "web"}
"""Changes to your calendar and other people's invitations: not over MCP's unauthenticated HTTP transport either."""
READ = ["calendar:read"]
WRITE = ["calendar:read,write"]
CALENDAR = Requirement.parse("calendar:read")
WRITTEN = Requirement.parse("calendar:read,write")

EventId = Annotated[str, Field(description="The event's id, as `hz calendar` and `hz calendar week` list it.")]
Confirm = Annotated[
    bool,
    Field(
        description="Carry it out. Without it, nothing changes: the answer says what would happen. An agent passes it "
        "only after the person approved that summary."
    ),
]
Notify = Annotated[bool, Field(description="Tell the guests by mail.")]
Day = Annotated[
    str | None, Field(description="The day: `tomorrow` or a date such as 2026-10-26; today when not given.")
]
Days = Annotated[int | None, Field(ge=1, le=366, description="Days before and after today to look through.")]


def _profile(config: Config) -> Profile:
    return profile(config.section("org"))


@module.operation(name="calendar", render=render_agenda, heads=READS, requires=READ, optional=True, audience="everyday")
def today(calendar: Calendar, config: Config, on: Day = None) -> CalendarAgenda:
    """Show today's events (or another day's), with your answer, the guests and the link to join."""
    you = _profile(config)
    return service.day(calendar, you, service.on(on, you))


@module.operation(render=render_agenda, heads=READS, requires=READ, optional=True, audience="everyday")
def week(calendar: Calendar, config: Config) -> CalendarAgenda:
    """Show your events of the next seven days, from today."""
    return service.week(calendar, _profile(config))


@module.operation(render=render_event, heads=READS, requires=READ, optional=True)
def show(
    calendar: Calendar, config: Config, settings: CalendarSettings, event: EventId, days: Days = None
) -> ShownEvent:
    """Show one event in full: when, where, who, your answer and its links."""
    you = _profile(config)
    return ShownEvent(now=you.now(), event=service.find(calendar, you, event, days or settings.look_days))


@module.operation(render=render_agenda, heads=READS, requires=READ, optional=True)
def search(
    calendar: Calendar,
    config: Config,
    settings: CalendarSettings,
    query: Annotated[str, Field(description="Words the title, location or a person's name holds, e.g. `planning`.")],
    days: Days = None,
) -> CalendarAgenda:
    """Find events around today whose title, location or people hold every word of `query`."""
    return service.search(calendar, _profile(config), query, days or settings.look_days)


@module.operation(render=render_free, heads=READS, requires=READ, optional=True, audience="everyday")
def free(
    calendar: Calendar,
    config: Config,
    on: Day = None,
    people: Annotated[
        str, Field(description="Others to find time with, by address, comma-separated: ada@example.com,bo@example.com.")
    ] = "",
    minutes: Annotated[int, Field(ge=5, le=480, description="The shortest free time worth listing.")] = 30,
) -> FreeTime:
    """Show when you (and the people you name) are free within your working hours on a day."""
    you = _profile(config)
    return service.free(calendar, you, service.on(on, you), people, minutes)


@module.operation(render=render_countdown, heads=READS, requires=READ, optional=True, audience="everyday")
def now(calendar: Calendar, config: Config, view: CapabilityView) -> Countdown:
    """Show the meeting you are in and your next one, counting down, in one line for a status bar."""
    adapter, problem = view.serves(CALENDAR)
    if adapter is None or problem is not None:
        at = _profile(config).now()
        if view.bound(CALENDAR.capability) is None:
            line = "No calendar connected"
            return Countdown(now=at, line=line, problem=f"{line} ({problem}).", fix=view.command("calendar"))
        return Countdown(now=at, line="Calendar unavailable", problem=f"{problem}. {view.fix('calendar')}")
    return service.read_countdown(calendar, _profile(config))


@module.operation(
    read_only=False, render=render_change, heads=WRITES, requires=WRITE, optional=True, audience="everyday"
)
def new(  # noqa: PLR0913, PLR0917 - what an event is
    calendar: Calendar,
    config: Config,
    view: CapabilityView,
    title: Annotated[str, Field(description="What it is called, one line.")],
    start: Annotated[str, Field(description="When it starts: `10:30` (today), `tomorrow 9:00` or `2026-10-26 14:00`.")],
    minutes: Annotated[int, Field(ge=5, le=24 * 60, description="How long it lasts.")] = 30,
    people: Annotated[
        str, Field(description="Whom to invite, by address, comma-separated: ada@example.com,bo@example.com.")
    ] = "",
    location: Annotated[str | None, Field(description="Where it takes place.")] = None,
    description: Annotated[str | None, Field(description="Notes for the guests.")] = None,
    video: Annotated[bool, Field(description="Add a video call from your calendar's conferencing.")] = False,
    notify: Notify = True,
    confirm: Confirm = False,
) -> CalendarChange:
    """Create an event in your calendar and invite its guests; shown first, created with `--confirm`.

    Raises:
        HeinzelError: If the start is no time hz reads, or the event can't be made (a guest's address, its title).
    """
    you = _profile(config)
    begin = service.when(start, you)
    if begin is None:  # pragma: no cover - start is required
        msg = "A new event needs a start."
        raise HeinzelError(msg)
    try:
        draft = EventDraft(
            title=title,
            start=begin,
            end=begin + timedelta(minutes=minutes),
            attendees=people.split(","),
            location=location,
            description=description,
            conference=video,
            notify=notify,
        )
    except ValidationError as exc:
        msg = f"The event can't be made: {_first(exc)}"
        raise HeinzelError(msg) from None
    _writable(view)
    # The confirm command names the time it showed, not `10:30`: confirmed tomorrow, that would be another day.
    words = ["new", title, begin.isoformat(timespec="minutes"), "--minutes", str(minutes)]
    words += (["--people", people] if people else []) + (["--location", location] if location else [])
    words += (["--description", description] if description else []) + (["--video"] if video else [])
    words += [] if notify else ["--no-notify"]
    return service.create(calendar, you, draft, words, confirm=confirm)


@module.operation(read_only=False, render=render_change, heads=WRITES, requires=WRITE, optional=True)
def move(  # noqa: PLR0913, PLR0917 - what a move is
    calendar: Calendar,
    config: Config,
    view: CapabilityView,
    settings: CalendarSettings,
    event: EventId,
    start: Annotated[str, Field(description="The new start: `10:30` (today), `tomorrow 9:00`, `2026-10-26 14:00`.")],
    minutes: Annotated[int | None, Field(ge=5, le=24 * 60, description="A new length; it keeps its own.")] = None,
    notify: Notify = True,
    confirm: Confirm = False,
) -> CalendarChange:
    """Move an event you organize to another time; shown first, moved with `--confirm`.

    Raises:
        HeinzelError: If the event isn't found or isn't yours, or the new time is no time hz reads.
    """
    you = _profile(config)
    found = service.find(calendar, you, event, settings.look_days)
    begin = service.when(start, you)
    end = begin + timedelta(minutes=minutes) if begin is not None and minutes is not None else None
    change = EventChange(start=begin, end=end, notify=notify)  # a start, and an end after it: always valid
    _writable(view)
    words = ["move", event, begin.isoformat(timespec="minutes") if begin else start]
    words += ["--minutes", str(minutes)] if minutes else []
    words += [] if notify else ["--no-notify"]
    return service.move(calendar, you, found, change, words, confirm=confirm)


@module.operation(read_only=False, render=render_change, heads=WRITES, requires=WRITE, optional=True)
def cancel(  # noqa: PLR0913, PLR0917 - what a cancellation is
    calendar: Calendar,
    config: Config,
    view: CapabilityView,
    settings: CalendarSettings,
    event: EventId,
    notify: Notify = True,
    confirm: Confirm = False,
) -> CalendarChange:
    """Cancel an event you organize, for everyone; shown first, cancelled with `--confirm`."""
    you = _profile(config)
    _writable(view)
    found = service.find(calendar, you, event, settings.look_days)
    words = ["cancel", event, *([] if notify else ["--no-notify"])]
    return service.cancel(calendar, you, found, words, notify=notify, confirm=confirm)


@module.operation(
    read_only=False, render=render_change, heads=WRITES, requires=WRITE, optional=True, audience="everyday"
)
def respond(  # noqa: PLR0913, PLR0917 - what an answer is
    calendar: Calendar,
    config: Config,
    view: CapabilityView,
    settings: CalendarSettings,
    event: EventId,
    answer: Annotated[
        Literal["accepted", "declined", "tentative"], Field(description="Your answer: accepted, declined or tentative.")
    ],
    confirm: Confirm = False,
) -> CalendarChange:
    """Answer an invitation: accepted, declined or tentative; shown first, answered with `--confirm`."""
    _writable(view)
    you = _profile(config)
    found = service.find(calendar, you, event, settings.look_days)
    return service.respond(calendar, you, found, answer, ["respond", event, answer], confirm=confirm)


# The daemon's notify tick runs it (`system.notify`), not a job: a receipt every five minutes would say nothing a
# notice doesn't, and would pile up for everyone. It only records local notices; it is still no read, so a chat's
# agent can't call it.
@module.operation(read_only=False, heads={"cli"}, requires=READ, optional=True)
def remind(  # noqa: PLR0913, PLR0917 - what it is injected with
    calendar: Calendar,
    config: Config,
    settings: CalendarSettings,
    kernel: Settings,
    notifications: Notifications,
    view: CapabilityView,
) -> Reminded:
    """Record a notice with the join link for each meeting starting within `[calendar] remind_minutes`."""
    adapter, problem = view.serves(CALENDAR)
    if settings.remind_minutes == 0 or adapter is None or problem is not None:
        return Reminded(reminded=0, skipped="reminders are off" if settings.remind_minutes == 0 else "no calendar")
    you = _profile(config)
    at = you.now()
    ahead = service.look_ahead(kernel.state_dir / "calendar" / "remind.json", at, settings.remind_minutes)
    soon = [e for e in service.meetings(calendar.events(Window(since=at, until=at + ahead))) if e.start > at]
    for event in soon:
        left = max(1, math.ceil((event.start - at).total_seconds() / 60))  # as the countdown counts
        join = event.conference_url if (event.conference_url or "").startswith("https://") else None
        notifications.emit(
            NoticeKind.MEETING,
            f"In {left} min: {event.title}",
            f"Join: {join}" if join else (event.location or ""),
            route=f"/calendar?event={quote(event.id, safe='')}",
            command=shlex.join(["hz", "calendar", "show", event.id]),
            key=f"meeting:{event.id}:{event.start.isoformat()}",
            urgent=True,  # about a moment: held until working hours start, it would come after the meeting
        )
    return Reminded(reminded=len(soon))


def _writable(view: CapabilityView) -> None:
    """Refuse a write before showing it when nothing serves `calendar:write`, saying how to connect one.

    Raises:
        HeinzelError: If no adapter serves writing the calendar.
    """
    adapter, problem = view.serves(WRITTEN)
    if adapter is None or problem is not None:
        msg = f"Nothing writes your calendar ({WRITTEN}: {problem}). {view.fix('calendar')}"
        raise HeinzelError(msg)


def _first(error: ValidationError) -> str:
    """The first problem a model's validation names, without pydantic's boilerplate."""
    return str(error.errors()[0]["msg"]).removeprefix("Value error, ")


__all__ = [
    "CalendarAgenda",
    "CalendarChange",
    "CalendarSettings",
    "Countdown",
    "Event",
    "FreeTime",
    "Reminded",
    "ShownEvent",
    "module",
]
