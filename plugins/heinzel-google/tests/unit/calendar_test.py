"""The calendar adapter, against the fake Google."""

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from heinzel_google import GoogleCalendar, Process

from heinzel import EventChange, EventDraft, HeinzelError, Window

pytestmark = pytest.mark.unit

EVENTS = ("calendar", "events", "list")
SINCE = datetime(2026, 10, 19, tzinfo=UTC)
UNTIL = datetime(2026, 10, 26, tzinfo=UTC)
WEEK = Window(since=SINCE, until=UNTIL)
BERLIN = ZoneInfo("Europe/Berlin")


def event(event_id: str, start: str, end: str | None = None, **fields: object) -> dict[str, object]:
    key = "date" if len(start) == 10 else "dateTime"
    item: dict[str, object] = {"id": event_id, "summary": f"Event {event_id}", "start": {key: start}, **fields}
    if end is not None:
        item["end"] = {key: end}
    return item


@pytest.fixture
def events(session, fake_gws):
    def read(*items: dict[str, object], zone: str = "Europe/Berlin") -> list:
        fake_gws.json(EVENTS, {"timeZone": zone, "items": list(items)})
        return GoogleCalendar(session).events(WEEK)

    return read


def test_the_request_expands_recurrences_and_includes_cancelled(session, fake_gws):
    fake_gws.json(EVENTS, {"items": []})
    assert GoogleCalendar(session).events(WEEK) == []
    assert fake_gws.api_runs[0].params == {
        "calendarId": "primary",
        "timeMin": "2026-10-19T00:00:00Z",
        "timeMax": "2026-10-26T00:00:00Z",
        "singleEvents": True,
        "showDeleted": True,
        "maxResults": 250,
    }


def test_events_with_people_and_a_zoom_meeting(events):
    item = event(
        "e1",
        "2026-10-20T10:00:00+02:00",
        "2026-10-20T10:30:00+02:00",
        status="confirmed",
        htmlLink="https://www.google.com/calendar/event?eid=abc",
        organizer={"email": "ada@example.com", "displayName": "Ada \u202eExample", "self": True},
        attendees=[
            {"email": "ada@example.com", "displayName": "Ada Example", "self": True},
            {"email": "grace@example.com"},
            {"email": "room@resource.calendar.google.com", "displayName": "Room 1", "resource": True},
        ],
        conferenceData={
            "conferenceId": "81234567890",
            "conferenceSolution": {"key": {"type": "addOn"}},
            "entryPoints": [{"entryPointType": "video", "uri": "https://acme.zoom.us/j/81234567890?pwd=secret"}],
        },
    )
    [found] = events(item)
    assert found.model_dump() == {
        "id": "e1",
        "title": "Event e1",
        "start": datetime(2026, 10, 20, 8, tzinfo=UTC),
        "end": datetime(2026, 10, 20, 8, 30, tzinfo=UTC),
        "all_day": False,
        "organizer": {"name": "Ada Example", "email": "ada@example.com"},
        "attendees": [
            {"name": "Ada Example", "email": "ada@example.com"},
            {"name": None, "email": "grace@example.com"},
        ],
        "status": "confirmed",
        "conference_id": "81234567890",
        "conference_url": "https://zoom.us/j/81234567890",
        "url": "https://www.google.com/calendar/event?eid=abc",
        "location": None,
        "response": "pending",
        "organized_by_user": True,
    }


