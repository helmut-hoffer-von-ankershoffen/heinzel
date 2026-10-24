"""What `hz org agenda` reads: the meeting you are in now and your next one, from the bound `calendar`.

It never fails for a missing or broken calendar: the web app's footer shows it all the time, so an unbound calendar
or an adapter error (an expired sign-in) comes back as `problem`, with the command that fixes it in `fix`.
"""

from datetime import datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, Field

from heinzel import Calendar, Event, HeinzelError, Window
from heinzel.kernel import Requirement
from heinzel.modules.org._discovery import local_timezone

CALENDAR = Requirement.parse("calendar:read")
LOOKAHEAD_DAYS = 2
"""The window ends at midnight this many days after today: the rest of today and all of tomorrow."""


class Bindings(Protocol):
    """What the agenda needs to know about bindings; `heinzel.kernel.CapabilityView` is one."""

    def serves(self, requirement: Requirement) -> tuple[str | None, str | None]:
        """The adapter that would serve `requirement` and why it can't, if it can't."""
        ...

    def bound(self, capability: str) -> str | None:
        """The adapter bound to `capability`, or None."""
        ...

    def command(self, capability: str) -> str:
        """The command line that serves `capability`."""
        ...

    def fix(self, capability: str) -> str:
        """What to do so that `capability` is served, ending with the command line that does it."""
        ...


class Agenda(BaseModel):
    """The meeting you are in now and your next one, or why they can't be read."""

    now: AwareDatetime = Field(description="When the agenda was read, in your time zone.")
    current: Event | None = Field(
        default=None,
        description="The event in progress now, not all-day and not cancelled; the one that started last if several "
        "overlap; none if you are free.",
    )
    next: Event | None = Field(
        default=None,
        description="The first event starting after now, not all-day and not cancelled, until the end of tomorrow; "
        "none if there is none.",
    )
    calendar: str | None = Field(
        default=None,
        description="The adapter bound to `calendar`, e.g. google, also when that binding can't be used; none when no "
        "calendar is connected.",
    )
    problem: str | None = Field(
        default=None,
        description="Why there is no agenda: no calendar is connected, its binding can't be used, or reading it "
        "failed (e.g. an expired sign-in); none when it was read.",
    )
    fix: str | None = Field(
        default=None,
        description="The command that connects a calendar when none is; none otherwise (`problem` says what to do).",
    )


def local_now(timezone: str | None) -> datetime:
    """Now in `timezone` (an IANA name), or in this machine's zone when it is unset.

    The machine's zone by name where /etc/localtime says it, so that tomorrow's times are right across a daylight
    saving change; a fixed offset only where it doesn't.
    """
    name = timezone or local_timezone()
    return datetime.now(ZoneInfo(name)) if name else datetime.now().astimezone()


def read_agenda(calendar: Calendar, bindings: Bindings, now: datetime) -> Agenda:
    """The event in progress at `now` and the next one, read from `calendar` if a calendar adapter serves it."""
    adapter, problem = bindings.serves(CALENDAR)
    if adapter is None or problem is not None:
        bound = bindings.bound(CALENDAR.capability)
        if bound is None:
            return Agenda(
                now=now,
                problem=f"No calendar connected ({CALENDAR}: {problem}).",
                fix=bindings.command(CALENDAR.capability),
            )
        # Bound, but to an adapter that can't serve (not installed, ambiguous, lacking read): a broken binding to
        # repair, not a calendar to connect.
        return Agenda(
            now=now,
            calendar=bound,
            problem=f"Your calendar binding {bound!r} can't be used: {problem}. {bindings.fix(CALENDAR.capability)}",
        )
    midnight = datetime.combine(now.date() + timedelta(days=LOOKAHEAD_DAYS), time(), tzinfo=now.tzinfo)
    try:
        events = calendar.events(Window(since=now, until=midnight))
    except HeinzelError as exc:
        return Agenda(now=now, calendar=adapter, problem=str(exc))
    except Exception as exc:  # noqa: BLE001 - isolation boundary: a broken adapter must not break the footer
        return Agenda(
            now=now, calendar=adapter, problem=f"Reading your {adapter} calendar failed: {type(exc).__name__}"
        )
    meetings = [e for e in events if not e.all_day and e.status != "cancelled"]
    running = [e for e in meetings if e.start <= now < e.end]
    upcoming = [e for e in meetings if e.start > now]
    return Agenda(
        now=now,
        current=max(running, key=lambda e: e.start, default=None),
        next=min(upcoming, key=lambda e: e.start, default=None),
        calendar=adapter,
    )
