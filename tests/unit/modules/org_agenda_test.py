"""`hz org agenda`: the meeting now and the next one from a fake calendar, and how it degrades without one."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from heinzel import Event, HeinzelError, Window
from heinzel.kernel import Requirement
from heinzel.modules.org._agenda import Agenda, local_now, read_agenda
from heinzel.modules.org._render import render_agenda

pytestmark = pytest.mark.unit

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 26, 10, 15, tzinfo=BERLIN)


def event(title: str, start: str, minutes: int = 30, **fields: object) -> Event:
    begins = datetime.fromisoformat(start).replace(tzinfo=BERLIN)
    return Event.model_validate(
        {"id": title, "title": title, "start": begins, "end": begins + timedelta(minutes=minutes), **fields}
    )


class FakeCalendar:
    """A calendar for tests: returns fixed events, or raises."""

    def __init__(self, *events: Event, error: Exception | None = None) -> None:
        self.found = list(events)
        self.error = error
        self.windows: list[Window] = []

    def events(self, window: Window) -> list[Event]:
        self.windows.append(window)
        if self.error is not None:
            raise self.error
        return self.found


class FakeBindings:
    """Bindings for tests: a calendar adapter is bound, or not."""

    def __init__(self, adapter: str | None = "google", problem: str | None = None) -> None:
        self.adapter = adapter
        self.problem = problem

    def serves(self, requirement: Requirement) -> tuple[str | None, str | None]:
        assert str(requirement) == "calendar:read"
        return self.adapter, self.problem

    def command(self, capability: str) -> str:
        return f"hz system config set capabilities.{capability} google"


def text(agenda: Agenda) -> str:
    rendered = render_agenda(agenda)
    renderables = getattr(rendered, "renderables", [rendered])
    return "\n".join(str(part) for part in renderables)


def test_no_calendar_bound_says_so_with_the_command_and_reads_nothing():
    calendar = FakeCalendar()
    agenda = read_agenda(calendar, FakeBindings(None, "no adapter bound"), NOW)
    assert agenda.calendar is None
    assert agenda.current is None
    assert agenda.next is None
    assert agenda.problem == "No calendar connected (calendar:read: no adapter bound)."
    assert agenda.fix == "hz system config set capabilities.calendar google"
    assert calendar.windows == []
    assert text(agenda).endswith("Run `hz system config set capabilities.calendar google`.")


def test_the_meeting_in_progress_is_current_and_the_one_started_last_wins():
    standup = event("Standup", "2026-10-26T10:00", 30, conference_url="https://meet.example.com/abc")
    workshop = event("Workshop", "2026-10-26T09:00", 120)
    review = event("Review", "2026-10-26T11:00")
    agenda = read_agenda(FakeCalendar(workshop, standup, review), FakeBindings(), NOW)
    assert agenda.calendar == "google"
    assert agenda.problem is None
    assert agenda.current == standup
    assert agenda.next == review
    assert text(agenda) == "Now:  Standup, until 10:30 (https://meet.example.com/abc)\nNext: Review, at 11:00"


def test_with_nothing_in_progress_only_the_next_one_is_set():
    tomorrow = event("Planning", "2026-10-27T09:00")
    later = event("Retro", "2026-10-27T15:00")
    ended = event("Breakfast", "2026-10-26T09:45")  # ends at 10:15, exactly now: over
    calendar = FakeCalendar(later, ended, tomorrow)
    agenda = read_agenda(calendar, FakeBindings(), NOW)
    assert agenda.current is None
    assert agenda.next == tomorrow
    assert calendar.windows == [Window(since=NOW, until=datetime(2026, 10, 28, tzinfo=BERLIN))]
    assert text(agenda) == "Now:  free\nNext: Planning, at Tue 09:00"


def test_all_day_and_cancelled_events_are_skipped():
    holiday = event("Holiday", "2026-10-26T00:00", 24 * 60, all_day=True)
    cancelled_now = event("Cancelled sync", "2026-10-26T10:00", status="cancelled")
    cancelled_next = event("Cancelled 1:1", "2026-10-26T10:30", status="cancelled")
    agenda = read_agenda(FakeCalendar(holiday, cancelled_now, cancelled_next), FakeBindings(), NOW)
    assert agenda.current is None
    assert agenda.next is None
    assert text(agenda) == "Now:  free\nNext: nothing until the end of tomorrow"


def test_an_adapter_error_degrades_to_its_message():
    expired = HeinzelError("Your Google sign-in expired; run `gws auth login`.")
    agenda = read_agenda(FakeCalendar(error=expired), FakeBindings(), NOW)
    assert agenda.calendar == "google"
    assert agenda.problem == "Your Google sign-in expired; run `gws auth login`."
    assert agenda.fix is None
    assert text(agenda) == "Your Google sign-in expired; run `gws auth login`."


def test_an_adapter_crash_degrades_without_its_message():
    agenda = read_agenda(FakeCalendar(error=RuntimeError("token=secret")), FakeBindings(), NOW)
    assert agenda.problem == "Reading your google calendar failed: RuntimeError"


def test_now_is_in_your_time_zone_or_this_machines():
    assert local_now("Asia/Tokyo").tzinfo == ZoneInfo("Asia/Tokyo")
    assert local_now(None).utcoffset() is not None
