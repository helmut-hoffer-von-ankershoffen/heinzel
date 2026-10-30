"""Read-set calls retain the host's captured-provider continuity boundary."""

import pytest

from heinzel import HeinzelError
from heinzel.kernel._approval import ApprovalConfirmation
from heinzel.modules.heinzel._connected_grants import ConnectedReadGrants
from heinzel.modules.heinzel._connected_read_set_approvals import ConnectedReadSetApprovals
from heinzel.modules.heinzel._connected_read_set_reviews import ConnectedReadSetReviews
from heinzel.modules.heinzel._connected_read_sets import ConnectedReadSets, ReadSetTerms
from tests.unit.modules.connected_read_set_input_test import _turn

pytestmark = pytest.mark.integration
pytest_plugins = ("tests.integration.source_discovery_prototype_test",)


def test_captured_source_change_during_set_read_discards_result_and_receipt(bound_source):
    _client, kernel, current, session, plugin, _source, _sources = bound_source
    fixed = {"window": {"since": "2026-01-01T00:00:00Z", "until": "2026-01-02T00:00:00Z"}}
    with current.local("cli"):
        grants = kernel.resolve(ConnectedReadGrants)
        observed = grants.propose(session, "legacy-unapproved", "calendar", "events", fixed)
        terms = ReadSetTerms(
            capability=observed.capability,
            method=observed.method,
            binding_sha256=observed.binding_sha256,
            review=observed.review,
        )
        sets = ConnectedReadSets(grants.crud, current)
        proposal = sets.propose(session, "set", (terms,))
        reviews = ConnectedReadSetReviews(sets, grants)
        review = reviews.prepare(session, "review", "set", proposal.revision)
        approvals = ConnectedReadSetApprovals(reviews)
        proof = approvals.issue(session, "review", review.revision, "human-confirmation")
        payload = review.model_dump(mode="json")
        confirmation = ApprovalConfirmation()
        args = {"session_id": session, "request_id": "review", "review": payload, "approval_proof": proof}
        with confirmation.using("heinzel", "approve-connected-read-set", args):
            approvals.approve(session, "review", payload, proof, confirmation)
        call = _turn(grants.crud, session, proposal.entries[0].handle)
        plugin.mutate_read = True
        with grants.crud.repository.main_writer(session) as writer, pytest.raises(HeinzelError):
            grants.read_set_entry(session, writer, call, approvals)
        assert plugin.reads == [True]
        assert not any(
            event.kind == "connected-set-input.observed"
            for event in grants.crud.repository.control_read(session).events
        )
