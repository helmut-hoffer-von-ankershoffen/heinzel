"""Small models of `heinzel.kernel._protocols`: merged reads, self answers and reporting lines."""

import pytest

from heinzel import Participant
from heinzel.kernel._protocols import Gathered, Relation, SelfAnswer, SourceFailure

pytestmark = pytest.mark.unit


def test_gathered_is_a_plain_list_that_also_names_who_failed() -> None:
    failure = SourceFailure(source="github", reason="rate limited")
    merged = Gathered([1, 2], [failure])
    assert merged == [1, 2]
    assert merged.failures == [failure]
    assert Gathered[int]().failures == []


def test_a_timed_out_answer_carries_nothing_else() -> None:
    assert SelfAnswer(timed_out=True).timed_out
    for extra in ({"text": "yes"}, {"option": "approve"}):
        with pytest.raises(ValueError, match="timed-out answer"):
            SelfAnswer(timed_out=True, **extra)


def test_an_answer_needs_text_or_an_option_and_knows_which_it_is() -> None:
    with pytest.raises(ValueError, match="text or an option"):
        SelfAnswer(text="   ")
    typed = SelfAnswer(text="later")
    chosen = SelfAnswer(text="Approve", option="approve")
    assert not typed.answered_by_option
    assert chosen.answered_by_option


def test_a_guessed_reporting_line_must_say_what_it_rests_on() -> None:
    ada = Participant(name="Ada")
    with pytest.raises(ValueError, match="evidence"):
        Relation(who=ada, guess=True)
    assert Relation(who=ada, guess=True, evidence=["a recurring 1:1 with Ada"]).guess
    assert not Relation(who=ada).guess


def test_evidence_never_names_people_by_address() -> None:
    with pytest.raises(ValueError, match="never by address"):
        Relation(who=Participant(name="Ada"), guess=True, evidence=["1:1 with ada@example.com"])
