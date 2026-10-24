"""The directory models and the setup seam `draft_reporting_lines` (`heinzel.kernel._directory`, ADR 0024)."""

import pytest
from pydantic import ValidationError

from heinzel import ConfirmedLines, HeinzelError, Participant, Person, Relation, draft_reporting_lines

pytestmark = pytest.mark.unit

MIRA = Participant(name="Mira", email="mira@example.com")
ADA = Participant(name="Ada", email="ada@example.com")
JONAS = Participant(name="Jonas", email="jonas@example.com")
GUESS = ["weekly 1:1"]


class Fake:
    """A directory for tests that knows a fixed set of people, or fails."""

    def __init__(self, *people: Person, fail: bool = False) -> None:
        self.people = {p.who.email: p for p in people}
        self.fail = fail

    def person(self, person: str) -> Person | None:
        if self.fail:
            msg = "directory needs calendar:read: no adapter bound"
            raise HeinzelError(msg)
        return self.people.get(person)

    def reports(self, person: str) -> list[Person]:
        return []


def test_a_guess_must_carry_evidence():
    with pytest.raises(ValidationError, match="evidence"):
        Relation(who=ADA, guess=True)
    assert Relation(who=ADA).evidence == []


def test_guesses_are_passed_on_marked():
    mira = Person(who=MIRA, manager=Relation(who=ADA, guess=True, evidence=GUESS))
    draft = draft_reporting_lines(Fake(mira), "mira@example.com")
    assert draft.found == mira
    assert draft.manager == mira.manager
    assert draft.needs_confirmation
    assert draft.model_dump()["needs_confirmation"] is True


def test_what_the_user_confirmed_beats_a_guess_per_attribute():
    mira = Person(
        who=MIRA,
        manager=Relation(who=ADA, guess=True, evidence=GUESS),
        reports=[Relation(who=ADA, guess=True, evidence=GUESS)],
    )
    jonas = Person(who=JONAS, login="jonas")
    draft = draft_reporting_lines(Fake(mira, jonas), "mira@example.com", ConfirmedLines(manager="jonas@example.com"))
    assert draft.manager == Relation(who=JONAS, login="jonas")
    assert draft.reports == mira.reports  # not confirmed: still the guess
    draft = draft_reporting_lines(Fake(mira), "mira@example.com", ConfirmedLines(reports=[]))
    assert draft.reports == []
    assert draft.needs_confirmation  # the manager is still a guess


def test_a_directory_of_record_beats_what_the_user_confirmed():
    mira = Person(who=MIRA, manager=Relation(who=ADA), reports=[Relation(who=JONAS)])
    confirmed = ConfirmedLines(manager="someone@example.com", reports=["someone@example.com"])
    draft = draft_reporting_lines(Fake(mira), "mira@example.com", confirmed)
    assert draft.manager == Relation(who=ADA)
    assert draft.reports == [Relation(who=JONAS)]
    assert not draft.needs_confirmation


def test_confirmed_people_the_directory_does_not_know_keep_their_login_or_email():
    confirmed = ConfirmedLines(manager="ada", reports=["jonas@example.com"])
    draft = draft_reporting_lines(Fake(), "mira@example.com", confirmed)
    assert draft.found is None
    assert draft.manager == Relation(who=Participant(), login="ada")
    assert draft.reports == [Relation(who=Participant(email="jonas@example.com"))]
    assert not draft.needs_confirmation


def test_a_failing_directory_leaves_the_confirmed_lines_and_says_why():
    draft = draft_reporting_lines(Fake(fail=True), "mira@example.com", ConfirmedLines(manager="ada@example.com"))
    assert draft.problem == "directory needs calendar:read: no adapter bound"
    assert draft.manager == Relation(who=Participant(email="ada@example.com"))
    assert draft.reports == []
    assert draft_reporting_lines(Fake(fail=True), "mira").manager is None


def test_a_lookup_that_fails_for_a_confirmed_person_still_names_them():
    class Flaky(Fake):
        def person(self, person: str) -> Person | None:
            if person != "mira@example.com":
                msg = "x"
                raise HeinzelError(msg)
            return Person(who=MIRA)

    draft = draft_reporting_lines(Flaky(), "mira@example.com", ConfirmedLines(manager="ada"))
    assert draft.manager == Relation(who=Participant(), login="ada")
