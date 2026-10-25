"""The Google Calendar adapter of the `calendar` capability (ADR 0028), read and write.

Events come from Calendar API v3 through `gws calendar events list`, calendar `primary`, with `singleEvents=true`
(recurrences expanded) and `showDeleted=true` (cancelled occurrences included), paged by `nextPageToken`. People become
`Participant`s: a display name and an email, nothing else; meeting rooms and other resources are not attendees. The
email is the join key the protocol defines; the workflow packet builder drops it before anything reaches an agent's
prompt (ADR 0028), and nothing this plugin shows besides the capability carries it.

`conference_id` is in the meeting tool's notation, so a `Meeting` with the same id and an overlapping time is the
event's meeting: a Zoom meeting number, found in the conferencing data, the location or the description, else a Meet
code. Conference links lose their query and fragment, which hold passcodes.

Free and busy times come from `gws calendar freebusy query`. Writes go to the primary calendar through `events
insert`, `patch` and `delete` (ADR 0042), invitations and updates sent when the draft says so (`sendUpdates`). An event
is moved or cancelled only when the user organizes it (or its guests may change it); an invitation is answered by
setting the user's own attendee entry. Event ids are checked before they reach `gws`, so none can name another path.
"""

import re
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal, cast
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from heinzel import (
    RESPONSES,
    Event,
    EventCancellation,
    EventChange,
    EventDraft,
    FreeBusy,
    HeinzelError,
    Participant,
    TimeSpan,
    Window,
    clean_text,
    free_busy_people,
)
from heinzel_google._gws import GoogleSession

EVENTS = ("calendar", "events", "list")
GET = ("calendar", "events", "get")
INSERT = ("calendar", "events", "insert")
PATCH = ("calendar", "events", "patch")
DELETE = ("calendar", "events", "delete")
FREEBUSY = ("calendar", "freebusy", "query")
API = "Google Calendar"
_EVENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,1024}$")
"""Google's event ids: base32hex, an occurrence's `_<time>Z` suffix; nothing that could name another path."""
_ZOOM = re.compile(r"https://(?:[a-z0-9-]+\.)?zoom\.us/(?:j|my|w)/([0-9]{9,11})\b", re.IGNORECASE)
_MEET = re.compile(r"^[a-z]{3}-[a-z]{4}-[a-z]{3}$")
_EVENT_LINK = ("https://www.google.com/calendar/", "https://calendar.google.com/")
_CONFERENCE_HOSTS = frozenset({"meet.google.com", "zoom.us", "teams.microsoft.com", "teams.live.com"})
_CONFERENCE_DOMAINS = (".zoom.us", ".webex.com")
_HOST = re.compile(r"^[a-z0-9.-]+$")
MAX_TEXT = 100_000
"""Characters of a location or description searched for a Zoom link."""
MAX_LOCATION = 300
"""Characters of a location kept on an `Event`."""
MAX_PEOPLE = 49
"""People one free/busy question asks about besides the user: Google answers at most 50 calendars at once."""
_ANSWERS: dict[str, Literal["accepted", "declined", "tentative", "pending"]] = {
    "accepted": "accepted",
    "declined": "declined",
    "tentative": "tentative",
    "needsAction": "pending",
}
"""Google's `responseStatus` in the protocol's words; anything else is `pending`."""

type Json = dict[str, Any]


def _object(value: object) -> Json:
    return cast("Json", value) if isinstance(value, dict) else {}


def _objects(value: object) -> list[Json]:
    if not isinstance(value, list):
        return []
    return [cast("Json", item) for item in cast("list[object]", value) if isinstance(item, dict)]


def _bounds(window: Window) -> tuple[datetime, datetime]:
    if window.since > window.until:
        msg = f"The window starts ({window.since.isoformat()}) after it ends ({window.until.isoformat()})."
        raise HeinzelError(msg)
    return window.since.astimezone(UTC), window.until.astimezone(UTC)


def _zone(name: object) -> ZoneInfo | None:
    try:
        return ZoneInfo(str(name)) if isinstance(name, str) and name else None
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _when(value: object, zone: ZoneInfo | None) -> tuple[datetime, bool] | None:
    """A start or end, and whether it is all-day: `dateTime`, or `date` as midnight in the calendar's zone (or UTC)."""
    point = _object(value)
    try:
        if isinstance(stamp := point.get("dateTime"), str):
            parsed = datetime.fromisoformat(stamp)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=_zone(point.get("timeZone")) or zone or UTC)
            return parsed, False
        if isinstance(day := point.get("date"), str):
            return datetime.combine(date.fromisoformat(day), time(), tzinfo=zone or UTC), True
    except ValueError:
        return None
    return None


