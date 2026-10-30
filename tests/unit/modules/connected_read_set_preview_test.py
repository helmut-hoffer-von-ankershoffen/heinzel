"""A private set preview exposes no authority and never runs a provider read."""

import pytest

from heinzel.kernel._approval import ApprovalConfirmation
from heinzel.modules.heinzel._connected_read_set_preview import read_set_tool_preview
from tests.unit.modules.connected_read_set_approval_test import _prepare

pytestmark = pytest.mark.unit
pytest_plugins = ("tests.unit.modules.connected_grants_test",)


def _approved(connected):
    current, crud, session, grants, state, sets, reviews, review, approvals = _prepare(connected)
    proof = approvals.issue(session, "review", review.revision, "human-confirmation")
    payload = review.model_dump(mode="json")
    confirmation = ApprovalConfirmation()
    args = {"session_id": session, "request_id": "review", "review": payload, "approval_proof": proof}
    with confirmation.using("heinzel", "approve-connected-read-set", args):
        approvals.approve(session, "review", payload, proof, confirmation)
    return current, crud, session, grants, state, sets, reviews, review, approvals


def test_preview_requires_exact_approved_owner_set_and_never_executes(connected):
    current, crud, session, grants, state, _, _, review, approvals = _approved(connected)
    events = list(crud.repository.control_read(session).events)
    with crud.repository.main_writer(session) as writer:
        preview = read_set_tool_preview(approvals, session, writer)
        assert preview is not None
        schema = preview["schema"]
        assert schema == {
            "type": "object",
            "properties": {"handle": {"type": "string", "enum": [review.proposal.entries[0].handle]}},
            "required": ["handle"],
            "additionalProperties": False,
        }
        with current.local("daemon"):
            assert read_set_tool_preview(approvals, session, writer) is None
    assert state["reads"] == 0
    assert crud.repository.control_read(session).events == events
    assert grants.tool_schema(session) is None  # no legacy grant was implicitly created


@pytest.mark.parametrize(("field", "replacement"), [("account", "other-account"), ("source", "other-source")])
def test_preview_refuses_revoked_or_changed_source(connected, field, replacement):
    _, crud, session, _, state, _, _, _review, approvals = _approved(connected)
    original = state[field]
    with crud.repository.main_writer(session) as writer:
        state[field] = replacement
        assert read_set_tool_preview(approvals, session, writer) is None
    state[field] = original
    approvals.revoke(session, "review")
    with crud.repository.main_writer(session) as writer:
        assert read_set_tool_preview(approvals, session, writer) is None
    assert state["reads"] == 0


def test_preview_does_not_use_another_session_writer(connected):
    _, crud, session, _, state, _, _, _, approvals = _approved(connected)
    other = crud.new_chat("other").session_id
    with crud.repository.main_writer(other) as writer:
        assert read_set_tool_preview(approvals, session, writer) is None
    assert state["reads"] == 0


def test_unapproved_preview_does_not_open_provider_or_create_legacy_authority(connected):
    _, crud, session, grants, state, _, _, _review, approvals = _prepare(connected)
    before = state["opens"]
    with crud.repository.main_writer(session) as writer:
        assert read_set_tool_preview(approvals, session, writer) is None
    assert state["opens"] == before
    assert state["reads"] == 0
    assert grants.tool_schema(session) is None


def test_preview_rejects_foreign_principal_before_observing(connected):
    from heinzel import CallIdentity, Principal

    current, crud, session, _, state, _, _, _, approvals = _approved(connected)
    before = state["opens"]
    foreign = CallIdentity(Principal("u-" + "b" * 32), "api-bearer", "foreign", "private", frozenset({"private:read"}))
    with crud.repository.main_writer(session) as writer, current.using(foreign):
        assert read_set_tool_preview(approvals, session, writer) is None
    assert state["opens"] == before
    assert state["reads"] == 0
