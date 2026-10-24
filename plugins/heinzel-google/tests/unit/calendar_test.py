"""The calendar adapter, against the fake Google."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from heinzel_google import GoogleCalendar

from heinzel import HeinzelError, Window

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
