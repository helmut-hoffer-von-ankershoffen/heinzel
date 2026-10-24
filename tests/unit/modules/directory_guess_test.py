"""Guessing reporting lines from recurring 1:1s (`system.directory`, ADR 0024)."""

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from heinzel import Event, Participant, Window
from heinzel.modules.system.directory import DirectorySettings, OneOnOnes
from heinzel.modules.system.directory._guess import guess_lines, person_from

pytestmark = pytest.mark.unit

START = datetime(2026, 9, 1, 10, tzinfo=UTC)
MIRA = Participant(name="Mira", email="mira@example.com")
JONAS = Participant(name="Jonas", email="jonas@example.com")
ADA = Participant(name="Ada", email="ada@example.com")
LEA = Participant(name="Lea", email="lea@example.com")


def series(  # noqa: PLR0913 - one knob per signal
    a: Participant,
    b: Participant,
    *,
    every: float = 7,
    times: int = 4,
    organizer: Participant | None = None,
    title: str = "1:1",
    **extra: object,
) -> list[Event]:
    return at(a, b, [every * i for i in range(times)], organizer=organizer, title=title, **extra)


def at(
    a: Participant,
    b: Participant,
    days: list[float],
    *,
    organizer: Participant | None = None,
    title: str = "1:1",
    **extra: object,
) -> list[Event]:
    return [
        Event(
            id=f"{a.name}{b.name}{i}",
            title=title,
            start=START + timedelta(days=day),
            end=START + timedelta(days=day, minutes=30),
            organizer=organizer if organizer is not None else a,
            attendees=[a, b],
            **extra,  # type: ignore[arg-type]
        )
        for i, day in enumerate(days)
    ]


def test_the_organizer_of_a_weekly_one_on_one_is_guessed_to_be_the_manager():
    lines, people = guess_lines(series(MIRA, JONAS, title="Mira / Jonas"), 3)
    (line,) = lines
    assert (line.manager, line.report, line.occurrences) == ("mira@example.com", "jonas@example.com", 4)
    assert line.evidence == "a recurring 1:1 weekly in your calendar, organized by Mira"
    assert set(people) == {"mira@example.com", "jonas@example.com"}


@pytest.mark.parametrize(
    ("every", "cadence"),
    [(4.9, None), (5, "weekly"), (9.9, "weekly"), (10, "every two weeks"), (16, "every two weeks"), (16.1, None)],
)
def test_weekly_and_every_two_weeks_have_exact_bounds(every, cadence):
    lines, _ = guess_lines(series(MIRA, JONAS, every=every), 3)
    if cadence is None:
        assert lines == []
    else:
        assert lines[0].evidence == f"a recurring 1:1 {cadence} in your calendar, organized by Mira"


def test_the_cadence_is_the_median_gap_so_one_long_break_doesnt_hide_a_weekly_one_on_one():
    (line,) = guess_lines(at(MIRA, JONAS, [0, 7, 14, 21, 81]), 3)[0]  # gaps 7, 7, 7, 60: mean 20, median 7
    assert "weekly" in line.evidence


@pytest.mark.parametrize(
    ("events", "why"),
    [
        (series(MIRA, JONAS, times=2), "too few"),
        (series(MIRA, JONAS, every=1), "daily is a stand-up, not a 1:1"),
        (series(MIRA, JONAS, every=30), "monthly"),
        (series(MIRA, JONAS, organizer=ADA), "organized by someone else"),
        (series(MIRA, JONAS, organizer=Participant(name="Mira")), "organizer without an address"),
        (series(MIRA, JONAS, all_day=True), "all-day"),
        (series(MIRA, JONAS, status="cancelled"), "cancelled"),
        (series(MIRA, Participant(name="Room")), "an attendee without an address"),
        (series(MIRA, Participant(name="Mira again", email="MIRA@example.com")), "the same person twice"),
    ],
)
def test_no_line_is_guessed_without_a_clear_signal(events, why):
    assert guess_lines(events, 3)[0] == [], why


def test_three_attendees_are_a_meeting_not_a_one_on_one():
    events = [e.model_copy(update={"attendees": [MIRA, JONAS, ADA]}) for e in series(MIRA, JONAS)]
    assert guess_lines(events, 3)[0] == []


def test_the_more_frequent_organizer_of_a_pair_is_the_guess():
    events = [*series(MIRA, JONAS, times=6), *series(MIRA, JONAS, times=2, organizer=JONAS)]
    events = [e.model_copy(update={"id": str(i), "start": START + timedelta(days=7 * i)}) for i, e in enumerate(events)]
    (line,) = guess_lines(events, 3)[0]
    assert line.manager == "mira@example.com"


