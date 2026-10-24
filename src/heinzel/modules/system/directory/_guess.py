"""Guessing reporting lines from recurring 1:1s, and the `one-on-ones` adapter that serves them as a directory.

A recurring 1:1 is a series of events with exactly two attendees, the same two, at least `min_occurrences` times,
weekly or every two weeks (the median gap between occurrences is 5 to 16 days). Its organizer is guessed to be the
manager and the other attendee the report: a guess the user confirms or corrects in setup, never a fact. A series
without an organizer, or organized by someone else, says nothing about who reports to whom and is left out.

Evidence names people by display name, never by address, so it may reach a prompt (ADR 0024, ADR 0028).
"""

import itertools
import statistics
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from heinzel import Calendar, Event, Participant, Person, Relation, Window, clean_text
from heinzel.modules.system.directory._models import DirectorySettings

WEEKLY_DAYS = (5.0, 16.0)
"""The median gap, in days, of a series that counts as weekly or every two weeks."""
BIWEEKLY_FROM = 10.0
"""A median gap from this many days on reads as every two weeks."""


@dataclass(frozen=True, slots=True)
class Line:
    """A guessed reporting line: who organizes a recurring 1:1 with whom, how often, and why hz thinks so."""

    manager: str
    """The organizer's email, case-folded."""
    report: str
    """The other attendee's email, case-folded."""
    occurrences: int
    gap_days: float
    evidence: str


def _key(participant: Participant) -> str | None:
    return participant.email.strip().casefold() if participant.email and participant.email.strip() else None


def _name(participant: Participant | None) -> str:
    return participant.name if participant and participant.name else "someone without a display name"


def guess_lines(events: Iterable[Event], min_occurrences: int) -> tuple[list[Line], dict[str, Participant]]:
    """Reporting lines guessed from recurring 1:1s in `events`, and every attendee of those events by email."""
    series: dict[frozenset[str], list[Event]] = {}
    people: dict[str, Participant] = {}
    for event in events:
        if event.all_day or event.status.casefold() == "cancelled" or len(event.attendees) != 2:  # noqa: PLR2004
            continue
        keys = [_key(attendee) for attendee in event.attendees]
        if None in keys or keys[0] == keys[1]:
            continue
        for key, attendee in zip(keys, event.attendees, strict=True):
            if key is not None and (key not in people or (attendee.name and not people[key].name)):
                people[key] = attendee
        series.setdefault(frozenset(k for k in keys if k is not None), []).append(event)
    lines = [line for pair, found in series.items() if (line := _line(pair, found, people, min_occurrences))]
    return sorted(lines, key=lambda line: (line.manager, line.report)), people


def _line(pair: frozenset[str], events: list[Event], people: dict[str, Participant], minimum: int) -> Line | None:
    starts = sorted({event.start for event in events})
    if len(starts) < minimum:
        return None
    gap = statistics.median((b - a).total_seconds() / 86400 for a, b in itertools.pairwise(starts))
    if not WEEKLY_DAYS[0] <= gap <= WEEKLY_DAYS[1]:
        return None
    organizers = Counter(key for event in events if event.organizer and (key := _key(event.organizer)))
    if not organizers:
        return None
    organizer = organizers.most_common(1)[0][0]
    if organizer not in pair:
        return None
    (report,) = pair - {organizer}
    title = Counter(event.title for event in events).most_common(1)[0][0]
    cadence = "every two weeks" if gap >= BIWEEKLY_FROM else "weekly"
    evidence = (
        f'1:1 "{title}" {cadence}, {len(starts)} times from {starts[0]:%Y-%m-%d} to {starts[-1]:%Y-%m-%d}, '
        f"organized by {_name(people.get(organizer))}"
    )
    return Line(organizer, report, len(starts), gap, clean_text(evidence))


def person_from(key: str, lines: list[Line], people: dict[str, Participant]) -> Person | None:
    """The person with email `key` as the guesses see them, or None if no 1:1 names them."""
    if key not in people:
        return None
    managers = sorted(
        (line for line in lines if line.report == key), key=lambda line: (-line.occurrences, line.gap_days)
    )
    manager = None
    if managers:
        first, *others = managers
        evidence = [first.evidence]
        if others:
            names = ", ".join(_name(people.get(line.manager)) for line in others)
            evidence.append(f"others organize recurring 1:1s with them too: {names}")
        manager = Relation(who=people[first.manager], guess=True, evidence=evidence)
    reports = [
        Relation(who=people[line.report], guess=True, evidence=[line.evidence]) for line in lines if line.manager == key
    ]
    return Person(who=people[key], manager=manager, reports=sorted(reports, key=lambda r: _name(r.who).casefold()))


class OneOnOnes:
    """The core's `directory` adapter: reporting lines guessed from recurring 1:1s in the bound calendar.

    Knows people only by email (a calendar has no logins) and only those in the user's own 1:1s, keeps nothing
    between calls, and marks every line as a guess with its evidence.
    """

    def __init__(self, calendar: Calendar, settings: DirectorySettings) -> None:
        """Read through the bound calendar, injected; `settings` say how far back and how often."""
        self._calendar = calendar
        self._settings = settings

    def _read(self) -> tuple[list[Line], dict[str, Participant]]:
        until = datetime.now(UTC)
        window = Window(since=until - timedelta(days=self._settings.lookback_days), until=until)
        return guess_lines(self._calendar.events(window), self._settings.min_occurrences)

    def person(self, person: str) -> Person | None:
        """The person whose email is `person`, with guessed manager and reports; None for a login or a stranger."""
        lines, people = self._read()
        return person_from(person.strip().casefold(), lines, people)

    def reports(self, person: str) -> list[Person]:
        """The guessed direct reports of the person whose email is `person`, by name."""
        lines, people = self._read()
        key = person.strip().casefold()
        found = [person_from(line.report, lines, people) for line in lines if line.manager == key]
        return sorted((p for p in found if p is not None), key=lambda p: _name(p.who).casefold())