def test_a_zoom_link_in_the_description_or_location_and_a_meet_code(events):
    zoom = event("z", "2026-10-21T09:00:00Z", "2026-10-21T10:00:00Z", description="Join https://zoom.us/j/987654321")
    room = event("r", "2026-10-21T11:00:00Z", "2026-10-21T12:00:00Z", location="https://us02web.zoom.us/my/12345678901")
    meet = event(
        "m",
        "2026-10-21T13:00:00Z",
        "2026-10-21T14:00:00Z",
        hangoutLink="https://meet.google.com/abc-defg-hij?authuser=0",
        conferenceData={"conferenceId": "abc-defg-hij"},
        htmlLink="https://evil.example/event",
    )
    other = event(
        "o",
        "2026-10-21T15:00:00Z",
        "2026-10-21T16:00:00Z",
        conferenceData={"conferenceId": "Not A Code", "entryPoints": [{"entryPointType": "video", "uri": "http://x"}]},
        hangoutLink="https://meet.example/x#frag",
    )
    found = events(zoom, room, meet, other)
    assert [(e.conference_id, e.conference_url, e.url) for e in found] == [
        ("987654321", "https://zoom.us/j/987654321", None),
        ("12345678901", "https://zoom.us/j/12345678901", None),
        ("abc-defg-hij", "https://meet.google.com/abc-defg-hij", None),
        (None, None, None),  # not a conferencing host
    ]


def test_all_day_events_start_at_midnight_in_the_calendars_zone(events):
    [found] = events(event("d", "2026-10-22", "2026-10-23"))
    assert found.all_day
    assert found.start == datetime(2026, 10, 22, tzinfo=BERLIN)
    assert found.end - found.start == timedelta(days=1)


def test_an_unknown_zone_is_utc_and_a_naive_time_takes_the_events_zone(events):
    naive = event("n", "2026-10-22T09:00:00", "2026-10-22T10:00:00")
    naive["start"]["timeZone"] = "America/New_York"
    [day, timed] = events(event("d", "2026-10-22", "2026-10-23"), naive, zone="Mars/Olympus")
    assert day.start == datetime(2026, 10, 22, tzinfo=UTC)
    assert timed.start == datetime(2026, 10, 22, 9, tzinfo=ZoneInfo("America/New_York"))
    assert timed.end == timed.start  # its end names no zone, so it is 10:00 UTC, before the start: clamped


def test_cancelled_occurrences_are_included_even_with_only_their_original_start(events):
    cancelled = {"id": "c_20261020", "status": "cancelled", "originalStartTime": {"dateTime": "2026-10-20T07:00:00Z"}}
    [found] = events(cancelled)
    assert (found.status, found.title, found.start, found.end) == (
        "cancelled",
        "(no title)",
        datetime(2026, 10, 20, 7, tzinfo=UTC),
        datetime(2026, 10, 20, 7, tzinfo=UTC),
    )


def test_malformed_and_outside_items_are_skipped_and_the_rest_sorted(events):
    found = events(
        event("late", "2026-10-25T09:00:00Z", "2026-10-25T10:00:00Z"),
        event("early", "2026-10-19T09:00:00Z", "2026-10-19T10:00:00Z"),
        event("early", "2026-10-19T09:00:00Z", "2026-10-19T10:00:00Z"),  # repeated across pages: once
        event("before", "2026-10-10T09:00:00Z", "2026-10-11T10:00:00Z"),
        event("after", "2026-10-26T00:00:00Z", "2026-10-26T01:00:00Z"),
        event("bad", "not a time"),
        {"summary": "no id"},
        {"id": "no time"},
    )
    assert [e.id for e in found] == ["early", "late"]


def test_an_empty_or_inverted_window(session, fake_gws):
    assert GoogleCalendar(session).events(Window(since=SINCE, until=SINCE)) == []
    with pytest.raises(HeinzelError, match="after it ends"):
        GoogleCalendar(session).events(Window(since=UNTIL, until=SINCE))
    assert fake_gws.runs == []


