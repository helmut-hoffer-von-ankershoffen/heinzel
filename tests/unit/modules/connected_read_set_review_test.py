"""A read-set review re-observes exact providers without issuing approval or reading data."""

import pytest

from heinzel import ConnectedReadReview, HeinzelError
from heinzel.kernel._approval import arguments_hash
from heinzel.modules.heinzel._capability_review import describe_capability_read
from heinzel.modules.heinzel._connected_grants import RollingWindowPolicy
from heinzel.modules.heinzel._connected_read_set_reviews import ConnectedReadSetReviews
from heinzel.modules.heinzel._connected_read_sets import ConnectedReadSets, ReadSetTerms

pytestmark = pytest.mark.unit
pytest_plugins = ("tests.unit.modules.connected_grants_test",)


def _terms(current, session, grants, fixed, state):
    descriptor = describe_capability_read(grants.kernel, "calendar", "events")
    review = ConnectedReadReview(
        owner=current.principal().id,
        session=session,
        operation="capability-method:calendar.events",
        schema_sha256=arguments_hash(descriptor.schema),
        source_revision=state["source"],
        account_identity=state["account"],
        provider_scopes=state["scopes"],
        fixed_arguments=fixed,
    )
    return ReadSetTerms(capability="calendar", method="events", binding_sha256=descriptor.binding_sha256, review=review)


def test_exact_host_review_is_replayed_without_approval_or_read(connected):
    current, crud, session, grants, fixed, state = connected
    sets = ConnectedReadSets(crud, current)
    second = {"window": {"since": "2026-01-03T00:00:00Z", "until": "2026-01-04T00:00:00Z"}}
    proposal = sets.propose(
        session,
        "set",
        (_terms(current, session, grants, fixed, state), _terms(current, session, grants, second, state)),
    )
    reviewer = ConnectedReadSetReviews(sets, grants)
    result = reviewer.prepare(session, "review", "set", proposal.revision)
    assert result.proposal == proposal
    assert result.provenance == "host-observed"
    result.proposal.entries[0].terms.review.fixed_arguments["window"] = {"since": "tampered"}
    replay = reviewer.prepare(session, "review", "set", proposal.revision)
    assert replay.proposal == proposal
    assert state["opens"] == 8
    assert state["reads"] == 0
    assert not any(event.kind == "connected.approved" for event in crud.repository.control_read(session).events)
    assert grants.tool_schema(session) is None


def test_switched_account_or_changed_set_denies_review_and_replay(connected):
    current, crud, session, grants, fixed, state = connected
    sets = ConnectedReadSets(crud, current)
    first = sets.propose(session, "first", (_terms(current, session, grants, fixed, state),))
    reviewer = ConnectedReadSetReviews(sets, grants)
    reviewer.prepare(session, "review", "first", first.revision)
    state["account"] = "different-account"
    with pytest.raises(HeinzelError, match="source or account changed"):
        reviewer.prepare(session, "review", "first", first.revision)
    state["account"] = "account-a"
    second = sets.propose(session, "second", (_terms(current, session, grants, fixed, state),))
    with pytest.raises(HeinzelError, match="proposal changed"):
        reviewer.prepare(session, "review", "first", first.revision)
    with pytest.raises(HeinzelError, match="different read-set review"):
        reviewer.prepare(session, "review", "second", second.revision)
    assert state["reads"] == 0
    assert sum(event.kind == "connected-set.reviewed" for event in crud.repository.control_read(session).events) == 1


def test_account_switch_mid_review_rejects_entire_set(connected, monkeypatch):
    current, crud, session, grants, fixed, state = connected
    sets = ConnectedReadSets(crud, current)
    proposal = sets.propose(session, "set", (_terms(current, session, grants, fixed, state),))
    observe = grants.verify_proposed_terms

    def change_after_observation(*args):
        observe(*args)
        state["account"] = "different-account"

    monkeypatch.setattr(grants, "verify_proposed_terms", change_after_observation)
    with pytest.raises(HeinzelError, match="source or account changed"):
        ConnectedReadSetReviews(sets, grants).prepare(session, "review", "set", proposal.revision)
    assert state["reads"] == 0
    assert not any(event.kind == "connected-set.reviewed" for event in crud.repository.control_read(session).events)


def test_unverified_claims_cannot_become_host_observed_review(connected):
    current, crud, session, grants, fixed, state = connected
    sets = ConnectedReadSets(crud, current)
    terms = _terms(current, session, grants, fixed, state)
    forged = terms.model_copy(update={"review": terms.review.model_copy(update={"account_identity": "other"})})
    proposal = sets.propose(session, "set", (forged,))
    reviewer = ConnectedReadSetReviews(sets, grants)
    with pytest.raises(HeinzelError, match="source or account changed"):
        reviewer.prepare(session, "review", "set", proposal.revision)
    invalid_revision = True
    with pytest.raises(HeinzelError, match="revision is invalid"):
        reviewer.prepare(session, "bad", "set", invalid_revision)
    assert not any(event.kind == "connected-set.reviewed" for event in crud.repository.control_read(session).events)
    assert state["reads"] == 0


