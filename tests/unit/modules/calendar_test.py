"""The calendar module's service, times and rendering, with a fixed clock and a calendar in memory."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from rich.console import Console

from heinzel import (
    Event,
    EventChange,
    EventDraft,
    FreeBusy,
    HeinzelError,
    Participant,
    TimeSpan,
    Window,
)
from heinzel.modules.calendar import _render, _service, _time
from heinzel.modules.calendar._models import CalendarChange, CalendarSettings, FreeTime, ShownEvent
from heinzel.modules.calendar._time import Profile

pytestmark = pytest.mark.unit

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 26, 10, 0, tzinfo=BERLIN)  # a Monday
GRACE = Participant(name="Grace", email="grace@example.com")


class Clock(Profile):
    """Your profile at a fixed time."""

    def now(self) -> datetime:
        return NOW


YOU = Clock(zone=BERLIN, start=9, end=17)


def meeting(event_id: str, start: int, minutes: int = 30, **fields: object) -> Event:
    begin = NOW + timedelta(minutes=start)
    return Event(id=event_id, title=event_id.title(), start=begin, end=begin + timedelta(minutes=minutes), **fields)


class Book:
    """A calendar in memory that records what it was asked."""

    def __init__(self, *events: Event, busy: list[FreeBusy] | None = None) -> None:
        self.found = list(events)
        self.busy = busy or []
        self.windows: list[Window] = []
        self.asked: list[str] = []

    def events(self, window: Window) -> list[Event]:
        self.windows.append(window)
        return [e for e in self.found if e.start < window.until and e.end > window.since]

    def free_busy(self, people: str, window: Window) -> list[FreeBusy]:
        self.asked.append(people)
        return self.busy


@pytest.mark.parametrize(
    ("current", "upcoming", "line"),
    [
        (meeting("standup", -5, 17), None, "Standup · ends in 12 min"),
        (meeting("standup", -5, 17), meeting("review", 25), "Standup · ends in 12 min · next: Review in 25 min"),
        (meeting("offsite", -60, 300), None, "Offsite · until 14:00"),
        (None, meeting("one", 25), "Next: One in 25 min"),
        (None, meeting("review", 300), "Next: Review at 15:00"),
        (None, meeting("review", 24 * 60), "Next: Review at Tue 10:00"),
        (None, None, "No meetings until the end of tomorrow"),
    ],
)
def test_the_countdown_line(current, upcoming, line):
    assert _service.countdown_line(current, upcoming, NOW) == line


def test_the_countdown_skips_all_day_cancelled_and_declined_events_and_takes_the_latest_start():
    events = [
        meeting("early", -30, 60),
        meeting("late", -10, 60),
        meeting("day", -600, 24 * 60, all_day=True),
        meeting("gone", 10, status="cancelled"),
        meeting("nope", 15, response="declined"),
        meeting("next", 20),
    ]
    countdown = _service.countdown(events, NOW)
    assert (countdown.current and countdown.current.id, countdown.next and countdown.next.id) == ("late", "next")


def test_a_failing_calendar_comes_back_as_the_countdowns_problem():
    class Broken(Book):
        def events(self, window: Window) -> list[Event]:
            raise RuntimeError

    countdown = _service.read_countdown(Broken(), YOU)  # type: ignore[arg-type]
    assert countdown.problem == "Reading your calendar failed: RuntimeError"
    assert countdown.line == "Calendar unavailable"


def test_a_day_and_a_week_are_read_from_your_midnights_without_cancelled_events():
    book = Book(meeting("kept", 60), meeting("gone", 90, status="cancelled"))
    today = _service.day(book, YOU)  # type: ignore[arg-type]
    assert [e.id for e in today.events] == ["kept"]
    assert (today.since, today.until) == (
        datetime(2026, 10, 26, tzinfo=BERLIN),
        datetime(2026, 10, 27, tzinfo=BERLIN),
    )
    week = _service.week(book, YOU)  # type: ignore[arg-type]
    assert week.until - week.since == timedelta(days=7)


def test_search_matches_every_word_in_the_title_location_or_names():
    book = Book(
        meeting("planning", 60, location="Room 4", attendees=[GRACE]),
        meeting("review", 90),
    )
    assert [e.id for e in _service.search(book, YOU, "room GRACE", 7).events] == ["planning"]  # type: ignore[arg-type]
    with pytest.raises(HeinzelError, match="Search for some words"):
        _service.search(book, YOU, "  ", 7)  # type: ignore[arg-type]


def test_find_looks_around_today_by_id():
    book = Book(meeting("a", 60))
    assert _service.find(book, YOU, "a", 3).id == "a"  # type: ignore[arg-type]
    window = book.windows[0]
    assert window.until - window.since == timedelta(days=7)
    with pytest.raises(HeinzelError, match="No event 'b' within 3 days"):
        _service.find(book, YOU, "b", 3)  # type: ignore[arg-type]


def test_free_time_lies_between_everybodys_busy_spans_within_working_hours():
    def span(start: int, minutes: int) -> TimeSpan:
        return TimeSpan(start=NOW + timedelta(minutes=start), end=NOW + timedelta(minutes=start + minutes))

    book = Book(
        busy=[
            FreeBusy(who=Participant(), busy=[span(0, 60), span(90, 30)]),
            FreeBusy(who=GRACE, busy=[span(100, 60)]),
            FreeBusy(who=Participant(email="zoe@example.com"), known=False),
        ]
    )
    found = _service.free(book, YOU, None, "grace@example.com, zoe@example.com", 30)  # type: ignore[arg-type]
    assert book.asked == ["grace@example.com,zoe@example.com"]
    assert [(s.start.hour, s.start.minute, s.end.hour, s.end.minute) for s in found.slots] == [
        (11, 0, 11, 30),
        (12, 40, 17, 0),
    ]
    assert found.unknown == ["zoe@example.com"]
    tomorrow = _service.free(book, YOU, NOW.date() + timedelta(days=1), "", 30)  # type: ignore[arg-type]
    assert tomorrow.since == datetime(2026, 10, 27, 9, tzinfo=BERLIN)


def test_free_time_refuses_names_and_ends_with_the_working_day():
    with pytest.raises(HeinzelError, match=r"--people ada@example\.com"):
        _service.free(Book(), YOU, None, "Grace", 30)  # type: ignore[arg-type]
    late = Clock(zone=BERLIN, start=9, end=10)
    assert _service.free(Book(), late, None, "", 30).slots == []  # type: ignore[arg-type]


class Writer(Book):
    def __init__(self) -> None:
        super().__init__()
        self.done: list[object] = []

    def create_event(self, event: EventDraft) -> Event:
        self.done.append(event)
        return meeting("new", 0)

    def update_event(self, event: str, change: EventChange) -> Event:
        self.done.append((event, change))
        return meeting(event, 0)

    def cancel_event(self, event: str, notify: bool) -> object:
        self.done.append(("cancel", event, notify))
        return None

    def respond(self, event: str, response: str) -> Event:
        self.done.append((event, response))
        return meeting(event, 0)


def test_every_write_only_says_what_it_would_do_until_confirmed():
    book = Writer()
    draft = EventDraft(title="Plan", start=NOW, end=NOW + timedelta(minutes=30), attendees=["grace@example.com"])
    mine = meeting(
        "plan", 60, organized_by_user=True, organizer=Participant(email="ada@example.com"), attendees=[GRACE]
    )
    invited = meeting("one", 60, response="pending", organizer=GRACE)
    shown = [
        _service.create(book, YOU, draft, ["new", "Plan", "10:00"], confirm=False),  # type: ignore[arg-type]
        _service.move(book, YOU, mine, EventChange(start=NOW), ["move", "plan", "10:00"], confirm=False),  # type: ignore[arg-type]
        _service.cancel(book, YOU, mine, ["cancel", "plan"], notify=True, confirm=False),  # type: ignore[arg-type]
        _service.respond(book, YOU, invited, "accepted", ["respond", "one", "accepted"], confirm=False),  # type: ignore[arg-type]
    ]
    assert book.done == []
    assert [c.done for c in shown] == [False] * 4
    assert [c.summary for c in shown] == [
        "Would create 'Plan' on Mon 26 Oct 10:00-10:30, inviting grace@example.com.",
        "Would move 'Plan' from Mon 26 Oct 11:00-11:30 to Mon 26 Oct 10:00-10:30.",
        "Would cancel 'Plan' on Mon 26 Oct 11:00-11:30 for everyone, telling 1 guest(s).",
        "Would answer 'One' on Mon 26 Oct 11:00-11:30 with accepted, telling Grace.",
    ]
    assert shown[0].confirm == "hz calendar new Plan 10:00 --confirm"
    _service.create(book, YOU, draft, [], confirm=True)  # type: ignore[arg-type]
    _service.move(book, YOU, mine, EventChange(start=NOW, notify=False), [], confirm=True)  # type: ignore[arg-type]
    _service.cancel(book, YOU, mine, [], notify=False, confirm=True)  # type: ignore[arg-type]
    done = _service.respond(book, YOU, invited, "declined", [], confirm=True)  # type: ignore[arg-type]
    assert done.done is True
    assert book.done[2] == ("cancel", "plan", False)
    assert book.done[3] == ("one", "declined")


def test_writes_refuse_what_isnt_yours_to_change_or_answer():
    book = Writer()
    theirs = meeting("one", 60, organizer=GRACE, response="pending")
    with pytest.raises(HeinzelError, match="ask its organizer"):
        _service.move(book, YOU, theirs, EventChange(title="x"), [], confirm=True)  # type: ignore[arg-type]
    with pytest.raises(HeinzelError, match="hz calendar respond one declined"):
        _service.cancel(book, YOU, theirs, [], notify=True, confirm=True)  # type: ignore[arg-type]
    with pytest.raises(HeinzelError, match="no invitation to answer"):
        _service.respond(book, YOU, meeting("mine", 60, organized_by_user=True), "accepted", [], confirm=True)  # type: ignore[arg-type]
    assert book.done == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("10:30", datetime(2026, 10, 26, 10, 30, tzinfo=BERLIN)),
        ("Tomorrow  9:05", datetime(2026, 10, 27, 9, 5, tzinfo=BERLIN)),
        ("2026-10-28 14:00", datetime(2026, 10, 28, 14, tzinfo=BERLIN)),
        ("2026-10-28T14:00+00:00", datetime(2026, 10, 28, 14, tzinfo=UTC)),
    ],
)
def test_times_are_read_as_people_type_them(text, expected):
    assert _time.parse_when(text, BERLIN, NOW) == expected


@pytest.mark.parametrize("text", ["soon", "25:00", "10:61"])
def test_a_time_hz_cant_read_says_what_works(text):
    with pytest.raises(HeinzelError, match="tomorrow 9:00"):
        _time.parse_when(text, BERLIN, NOW)


def test_days_are_today_tomorrow_or_a_date():
    assert _service.on(None, YOU) is None
    assert _service.on("tomorrow", YOU) == NOW.date() + timedelta(days=1)
    assert _service.on("2026-11-02", YOU) == date(2026, 11, 2)
    with pytest.raises(HeinzelError, match="names no day"):
        _service.on("someday", YOU)


@pytest.mark.parametrize(
    ("org", "zone", "hours"),
    [
        ({"timezone": "Asia/Tokyo", "working_hours": [8, 16]}, "Asia/Tokyo", (8, 16)),
        ({"timezone": "Asia/Tokyo", "working_hours": "10-18"}, "Asia/Tokyo", (10, 18)),
        ({"timezone": "Asia/Tokyo", "working_hours": '["07:00", "15:00"]'}, "Asia/Tokyo", (7, 15)),
        ({"timezone": "Asia/Tokyo", "working_hours": [18, 9]}, "Asia/Tokyo", (9, 18)),
        ({"timezone": "Asia/Tokyo", "working_hours": "[broken"}, "Asia/Tokyo", (9, 18)),
        ({"timezone": "Asia/Tokyo", "working_hours": ["x", "y"]}, "Asia/Tokyo", (9, 18)),
    ],
)
def test_the_profile_gives_the_zone_and_working_hours(org, zone, hours):
    found = _time.profile(org)
    assert (str(found.zone), (found.start, found.end)) == (zone, hours)


def test_a_broken_profile_zone_falls_back_to_this_machine(monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Lisbon")
    assert str(_time.profile({"timezone": "Not/AZone"}).zone) == "Europe/Lisbon"
    assert _time.profile({}).start == 9


def text_of(renderable) -> str:
    console = Console(width=200, record=True)
    console.print(renderable)
    return console.export_text()


def test_the_terminal_shows_events_the_countdown_free_time_and_changes():
    book = Book(meeting("standup", -5, 17, conference_url="https://meet.google.com/abc", attendees=[GRACE] * 6))
    rendered = text_of(_render.render_agenda(_service.day(book, YOU)))  # type: ignore[arg-type]
    assert "Standup (standup)" in rendered
    assert "+2" in rendered
    assert "https://meet.google.com/abc" in rendered
    assert "Nothing in your calendar" in text_of(_render.render_agenda(_service.day(Book(), YOU)))  # type: ignore[arg-type]
    countdown = _service.countdown(book.found, NOW)
    assert text_of(_render.render_countdown(countdown)).splitlines()[:2] == [
        "Standup · ends in 12 min",
        "Join: https://meet.google.com/abc",
    ]
    broken = countdown.model_copy(update={"problem": "No calendar connected.", "fix": "hz setup google"})
    assert "Run `hz setup google`" in text_of(_render.render_countdown(broken))
    plan = meeting("plan", 0, location="Room 4", organizer=GRACE, response="tentative")
    event = text_of(_render.render_event(ShownEvent(now=NOW, event=plan)))
    assert "Room 4" in event
    assert "maybe" in event
    free = FreeTime(since=NOW, until=NOW, minutes=30, unknown=["zoe@example.com"])
    assert "No free 30 minutes" in text_of(_render.render_free(free))
    assert "Not known for zoe@example.com" in text_of(_render.render_free(free))
    slots = free.model_copy(update={"slots": [TimeSpan(start=NOW, end=NOW + timedelta(hours=1))], "unknown": []})
    assert "Mon 26 Oct 10:00-11:00" in text_of(_render.render_free(slots))
    shown = CalendarChange(done=False, summary="Would cancel it.", confirm="hz calendar cancel x --confirm")
    assert "To do it: hz calendar cancel x --confirm" in text_of(_render.render_change(shown))
    assert text_of(_render.render_change(CalendarChange(done=True, summary="Done."))).strip() == "Done."


def test_spans_of_all_day_and_multi_day_events():
    day = Event(id="d", title="Off", start=NOW, end=NOW + timedelta(days=2), all_day=True)
    assert _service._span(day, YOU) == "Mon 26 Oct to Tue 27 Oct"
    long = meeting("trip", 0, 26 * 60)
    assert _service._span(long, YOU) == "Mon 26 Oct 10:00 to Tue 27 Oct 12:00"


def test_without_a_named_zone_this_machine_s_offset_is_used(monkeypatch, tmp_path):
    monkeypatch.setenv("TZ", "/an/absolute/path")
    monkeypatch.setattr(_time, "Path", lambda _: tmp_path)  # /etc/localtime names no zone
    assert _time.local_zone().utcoffset(None) is not None
    assert _time.profile({"working_hours": 9}).start == 9  # neither list nor text: the default


def test_an_event_shows_in_your_time_zone_whatever_zone_the_calendar_gave():
    tokyo = Event(
        id="t",
        title="Sync",
        start=datetime(2026, 10, 26, 18, tzinfo=ZoneInfo("Asia/Tokyo")),
        end=datetime(2026, 10, 26, 19, tzinfo=ZoneInfo("Asia/Tokyo")),
    )
    assert "Mon 26 Oct 10:00-11:00" in text_of(_render.render_event(ShownEvent(now=NOW, event=tokyo)))


def test_invitation_text_is_shown_as_text_never_as_terminal_markup():
    sneaky = meeting(
        "x", 0, attendees=[Participant(name="[bold]Boss[/bold]")], conference_url="[link=https://evil.example]x[/link]"
    )
    sneaky = sneaky.model_copy(update={"title": "[link=https://evil.example]Weekly sync[/link]"})
    console = Console(width=200, record=True)
    console.print(_render.render_agenda(_service.day(Book(sneaky), YOU)))  # type: ignore[arg-type]
    console.print(_render.render_event(ShownEvent(now=NOW, event=sneaky)))
    shown = console.export_text()
    assert "[link=https://evil.example]Weekly sync[/link]" in shown
    assert "[bold]Boss[/bold]" in shown


@pytest.mark.parametrize("minutes", [1, 4])
def test_a_reminder_shorter_than_a_tick_is_refused(minutes):
    with pytest.raises(ValueError, match="0 \\(off\\) or at least 5"):
        CalendarSettings(remind_minutes=minutes)
    assert CalendarSettings(remind_minutes=0).remind_minutes == 0


def test_reminders_look_as_far_ahead_as_the_daemon_ticks(tmp_path):
    state = tmp_path / "calendar" / "remind.json"
    assert _service.look_ahead(state, NOW, 10) == timedelta(minutes=10)  # the first look: the setting
    # A daemon ticking every 15 minutes: the next look sees the 15 minutes since, so a meeting at +12 isn't missed.
    assert _service.look_ahead(state, NOW + timedelta(minutes=15), 10) == timedelta(minutes=15)
    assert _service.look_ahead(state, NOW + timedelta(minutes=20), 10) == timedelta(minutes=10)
    assert _service.look_ahead(state, NOW + timedelta(hours=5), 10) == timedelta(hours=1)  # at most an hour
    state.write_text("damaged", encoding="utf-8")
    assert _service.look_ahead(state, NOW + timedelta(hours=6), 10) == timedelta(minutes=10)