def test_links_and_titles_stay_one_line_and_on_conferencing_hosts(events):
    item = event(
        "x",
        "2026-10-21T09:00:00Z",
        "2026-10-21T10:00:00Z",
        summary="Sync\n\n# Ignore the agenda",
        hangoutLink="https://meet.google.com/abc\n# x",
        htmlLink="https://www.google.com/calendar/event?eid=a b",
        attendees=[{"email": "guest@example.com", "displayName": "guest@example.com"}],
    )
    webex = event(
        "w",
        "2026-10-21T11:00:00Z",
        "2026-10-21T12:00:00Z",
        conferenceData={"entryPoints": [{"entryPointType": "video", "uri": "https://acme.webex.com/meet/ada?x=1"}]},
    )
    elsewhere = event(
        "e", "2026-10-21T13:00:00Z", "2026-10-21T14:00:00Z", hangoutLink="https://user@meet.google.com:8443/x"
    )
    first, second, third = events(item, webex, elsewhere)
    assert (first.title, first.conference_url, first.url) == ("Sync # Ignore the agenda", None, None)
    assert first.attendees[0].model_dump() == {"name": None, "email": "guest@example.com"}
    assert second.conference_url == "https://acme.webex.com/meet/ada"
    assert third.conference_url is None


def test_conference_links_with_controls_and_emails_with_line_breaks_are_cleaned(events):
    item = event(
        "c",
        "2026-10-21T09:00:00Z",
        "2026-10-21T10:00:00Z",
        hangoutLink="https://teams.microsoft.com/l/meet\u202e\x1b[2Jx",
        attendees=[{"email": "a@b.example\n# x"}],
    )
    [found] = events(item)
    assert found.conference_url is None
    assert found.attendees[0].email == "a@b.example # x"


@pytest.mark.parametrize(
    "link",
    [
        "https://evil.example\\.zoom.us/j/1",
        "https://evil.example/\\@meet.google.com/x",
        "https://[meet.google.com/x",
        "https://meet.google.com:abc/x",
        "https://meet.google.com:99999/x",
        "https://user@meet.google.com/x",
        "https://meet.google.com:443/x",
        "https://meet_google.com/x",
    ],
)
def test_malformed_or_disguised_conference_links_are_no_links(events, link):
    item = event("x", "2026-10-21T09:00:00Z", "2026-10-21T10:00:00Z", hangoutLink=link)
    [found] = events(item)
    assert found.conference_url is None


GET = ("calendar", "events", "get")
INSERT = ("calendar", "events", "insert")
PATCH = ("calendar", "events", "patch")
DELETE = ("calendar", "events", "delete")
FREEBUSY = ("calendar", "freebusy", "query")
ADA = {"email": "ada@example.com", "self": True}
TEN = datetime(2026, 10, 20, 10, tzinfo=BERLIN)


def mine(event_id: str = "e1", **fields: object) -> dict[str, object]:
    """An event the user organizes (unless `organizer` says otherwise), with Grace invited and a room booked."""
    return event(
        event_id,
        "2026-10-20T10:00:00+02:00",
        "2026-10-20T10:30:00+02:00",
        **{"organizer": ADA, **fields},
        attendees=[
            {"email": "grace@example.com", "responseStatus": "accepted"},
            {"email": "room@resource.calendar.google.com", "resource": True},
        ],
    )


def test_an_event_says_where_whether_it_is_yours_and_your_answer(events):
    invited = event(
        "i",
        "2026-10-20T12:00:00Z",
        "2026-10-20T13:00:00Z",
        location=" Room\u202e 4 ",
        organizer={"email": "grace@example.com"},
        attendees=[{"email": "ada@example.com", "self": True, "responseStatus": "tentative"}],
    )
    found = {e.id: e for e in events(invited, mine())}
    assert (found["i"].location, found["i"].response, found["i"].organized_by_user) == ("Room 4", "tentative", False)
    assert (found["e1"].location, found["e1"].response, found["e1"].organized_by_user) == (None, None, True)