def _person(value: Json) -> Participant:
    name = value.get("displayName")
    email = value.get("email")
    return Participant(
        name=" ".join(clean_text(str(name)).split()) if name and "@" not in str(name) else None,  # no address
        email=" ".join(clean_text(str(email)).split()) or None if email else None,
    )


def _link(url: object) -> str | None:
    """A conferencing link without query or fragment (where passcodes live), or None.

    Anyone can put an event on a calendar, so only the conferencing services' own hosts count, over https, in one
    piece without whitespace or backslashes (which browsers read as a slash, so a host "evil.example", backslash,
    ".zoom.us" goes to evil.example); a host is letters, digits, dots and hyphens only. A malformed URL is no link.
    """
    if not isinstance(url, str):
        return None
    bare = url.split("#", 1)[0].split("?", 1)[0]
    if not bare.isprintable() or " " in bare or "\\" in bare:
        return None
    try:
        parts = urlsplit(bare)
        port = parts.port
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or parts.username or port or not _HOST.fullmatch(host):
        return None
    if host in _CONFERENCE_HOSTS or host.endswith(_CONFERENCE_DOMAINS):
        return bare
    return None


def _conference(item: Json) -> tuple[str | None, str | None]:
    """The conference id in the meeting tool's notation and the link to join, from the conferencing data first."""
    data = _object(item.get("conferenceData"))
    entries = _objects(data.get("entryPoints"))
    video = next((e for e in entries if e.get("entryPointType") == "video"), {})
    places = [str(video.get("uri") or ""), str(item.get("location") or "")[:MAX_TEXT]]
    places.append(str(item.get("description") or "")[:MAX_TEXT])
    for place in places:
        if found := _ZOOM.search(place):
            return found.group(1), f"https://zoom.us/j/{found.group(1)}"
    meet = data.get("conferenceId")
    if isinstance(meet, str) and _MEET.fullmatch(meet):
        return meet, _link(video.get("uri")) or _link(item.get("hangoutLink"))
    return None, _link(video.get("uri")) or _link(item.get("hangoutLink"))


def _event(item: Json, zone: ZoneInfo | None) -> Event | None:
    """One occurrence; None if it has no id or no time at all."""
    event_id = item.get("id")
    if not isinstance(event_id, str) or not event_id:
        return None
    start = _when(item.get("start"), zone) or _when(item.get("originalStartTime"), zone)
    if start is None:
        return None
    end = _when(item.get("end"), zone)
    # A cancelled occurrence may carry only its original start: it then lasts no time at all.
    finish = end[0] if end is not None else start[0]
    attendees = [_person(a) for a in _objects(item.get("attendees")) if a.get("resource") is not True]
    organizer = _object(item.get("organizer"))
    conference_id, conference_url = _conference(item)
    link = item.get("htmlLink")
    you = next((a for a in _objects(item.get("attendees")) if a.get("self") is True), None)
    location = " ".join(clean_text(str(item.get("location") or "")).split())[:MAX_LOCATION]
    return Event(
        id=clean_text(event_id),
        title=" ".join(clean_text(str(item.get("summary") or "(no title)")).split()),
        start=start[0],
        end=max(finish, start[0]),
        all_day=start[1],
        organizer=_person(organizer) if organizer else None,
        attendees=attendees,
        status=clean_text(str(item.get("status") or "confirmed")),
        conference_id=conference_id,
        conference_url=conference_url,
        url=link
        if isinstance(link, str) and link.startswith(_EVENT_LINK) and link.isprintable() and " " not in link
        else None,
        location=location or None,
        response=_ANSWERS.get(str(you.get("responseStatus")), "pending") if you is not None else None,
        organized_by_user=organizer.get("self") is True,
    )


