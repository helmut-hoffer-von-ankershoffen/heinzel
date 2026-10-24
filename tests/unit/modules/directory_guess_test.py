"""Guessing reporting lines from recurring 1:1s (`system.directory`, ADR 0024)."""

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

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
    every: int = 7,
    times: int = 4,
    organizer: Participant | None = None,
    title: str = "1:1",
    **extra: object,
) -> list[Event]:
    return [
        Event(
            id=f"{a.name}{b.name}{i}",
            title=title,
            start=START + timedelta(days=every * i),
            end=START + timedelta(days=every * i, minutes=30),
            organizer=organizer if organizer is not None else a,
            attendees=[a, b],
            **extra,  # type: ignore[arg-type]
        )
        for i in range(times)
    ]


def test_the_organizer_of_a_weekly_one_on_one_is_guessed_to_be_the_manager():
    lines, people = guess_lines(series(MIRA, JONAS, title="Mira / Jonas"), 3)
    (line,) = lines
    assert (line.manager, line.report, line.occurrences) == ("mira@example.com", "jonas@example.com", 4)
    assert line.evidence == '1:1 "Mira / Jonas" weekly, 4 times from 2026-09-01 to 2026-09-22, organized by Mira'
    assert set(people) == {"mira@example.com", "jonas@example.com"}


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


def test_every_two_weeks_reads_so_and_duplicates_count_once():
    events = series(ADA, MIRA, every=14, times=3)
    (line,) = guess_lines([*events, *events], 3)[0]
    assert "every two weeks, 3 times" in line.evidence


def test_a_person_gets_the_strongest_manager_guess_and_the_others_as_evidence():
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
    assert mira.manager.evidence[1] == "others organize recurring 1:1s with them too: Lea"
    assert [r.who.name for r in mira.reports] == ["Ben", "Jonas"]
    assert all(r.guess and r.evidence for r in mira.reports)
    assert person_from("nobody@example.com", lines, people) is None


def test_a_missing_display_name_is_never_replaced_by_an_address():
    anonymous = Participant(email="x@example.com")
    lines, _ = guess_lines(series(anonymous, JONAS), 3)
    assert "x@example.com" not in lines[0].evidence
    assert "organized by someone without a display name" in lines[0].evidence


class Agenda:
    def __init__(self, events: list[Event]) -> None:
        self.events_ = events
        self.windows: list[Window] = []

    def events(self, window: Window) -> list[Event]:
        self.windows.append(window)
        return self.events_


def test_the_adapter_reads_the_configured_lookback_and_answers_by_email_only():
    agenda = Agenda([*series(MIRA, JONAS), *series(ADA, MIRA)])
    adapter = OneOnOnes(agenda, DirectorySettings(lookback_days=30))
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
    assert window.until - window.since == timedelta(days=30)
    assert window.until.tzinfo is not None


@given(
    st.lists(
        st.tuples(
            st.sampled_from([MIRA, JONAS, ADA, LEA]), st.sampled_from([MIRA, JONAS, ADA, LEA]), st.integers(0, 60)
        ),
        max_size=40,
    )
)
def test_any_calendar_gives_lines_between_attendees_with_evidence_and_no_addresses(meetings):
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
    lines, people = guess_lines(events, 3)
    for line in lines:
        assert line.manager != line.report
        assert {line.manager, line.report} <= set(people)
        assert "@" not in line.evidence