def test_free_busy_asks_for_the_user_and_each_person_and_marks_hidden_calendars(session, fake_gws):
    fake_gws.json(
        FREEBUSY,
        {
            "calendars": {
                "primary": {
                    "busy": [
                        {"start": "2026-10-20T09:00:00Z", "end": "2026-10-20T10:00:00Z"},
                        {"start": "2026-10-18T09:00:00Z", "end": "2026-10-19T01:00:00Z"},  # cut to the window
                        {"start": "2026-10-18T09:00:00Z", "end": "2026-10-19T00:00:00Z"},  # before it: dropped
                        {"start": "nonsense", "end": "2026-10-20T10:00:00Z"},
                    ]
                },
                "grace@example.com": {"busy": []},
                "zoe@example.com": {"errors": [{"reason": "notFound"}]},
            }
        },
    )
    you, grace, zoe = GoogleCalendar(session).free_busy(" Grace@Example.com, zoe@example.com,,", WEEK)
    assert you.who.email is None
    assert [(b.start, b.end) for b in you.busy] == [
        (SINCE, datetime(2026, 10, 19, 1, tzinfo=UTC)),
        (datetime(2026, 10, 20, 9, tzinfo=UTC), datetime(2026, 10, 20, 10, tzinfo=UTC)),
    ]
    assert (grace.who.email, grace.known, grace.busy) == ("grace@example.com", True, [])
    assert (zoe.who.email, zoe.known) == ("zoe@example.com", False)
    (run,) = fake_gws.api_runs
    assert run.body == {
        "timeMin": "2026-10-19T00:00:00Z",
        "timeMax": "2026-10-26T00:00:00Z",
        "items": [{"id": "primary"}, {"id": "grace@example.com"}, {"id": "zoe@example.com"}],
    }


@pytest.mark.parametrize(
    ("people", "problem"),
    [
        ("not an address", "by address, comma-separated"),
        ("Grace <grace@example.com>", "by address, comma-separated"),
        (",".join(f"p{i}@example.com" for i in range(50)), "at most 49 people"),
    ],
    ids=["no address", "a display name", "fifty people and you"],
)
def test_free_busy_refuses_what_is_no_list_of_addresses_or_too_many(session, fake_gws, people, problem):
    with pytest.raises(HeinzelError, match=problem):
        GoogleCalendar(session).free_busy(people, WEEK)
    assert fake_gws.api_runs == []


def test_free_busy_of_an_empty_window_asks_google_nothing(session, fake_gws):
    moment = Window(since=SINCE, until=SINCE)
    you, grace = GoogleCalendar(session).free_busy("grace@example.com", moment)
    assert (you.who.email, grace.who.email, you.busy, grace.busy) == (None, "grace@example.com", [], [])
    assert fake_gws.api_runs == []


def test_creating_an_event_invites_and_adds_a_video_call_when_asked(session, fake_gws):
    fake_gws.json(INSERT, mine("new", summary="Plan", location="Room 4"))
    draft = EventDraft(
        title="Plan",
        start=TEN,
        end=TEN + timedelta(minutes=30),
        attendees=["grace@example.com"],
        location="Room 4",
        description="Agenda",
        conference=True,
    )
    created = GoogleCalendar(session).create_event(draft)
    assert (created.id, created.title, created.organized_by_user) == ("new", "Plan", True)
    (run,) = fake_gws.api_runs
    assert run.params == {"calendarId": "primary", "sendUpdates": "all", "conferenceDataVersion": 1}
    body = run.body
    assert body is not None
    assert re.fullmatch(r"[0-9a-f]{32}", body.pop("id"))  # ours: a retried insert can't make a second event
    request = body.pop("conferenceData")["createRequest"]
    assert request["conferenceSolutionKey"] == {"type": "hangoutsMeet"}
    assert len(request["requestId"]) == 32
    assert body == {
        "summary": "Plan",
        "start": {"dateTime": "2026-10-20T10:00:00+02:00"},
        "end": {"dateTime": "2026-10-20T10:30:00+02:00"},
        "attendees": [{"email": "grace@example.com"}],
        "location": "Room 4",
        "description": "Agenda",
    }