def test_duplicates_count_once():
    events = series(ADA, MIRA, every=14, times=3)
    (line,) = guess_lines([*events, *events], 3)[0]
    assert line.occurrences == 3
    assert guess_lines([*series(ADA, MIRA, times=2), *series(ADA, MIRA, times=2)], 3)[0] == []


def test_a_person_gets_the_strongest_manager_guess_and_the_others_only_as_a_hint():
    events = [
        *series(ADA, MIRA, times=6),
        *series(LEA, MIRA, every=14, times=3),
        *series(MIRA, JONAS),
        *series(MIRA, Participant(name="Ben", email="ben@example.com")),
    ]
    lines, people = guess_lines(events, 3)
    mira = person_from("mira@example.com", lines, people)
    assert mira is not None
    assert mira.manager is not None
    assert mira.manager.who == ADA
    assert mira.manager.guess
    assert mira.manager.evidence == [
        "a recurring 1:1 weekly in your calendar, organized by Ada",
        "other people organize recurring 1:1s with them too",
    ]
    assert [r.who.name for r in mira.reports] == ["Ben", "Jonas"]
    assert all(r.guess and r.evidence for r in mira.reports)
    assert person_from("nobody@example.com", lines, people) is None


@pytest.mark.parametrize("name", [None, "", "  ", "x@example.com"])
def test_evidence_never_carries_an_address_or_a_title(name):
    organizer = Participant(name=name, email="x@example.com")
    (line,) = guess_lines(series(organizer, JONAS, title="x@example.com / Jonas: performance plan"), 3)[0]
    assert line.evidence == "a recurring 1:1 weekly in your calendar, organized by someone without a display name"


def test_evidence_with_an_address_is_refused():
    from heinzel import Relation

    with pytest.raises(ValidationError, match="never by address"):
        Relation(who=ADA, guess=True, evidence=["organized by ada@example.com"])


class Agenda:
    def __init__(self, events: list[Event]) -> None:
        self.events_ = events
        self.windows: list[Window] = []

    def events(self, window: Window) -> list[Event]:
        self.windows.append(window)
        return self.events_


@pytest.mark.parametrize(("settings", "days"), [(DirectorySettings(), 56), (DirectorySettings(lookback_days=90), 90)])
def test_the_adapter_reads_the_configured_lookback_and_answers_by_email_only(settings, days):
    agenda = Agenda([*series(MIRA, JONAS), *series(ADA, MIRA)])
    adapter = OneOnOnes(agenda, settings)
    found = adapter.person(" Mira@Example.com ")
    assert found is not None
    assert found.who == MIRA
    assert found.login is None
    assert adapter.person("mira") is None  # a calendar has no logins
    assert [p.who for p in adapter.reports("mira@example.com")] == [JONAS]
    (jonas,) = adapter.reports("mira@example.com")
    assert jonas.manager is not None
    assert jonas.manager.who == MIRA
    assert adapter.reports("jonas@example.com") == []
    window = agenda.windows[0]
    assert window.until - window.since == timedelta(days=days)
    assert window.until.tzinfo is not None


def test_reports_are_sorted_by_name():
    events = [*series(MIRA, LEA), *series(MIRA, JONAS), *series(MIRA, ADA)]
    assert [p.who.name for p in OneOnOnes(Agenda(events), DirectorySettings()).reports("mira@example.com")] == [
        "Ada",
        "Jonas",
        "Lea",
    ]


@pytest.mark.parametrize("value", [{"lookback_days": 41}, {"lookback_days": 366}, {"min_occurrences": 1}])
def test_settings_keep_a_fortnightly_one_on_one_findable(value):
    with pytest.raises(ValidationError):
        DirectorySettings(**value)


PEOPLE = st.sampled_from([MIRA, JONAS, ADA, LEA])


@given(st.lists(st.tuples(PEOPLE, PEOPLE, st.integers(0, 60)), max_size=40), st.integers(2, 5))
def test_any_calendar_gives_lines_between_attendees_with_evidence_and_no_addresses(meetings, minimum):
    events = [
        Event(
            id=str(i),
            title="t",
            start=START + timedelta(days=day),
            end=START + timedelta(days=day, minutes=30),
            organizer=a,
            attendees=[a, b],
        )
        for i, (a, b, day) in enumerate(meetings)
    ]
    lines, people = guess_lines(events, minimum)
    for line in lines:
        assert line.manager != line.report
        assert {line.manager, line.report} <= set(people)
        assert line.occurrences >= minimum
        assert line.evidence
        assert "@" not in line.evidence