def test_rolling_window_is_reobserved_under_explicit_policy(connected):
    current, crud, session, grants, fixed, state = connected
    sets = ConnectedReadSets(crud, current)
    terms = _terms(current, session, grants, fixed, state).model_copy(
        update={
            "window_policy": RollingWindowPolicy(
                parameter="window", lookback_seconds=60, lookahead_seconds=60, timezone="UTC"
            )
        }
    )
    proposal = sets.propose(session, "rolling", (terms,))
    review = ConnectedReadSetReviews(sets, grants).prepare(session, "review", "rolling", proposal.revision)
    assert review.proposal.entries[0].terms.window_policy == terms.window_policy
    assert state["opens"] == 2
    assert state["reads"] == 0


def test_source_change_denies_host_review(connected):
    current, crud, session, grants, fixed, state = connected
    sets = ConnectedReadSets(crud, current)
    proposal = sets.propose(session, "set", (_terms(current, session, grants, fixed, state),))
    state["source"] = "different-source"
    with pytest.raises(HeinzelError, match="source or account changed"):
        ConnectedReadSetReviews(sets, grants).prepare(session, "review", "set", proposal.revision)
    assert state["reads"] == 0
    assert not any(event.kind == "connected-set.reviewed" for event in crud.repository.control_read(session).events)


def test_exact_set_intent_does_not_issue_or_grant_and_rechecks_sources(connected):
    current, crud, session, grants, fixed, state = connected
    sets = ConnectedReadSets(crud, current)
    proposal = sets.propose(session, "set", (_terms(current, session, grants, fixed, state),))
    reviewer = ConnectedReadSetReviews(sets, grants)
    review = reviewer.prepare(session, "review", "set", proposal.revision)
    before = list(crud.repository.control_read(session).events)
    intent = reviewer.approval_intent(session, "review", review.revision)
    assert intent.owner == current.principal().id
    assert intent.session == session
    assert intent.audience == "personal-connected-read-set"
    assert intent.action == "approve-connected-read-set"
    assert intent.resource == review.request_id
    assert intent.grant_revision == review.revision
    assert intent.arguments_hash == arguments_hash(review.model_dump(mode="json"))
    assert crud.repository.control_read(session).events == before
    assert state["reads"] == 0
    assert grants.tool_schema(session) is None
    assert not hasattr(reviewer, "issue")
    state["account"] = "different-account"
    with pytest.raises(HeinzelError, match="source or account changed"):
        reviewer.approval_intent(session, "review", review.revision)


def test_intent_rejects_stale_review_and_different_revision(connected):
    current, crud, session, grants, fixed, state = connected
    sets = ConnectedReadSets(crud, current)
    first = sets.propose(session, "first", (_terms(current, session, grants, fixed, state),))
    reviewer = ConnectedReadSetReviews(sets, grants)
    review = reviewer.prepare(session, "old-review", "first", first.revision)
    original = reviewer.approval_intent(session, "old-review", review.revision)
    with pytest.raises(HeinzelError, match="review changed"):
        reviewer.approval_intent(session, "old-review", review.revision + 1)
    second = sets.propose(session, "second", (_terms(current, session, grants, fixed, state),))
    with pytest.raises(HeinzelError, match="review changed"):
        reviewer.approval_intent(session, "old-review", review.revision)
    newer = reviewer.prepare(session, "new-review", "second", second.revision)
    changed = reviewer.approval_intent(session, "new-review", newer.revision)
    assert changed.arguments_hash != original.arguments_hash
    with pytest.raises(HeinzelError, match="review changed"):
        reviewer.approval_intent(session, "old-review", review.revision)
    assert state["reads"] == 0


def test_intent_requires_authenticated_owner_and_original_session(connected):
    current, crud, session, grants, fixed, state = connected
    sets = ConnectedReadSets(crud, current)
    proposal = sets.propose(session, "set", (_terms(current, session, grants, fixed, state),))
    reviewer = ConnectedReadSetReviews(sets, grants)
    review = reviewer.prepare(session, "review", "set", proposal.revision)
    other = crud.new_chat("another").session_id
    with pytest.raises(HeinzelError, match="review changed"):
        reviewer.approval_intent(other, "review", review.revision)
    with current.local("daemon"), pytest.raises(HeinzelError, match="authenticated owner"):
        reviewer.approval_intent(session, "review", review.revision)
    assert state["reads"] == 0