def test_an_all_day_event_is_written_as_dates_and_without_invitations_when_asked(session, fake_gws):
    fake_gws.json(INSERT, event("d", "2026-10-21", "2026-10-22", organizer=ADA))
    day = datetime(2026, 10, 21, tzinfo=BERLIN)
    draft = EventDraft(title="Offsite", start=day, end=day + timedelta(days=1), all_day=True, notify=False)
    GoogleCalendar(session).create_event(draft)
    (run,) = fake_gws.api_runs
    assert run.params == {"calendarId": "primary", "sendUpdates": "none"}
    body = run.body
    assert body is not None
    body.pop("id")
    assert body == {"summary": "Offsite", "start": {"date": "2026-10-21"}, "end": {"date": "2026-10-22"}}


def test_moving_an_event_keeps_its_length_and_the_answers_of_those_who_stay(session, fake_gws):
    fake_gws.json(GET, mine())
    fake_gws.json(PATCH, mine(summary="Moved"))
    change = EventChange(start=TEN + timedelta(hours=2), attendees=["grace@example.com", "zoe@example.com"])
    moved = GoogleCalendar(session).update_event("e1", change)
    assert moved.title == "Moved"
    get, patch = fake_gws.api_runs
    assert get.params == {"calendarId": "primary", "eventId": "e1"}
    assert patch.params == {"calendarId": "primary", "eventId": "e1", "sendUpdates": "all"}
    assert patch.body == {
        "start": {"dateTime": "2026-10-20T12:00:00+02:00"},
        "end": {"dateTime": "2026-10-20T12:30:00+02:00"},
        "attendees": [
            {"email": "room@resource.calendar.google.com", "resource": True},
            {"email": "grace@example.com", "responseStatus": "accepted"},
            {"email": "zoe@example.com"},
        ],
    }


def test_a_new_end_alone_keeps_the_start_and_a_title_alone_moves_nothing(session, fake_gws):
    fake_gws.json(GET, mine())
    fake_gws.json(PATCH, mine())
    calendar = GoogleCalendar(session)
    calendar.update_event("e1", EventChange(end=TEN + timedelta(hours=1), notify=False))
    calendar.update_event("e1", EventChange(title="Renamed", location="Room 5", description="New"))
    first, second = (r for r in fake_gws.api_runs if r.command == PATCH)
    assert first.params["sendUpdates"] == "none"
    assert first.body == {
        "start": {"dateTime": "2026-10-20T10:00:00+02:00"},
        "end": {"dateTime": "2026-10-20T11:00:00+02:00"},
    }
    assert second.body == {"summary": "Renamed", "location": "Room 5", "description": "New"}


def test_an_event_that_would_end_before_it_starts_is_refused(session, fake_gws):
    fake_gws.json(GET, mine())
    with pytest.raises(HeinzelError, match="would end"):
        GoogleCalendar(session).update_event("e1", EventChange(end=TEN - timedelta(hours=3)))
    assert [r.command for r in fake_gws.api_runs] == [GET]


def test_someone_elses_event_is_neither_moved_nor_cancelled_unless_its_guests_may_change_it(session, fake_gws):
    theirs = mine(organizer={"email": "grace@example.com"})
    fake_gws.json(GET, theirs)
    calendar = GoogleCalendar(session)
    with pytest.raises(HeinzelError, match="ask its organizer to change it"):
        calendar.update_event("e1", EventChange(title="Mine now"))
    with pytest.raises(HeinzelError, match="decline it instead"):
        calendar.cancel_event("e1", notify=True)
    assert {r.command for r in fake_gws.api_runs} == {GET}
    fake_gws.json(GET, {**theirs, "guestsCanModify": True})
    fake_gws.json(PATCH, theirs)
    calendar.update_event("e1", EventChange(title="Shared"))
    assert fake_gws.api_runs[-1].command == PATCH


def test_cancelling_your_event_deletes_it_and_tells_the_guests_if_asked(session, fake_gws):
    fake_gws.json(GET, mine())
    fake_gws.on(DELETE, Process(0, b""))
    cancelled = GoogleCalendar(session).cancel_event("e1", notify=False)
    assert (cancelled.id, cancelled.notified) == ("e1", False)
    assert fake_gws.api_runs[-1].params == {"calendarId": "primary", "eventId": "e1", "sendUpdates": "none"}


