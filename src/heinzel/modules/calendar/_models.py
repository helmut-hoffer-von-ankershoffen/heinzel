"""Settings of the calendar module and what its operations return."""

from typing import ClassVar

from pydantic import AwareDatetime, BaseModel, Field, field_validator

from heinzel import Event, FreeBusy, ModuleSettings, TimeSpan

MIN_REMIND = 5
"""The shortest reminder: the daemon ticks every few minutes, so a shorter one could fall between two ticks."""


class CalendarSettings(ModuleSettings):
    """The `[calendar]` table, or `HZ_CALENDAR__<FIELD>`: when hz reminds you of a meeting, how far `show` looks.

    Your zone and working hours come from your profile (`[org] timezone` and `working_hours`).
    """

    remind_minutes: int = Field(
        default=10,
        ge=0,
        le=120,
        description="Remind you this many minutes before a meeting starts (a notice with its join link), at least 5; "
        "0 turns the reminders off. The daemon looks at each tick; when it ticks less often than this, a reminder "
        "comes up to a tick early instead of falling between two ticks.",
    )
    look_days: int = Field(
        default=30, ge=1, le=366, description="Days before and after today that `show` and `search` look through."
    )

    ignored_in_project: ClassVar[frozenset[str]] = frozenset({"remind_minutes"})

    @field_validator("remind_minutes")
    @classmethod
    def _off_or_at_least_a_tick(cls, minutes: int) -> int:
        if 0 < minutes < MIN_REMIND:
            msg = f"remind_minutes is 0 (off) or at least {MIN_REMIND}: the daemon looks every few minutes"
            raise ValueError(msg)
        return minutes


class CalendarAgenda(BaseModel):
    """Your events in a window, by start."""

    since: AwareDatetime = Field(description="Start of the window, in your time zone.")
    until: AwareDatetime = Field(description="End of the window, exclusive.")
    now: AwareDatetime = Field(description="When it was read.")
    events: list[Event] = Field(
        default_factory=list[Event], description="Events overlapping the window, cancelled ones left out, by start."
    )


class Countdown(BaseModel):
    """The meeting you are in and your next one, with a line that counts down to either: what the footer shows."""

    now: AwareDatetime = Field(description="When it was read, in your time zone.")
    current: Event | None = Field(
        default=None, description="The meeting in progress, not all-day; the one that started last if several do."
    )
    next: Event | None = Field(
        default=None, description="The first meeting starting after now, until the end of tomorrow; none if none."
    )
    line: str = Field(
        description="One line for a status bar: `Standup · ends in 12 min`, `Next: 1:1 in 25 min`, or why there is "
        "none."
    )
    problem: str | None = Field(
        default=None, description="Why there is no countdown (no calendar, a failed read); none when it was read."
    )
    fix: str | None = Field(default=None, description="The command that connects a calendar when none is.")


class FreeTime(BaseModel):
    """When you, and the people asked about, are free within your working hours on a day."""

    since: AwareDatetime = Field(description="Start of the working hours looked at (or now, if later).")
    until: AwareDatetime = Field(description="End of the working hours looked at.")
    minutes: int = Field(description="The shortest free span listed, in minutes.")
    slots: list[TimeSpan] = Field(
        default_factory=list[TimeSpan], description="Free spans everybody asked about shares, by start."
    )
    calendars: list[FreeBusy] = Field(
        default_factory=list[FreeBusy], description="Each calendar's busy spans, yours first."
    )
    unknown: list[str] = Field(
        default_factory=list, description="Addresses whose calendar wasn't shown: their free time is unknown."
    )


class CalendarChange(BaseModel):
    """What a calendar write would do, or did: shown first, run only with `confirm`."""

    done: bool = Field(description="Whether it was carried out; false: it only says what `confirm` would do.")
    summary: str = Field(description="What it does, in a sentence.")
    confirm: str | None = Field(
        default=None, description="The command that carries it out, when it wasn't yet; none once done."
    )
    event: Event | None = Field(
        default=None, description="The event as it is now (done) or as it is before the change (shown)."
    )


class ShownEvent(BaseModel):
    """One event, and when it was read, so it shows in your time zone."""

    now: AwareDatetime = Field(description="When it was read, in your time zone.")
    event: Event = Field(description="The event.")


class Reminded(BaseModel):
    """What one look for meetings starting soon recorded."""

    reminded: int = Field(description="How many meeting notices were recorded; notify drops a repeat.")
    skipped: str | None = Field(default=None, description="Why nothing was looked at: reminders off, or no calendar.")
