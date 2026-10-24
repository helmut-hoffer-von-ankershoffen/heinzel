"""The Google Calendar adapter of the `calendar` capability (ADR 0028), read mode only.

Events come from Calendar API v3 through `gws calendar events list`, calendar `primary`, with `singleEvents=true`
(recurrences expanded) and `showDeleted=true` (cancelled occurrences included), paged by `nextPageToken`. People become
`Participant`s: a display name and an email, nothing else; meeting rooms and other resources are not attendees. The
email is the join key the protocol defines; the workflow packet builder drops it before anything reaches an agent's
prompt (ADR 0028), and nothing this plugin shows besides the capability carries it.

`conference_id` is in the meeting tool's notation, so a `Meeting` with the same id and an overlapping time is the
event's meeting: a Zoom meeting number, found in the conferencing data, the location or the description, else a Meet
code. Conference links lose their query and fragment, which hold passcodes.
"""

import re
from datetime import UTC, date, datetime, time
from typing import Any, cast
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from heinzel import Event, HeinzelError, Participant, Window, clean_text
from heinzel_google._gws import GoogleSession

EVENTS = ("calendar", "events", "list")
_ZOOM = re.compile(r"https://(?:[a-z0-9-]+\.)?zoom\.us/(?:j|my|w)/([0-9]{9,11})\b", re.IGNORECASE)
_MEET = re.compile(r"^[a-z]{3}-[a-z]{4}-[a-z]{3}$")
_EVENT_LINK = ("https://www.google.com/calendar/", "https://calendar.google.com/")
_CONFERENCE_HOSTS = frozenset({"meet.google.com", "zoom.us", "teams.microsoft.com", "teams.live.com"})
_CONFERENCE_DOMAINS = (".zoom.us", ".webex.com")
_HOST = re.compile(r"^[a-z0-9.-]+$")
MAX_TEXT = 100_000
"""Characters of a location or description searched for a Zoom link."""

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