class GoogleCalendar:
    """Reads the signed-in user's primary Google Calendar."""

    def __init__(self, session: GoogleSession) -> None:
        """Build the adapter; the kernel injects the session."""
        self._session = session

    def events(self, window: Window) -> list[Event]:
        """The user's events that overlap `window`, recurrences expanded, cancelled ones included, by start.

        Raises:
            HeinzelError: If the window is malformed, the sign-in is missing, Google fails, or the window holds more
                than the adapter reads at once (narrow it).
        """
        since, until = _bounds(window)
        if since == until:
            return []
        params: dict[str, object] = {
            "calendarId": "primary",
            "timeMin": since.isoformat().replace("+00:00", "Z"),
            "timeMax": until.isoformat().replace("+00:00", "Z"),
            "singleEvents": True,
            "showDeleted": True,
            "maxResults": 250,
        }
        found: dict[str, Event] = {}  # by id: an edit between two pages may shift an occurrence
        pages = self._session.pages("Google Calendar", EVENTS, params, "the calendar in that window", "items")
        for items, page in pages:
            zone = _zone(page.get("timeZone"))  # the calendar's zone, for all-day events
            for item in items:
                event = _event(item, zone)
                if event is not None and event.start < until and (event.end > since or event.start >= since):
                    found.setdefault(event.id, event)
        return sorted(found.values(), key=lambda e: (e.start, e.end, e.id))

    def free_busy(self, people: str, window: Window) -> list[FreeBusy]:
        """When the user and `people` (comma-separated addresses) are busy within `window`; the user first.

        Raises:
            HeinzelError: If an address or the window is malformed, the sign-in fails, or Google refuses.
        """
        since, until = _bounds(window)
        try:
            asked = free_busy_people(people)
        except ValueError as exc:
            msg = f"Ask about people by address, comma-separated, e.g. ada@example.com,bo@example.com: {exc}."
            raise HeinzelError(msg) from None
        if len(asked) > MAX_PEOPLE:
            msg = f"Ask about at most {MAX_PEOPLE} people at once; Google answers 50 calendars, yours included."
            raise HeinzelError(msg)
        if since == until:
            return [FreeBusy(who=Participant()), *(FreeBusy(who=Participant(email=a)) for a in asked)]
        body: dict[str, object] = {
            "timeMin": _stamp(since),
            "timeMax": _stamp(until),
            "items": [{"id": "primary"}, *({"id": a} for a in asked)],
        }
        answer, _ = self._session.get(API, FREEBUSY, {}, "free and busy times", body=body)
        calendars = _object(answer.get("calendars"))
        found = [_busy(Participant(), _object(calendars.get("primary")), since, until)]
        found += [_busy(Participant(email=a), _object(calendars.get(a)), since, until) for a in asked]
        return found

    def create_event(self, event: EventDraft) -> Event:
        """Put `event` in the primary calendar, the user organizing it, inviting its attendees if it says so.

        Raises:
            HeinzelError: If the sign-in lacks the write scope (the message names the sign-in), or Google refuses.
        """
        # An id of our own (base32hex: hex digits are), so a retried insert is refused as a duplicate, never doubled.
        body: dict[str, object] = {
            "id": uuid.uuid4().hex,
            "summary": event.title,
            **_times(event.start, event.end, all_day=event.all_day),
        }
        if event.attendees:
            body["attendees"] = [{"email": a} for a in event.attendees]
        if event.location:
            body["location"] = event.location
        if event.description:
            body["description"] = event.description
        params: dict[str, object] = {"calendarId": "primary", "sendUpdates": "all" if event.notify else "none"}
        if event.conference:
            params["conferenceDataVersion"] = 1
            body["conferenceData"] = {
                "createRequest": {"requestId": uuid.uuid4().hex, "conferenceSolutionKey": {"type": "hangoutsMeet"}}
            }
        return self._written(self._session.write(API, INSERT, params, body, f"the new event {event.title!r}"))

    def update_event(self, event: str, change: EventChange) -> Event:
        """Change `event` as `change` says, if the user organizes it or its guests may change it.

        A new start without an end keeps the event's length; new attendees keep the answers of those who stay, and
        rooms stay booked.

        Raises:
            HeinzelError: If the id is malformed, the event is not the user's to change, the times don't fit, the
                sign-in lacks the write scope, or Google refuses.
        """
        item = self._item(event)
        current = self._current(item, event)
        if not (current.organized_by_user or item.get("guestsCanModify") is True):
            msg = f"You don't organize {current.title!r}: ask its organizer to change it, or answer it instead."
            raise HeinzelError(msg)
        body: dict[str, object] = {}
        if change.title is not None:
            body["summary"] = change.title
        if change.location is not None:
            body["location"] = change.location
        if change.description is not None:
            body["description"] = change.description
        if change.start is not None or change.end is not None:
            start = change.start or current.start
            end = change.end or (start + (current.end - current.start) if change.start is not None else current.end)
            if end <= start:
                msg = f"{current.title!r} would end ({end.isoformat()}) before it starts ({start.isoformat()})."
                raise HeinzelError(msg)
            body |= _times(start, end, all_day=current.all_day)
        if change.attendees is not None:
            body["attendees"] = _attendees(_objects(item.get("attendees")), change.attendees)
        params = {"calendarId": "primary", "eventId": event, "sendUpdates": "all" if change.notify else "none"}
        return self._written(self._session.write(API, PATCH, params, body, f"event {current.title!r}"))

    def cancel_event(self, event: str, notify: bool) -> EventCancellation:
        """Cancel `event` for everyone, if the user organizes it; tell the attendees if `notify`.

        Raises:
            HeinzelError: If the id is malformed, the user doesn't organize the event (answer it with `declined`
                instead), the sign-in lacks the write scope, or Google refuses.
        """
        item = self._item(event)
        current = self._current(item, event)
        if not current.organized_by_user:
            msg = f"You don't organize {current.title!r}, so you can't cancel it for everyone: decline it instead."
            raise HeinzelError(msg)
        params = {"calendarId": "primary", "eventId": event, "sendUpdates": "all" if notify else "none"}
        self._session.write(API, DELETE, params, None, f"event {current.title!r}")
        return EventCancellation(id=event, notified=notify)

    def respond(self, event: str, response: str) -> Event:
        """Answer the invitation to `event` with `response` (`accepted`, `declined`, `tentative`); its organizer hears.

        Raises:
            HeinzelError: If the id or answer is malformed, the user is not invited, the sign-in lacks the write scope,
                or Google refuses.
        """
        if response not in RESPONSES:
            msg = f"Answer with one of {', '.join(RESPONSES)}, not {clean_text(response)[:40]!r}."
            raise HeinzelError(msg)
        item = self._item(event)
        current = self._current(item, event)
        attendees = _objects(item.get("attendees"))
        if not any(a.get("self") is True for a in attendees):
            msg = f"You are not among the guests of {current.title!r}, so there is no invitation to answer."
            raise HeinzelError(msg)
        if item.get("attendeesOmitted") is True:  # the list is only yours: sending it back would drop the others
            attendees = [a for a in attendees if a.get("self") is True]
        body = {"attendees": [{**a, "responseStatus": response} if a.get("self") is True else a for a in attendees]}
        params = {"calendarId": "primary", "eventId": event, "sendUpdates": "all"}
        return self._written(self._session.write(API, PATCH, params, body, f"event {current.title!r}"))

    def _item(self, event: str) -> Json:
        if not _EVENT_ID.fullmatch(event):
            msg = f"{clean_text(event)[:100]!r} is not a Google Calendar event id; `hz calendar` lists them."
            raise HeinzelError(msg)
        return self._session.get(API, GET, {"calendarId": "primary", "eventId": event}, f"event {event}")[0]

    @staticmethod
    def _current(item: Json, event: str) -> Event:
        found = _event(item, None)
        if found is None or found.status == "cancelled":
            msg = f"Google Calendar has no event {event} any more; `hz calendar` lists the current ones."
            raise HeinzelError(msg)
        if item.get("recurrence"):  # a series' own id: a write would change every occurrence, for everyone
            msg = f"{event} is a recurring series, not one occurrence; `hz calendar` lists each occurrence's id."
            raise HeinzelError(msg)
        return found

    @staticmethod
    def _written(item: Json) -> Event:
        found = _event(item, None)
        if found is None:
            msg = "Google Calendar's answer to the change lacks the event's id or time; check the calendar."
            raise HeinzelError(msg)
        return found


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _times(start: datetime, end: datetime, *, all_day: bool) -> dict[str, object]:
    """Google's `start` and `end`: dates for an all-day event (in the times' own zone), else instants."""
    if all_day:
        last = end.date() if end.timetz() == time(tzinfo=end.tzinfo) else end.date() + timedelta(days=1)
        return {"start": {"date": start.date().isoformat()}, "end": {"date": max(last, start.date()).isoformat()}}
    return {"start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()}}


def _attendees(current: list[Json], wanted: list[str]) -> list[Json]:
    """The attendee entries for `wanted`: those who stay keep their entry and answer; rooms and the user stay."""
    by_email = {str(a.get("email") or "").lower(): a for a in current}
    kept = [a for a in current if a.get("resource") is True or a.get("self") is True]
    return kept + [by_email.get(email, {"email": email}) for email in wanted if by_email.get(email) not in kept]


def _busy(who: Participant, calendar: Json, since: datetime, until: datetime) -> FreeBusy:
    """One calendar's busy spans within `[since, until)`; unknown when Google reports an error or nothing for it."""
    if not calendar or _objects(calendar.get("errors")):
        return FreeBusy(who=who, known=False)
    spans: list[TimeSpan] = []
    for entry in _objects(calendar.get("busy")):
        try:
            start = datetime.fromisoformat(str(entry.get("start"))).astimezone(UTC)
            end = datetime.fromisoformat(str(entry.get("end"))).astimezone(UTC)
        except ValueError:
            continue
        start, end = max(start, since), min(end, until)
        if start < end:
            spans.append(TimeSpan(start=start, end=end))
    return FreeBusy(who=who, busy=sorted(spans, key=lambda span: span.start))
