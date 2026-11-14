"""A Heinzel's one-word state is computed by code from journal envelopes, by fixed precedence, never by the model."""

from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import get_args

import pytest

from heinzel import SessionEvent
from heinzel.modules.heinzel._activity import (
    MAX_TURN_SECONDS,
    PRECEDENCE,
    Activity,
    ActivityFacts,
    activity,
    open_write_drafts,
    recently,
    run_counts,
)
from heinzel.modules.heinzel._turn_activity import interactive_turn_id

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)


def _events(*specs: tuple[str, str | None, dict, datetime | None]) -> list[SessionEvent]:
    return [
        SessionEvent(seq=index, ts=at or NOW, prev=None, kind=kind, key=key, data=data)
        for index, (kind, key, data, at) in enumerate(specs, start=1)
    ]


def _accepted(message: str, at: datetime | None = None) -> tuple[str, str, dict, datetime | None]:
    return ("message.accepted", f"message-{message}", {"message_id": message}, at)


def _turn(kind: str, message: str, at: datetime | None = None) -> tuple[str, str, dict, datetime | None]:
    return (kind, interactive_turn_id(message), {}, at)


def test_the_precedence_is_the_approved_order_and_covers_all_nine_states():
    assert (*PRECEDENCE, "idle") == get_args(Activity)
    assert PRECEDENCE == (
        "paused",
        "stuck",
        "waiting_for_you",
        "overloaded",
        "working",
        "waiting_on_others",
        "proud",
        "asleep",
    )


@pytest.mark.parametrize("state", PRECEDENCE)
def test_each_state_alone_is_shown(state):
    assert activity(ActivityFacts(**{state: True})) == state


def test_nothing_known_is_idle():
    assert activity(ActivityFacts()) == "idle"


@pytest.mark.parametrize(("winner", "loser"), list(pairwise(PRECEDENCE)))
def test_each_adjacent_tie_goes_to_the_earlier_state(winner, loser):
    assert activity(ActivityFacts(**{winner: True, loser: True})) == winner


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        (ActivityFacts(paused=True, stuck=True), "paused"),
        (ActivityFacts(stuck=True, waiting_for_you=True), "stuck"),
        (ActivityFacts(waiting_for_you=True, working=True), "waiting_for_you"),
        (ActivityFacts(overloaded=True, working=True), "overloaded"),
        (ActivityFacts(**dict.fromkeys(PRECEDENCE, True)), "paused"),
    ],
)
def test_named_ties(facts, expected):
    assert activity(facts) == expected


def test_no_events_count_nothing():
    counts = run_counts([], NOW)
    assert (counts.runs_last_hour, counts.queued_runs, counts.running, counts.last_run_failed) == (0, 0, False, False)
    assert counts.last_approved_at is None
    assert open_write_drafts([]) == []


def test_a_started_unfinished_turn_is_running_and_counted_in_the_last_hour():
    counts = run_counts(_events(_accepted("a"), _turn("interactive.started", "a")), NOW)
    assert counts.running
    assert counts.runs_last_hour == 1
    assert counts.queued_runs == 0


def test_a_replied_turn_is_neither_running_nor_failed():
    counts = run_counts(_events(_accepted("a"), _turn("interactive.started", "a"), _turn("message.replied", "a")), NOW)
    assert not counts.running
    assert not counts.last_run_failed


def test_accepted_messages_without_a_start_are_queued():
    counts = run_counts(_events(_accepted("a"), _turn("interactive.started", "a"), _accepted("b"), _accepted("c")), NOW)
    assert counts.queued_runs == 2
    assert counts.running


def test_an_abandoned_unstarted_message_is_not_queued():
    assert run_counts(_events(_accepted("a"), _turn("interactive.abandoned", "a")), NOW).queued_runs == 0


def test_old_runs_are_not_in_the_last_hour():
    earlier = NOW - timedelta(hours=2)
    counts = run_counts(_events(_accepted("a", earlier), _turn("interactive.started", "a", earlier)), NOW)
    assert counts.runs_last_hour == 0


@pytest.mark.parametrize("failure", ["interactive.abandoned", "agent.schedule-failed"])
def test_the_latest_failed_outcome_marks_the_last_run_failed(failure):
    events = _events(
        _accepted("a"), _turn("interactive.started", "a"), _turn("message.replied", "a"), (failure, "x", {}, None)
    )
    assert run_counts(events, NOW).last_run_failed


def test_a_later_reply_clears_an_earlier_failure():
    events = _events(
        ("agent.schedule-failed", "schedule-failed-1", {}, None),
        _accepted("a"),
        _turn("interactive.started", "a"),
        _turn("message.replied", "a"),
    )
    assert not run_counts(events, NOW).last_run_failed


def test_the_latest_owner_approval_or_publication_is_reported():
    earlier = NOW - timedelta(hours=3)
    later = NOW - timedelta(minutes=10)
    events = _events(("approval.issued", "a", {}, earlier), ("draft.published", "b", {}, later))
    assert run_counts(events, NOW).last_approved_at == later


def test_malformed_message_identities_are_ignored_not_errors():
    events = _events(
        ("message.accepted", "message-x", {"message_id": 3}, None),
        ("message.accepted", "message-y", {}, None),
        ("message.accepted", "message-z", {"message_id": ""}, None),
    )
    assert run_counts(events, NOW).queued_runs == 0


def test_open_write_drafts_exclude_discarded_and_attempted():
    events = _events(
        ("write.drafted", "a", {}, None),
        ("write.drafted", "b", {}, None),
        ("write.drafted", "c", {}, None),
        ("write.discarded", "a", {}, None),
        ("write.attempted", "write-attempt-b", {}, None),
    )
    assert [event.key for event in open_write_drafts(events)] == ["c"]


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (None, False),
        (NOW, True),
        (NOW - timedelta(minutes=59), True),
        (NOW - timedelta(hours=1, seconds=1), False),
        (NOW + timedelta(minutes=1), False),
    ],
)
def test_recently_means_within_the_last_hour(moment, expected):
    assert recently(moment, NOW) is expected


def test_future_runs_are_not_in_the_last_hour():
    later = NOW + timedelta(minutes=5)
    counts = run_counts(_events(_accepted("a", later), _turn("interactive.started", "a", later)), NOW)
    assert counts.runs_last_hour == 0


@pytest.mark.parametrize(
    ("age", "running", "failed", "state"),
    [(MAX_TURN_SECONDS - 1, True, False, "working"), (MAX_TURN_SECONDS + 1, False, True, "stuck")],
)
def test_an_unfinished_turn_is_working_until_the_cap_then_stuck(age, running, failed, state):
    started = NOW - timedelta(seconds=age)
    counts = run_counts(_events(_accepted("a", started), _turn("interactive.started", "a", started)), NOW)
    assert (counts.running, counts.last_run_failed) == (running, failed)
    assert activity(ActivityFacts(stuck=counts.last_run_failed, working=counts.running)) == state