def test_a_cancelled_or_unknown_event_is_not_written(session, fake_gws):
    fake_gws.json(GET, mine(status="cancelled"))
    with pytest.raises(HeinzelError, match="has no event e1 any more"):
        GoogleCalendar(session).cancel_event("e1", notify=True)


@pytest.mark.parametrize("bad", ["../../users/me/settings", "e1?x=y", "", "e" * 1025])
def test_an_event_id_that_could_name_another_path_never_reaches_gws(session, fake_gws, bad):
    calendar = GoogleCalendar(session)
    for write in (
        lambda: calendar.update_event(bad, EventChange(title="x")),
        lambda: calendar.cancel_event(bad, notify=True),
        lambda: calendar.respond(bad, "accepted"),
    ):
        with pytest.raises(HeinzelError, match="is not a Google Calendar event id"):
            write()
    assert fake_gws.api_runs == []


def test_answering_an_invitation_sets_only_your_own_entry(session, fake_gws):
    invited = event(
        "i",
        "2026-10-20T12:00:00Z",
        "2026-10-20T13:00:00Z",
        organizer={"email": "grace@example.com"},
        attendees=[
            {"email": "grace@example.com", "responseStatus": "accepted"},
            {**ADA, "responseStatus": "needsAction"},
        ],
    )
    fake_gws.json(GET, invited)
    fake_gws.json(PATCH, {**invited, "attendees": [{**ADA, "responseStatus": "declined"}]})
    answered = GoogleCalendar(session).respond("i", "declined")
    assert answered.response == "declined"
    patch = fake_gws.api_runs[-1]
    assert patch.params == {"calendarId": "primary", "eventId": "i", "sendUpdates": "all"}
    assert patch.body == {
        "attendees": [
            {"email": "grace@example.com", "responseStatus": "accepted"},
            {**ADA, "responseStatus": "declined"},
        ]
    }


def test_an_answer_needs_an_invitation_and_a_known_word(session, fake_gws):
    fake_gws.json(GET, mine())
    calendar = GoogleCalendar(session)
    with pytest.raises(HeinzelError, match="one of accepted, declined, tentative"):
        calendar.respond("e1", "maybe")
    with pytest.raises(HeinzelError, match="not among the guests"):
        calendar.respond("e1", "accepted")
    assert {r.command for r in fake_gws.api_runs} == {GET}


def test_an_answer_without_an_event_says_to_check_the_calendar(session, fake_gws):
    fake_gws.json(INSERT, {"status": "confirmed"})
    with pytest.raises(HeinzelError, match="lacks the event's id or time"):
        GoogleCalendar(session).create_event(EventDraft(title="x", start=TEN, end=TEN + timedelta(hours=1)))


def test_a_recurring_series_is_never_moved_or_cancelled_as_a_whole(session, fake_gws):
    fake_gws.json(GET, mine("series", recurrence=["RRULE:FREQ=WEEKLY"]))
    calendar = GoogleCalendar(session)
    with pytest.raises(HeinzelError, match="is a recurring series, not one occurrence"):
        calendar.cancel_event("series", notify=True)
    with pytest.raises(HeinzelError, match="is a recurring series"):
        calendar.update_event("series", EventChange(title="x"))
    assert {r.command for r in fake_gws.api_runs} == {GET}


def test_an_answer_with_omitted_guests_sends_back_only_your_entry(session, fake_gws):
    yours = {**ADA, "responseStatus": "needsAction"}
    invited = event("i", "2026-10-20T12:00:00Z", "2026-10-20T13:00:00Z", attendees=[yours], attendeesOmitted=True)
    fake_gws.json(GET, invited)
    fake_gws.json(PATCH, invited)
    GoogleCalendar(session).respond("i", "tentative")
    assert fake_gws.api_runs[-1].body == {"attendees": [{**ADA, "responseStatus": "tentative"}]}
