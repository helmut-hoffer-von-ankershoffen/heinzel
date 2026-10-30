"""Notification drafts stay owner-private, replay-safe and inert."""

import json
from typing import override

import pytest
from pydantic import ValidationError

from heinzel import CallIdentity, CurrentUser, HeinzelError, Principal
from heinzel.modules.heinzel._notification_proposals import NotificationProposal, NotificationProposals
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository
from heinzel.modules.heinzel._user_proposal_tools import UserProposalTools

pytestmark = pytest.mark.unit


def test_owner_bound_proposal_exact_replay_and_no_automatic_notice(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = crud.new_chat("create", name="Agent").session_id
        owner = current.principal().id
        with repo.main_writer(session) as writer:
            tools = UserProposalTools(crud, session, "turn", owner, writer, "Please draft a notification", lambda: True)
            args = {"topic": "example", "title": "Draft title", "body": "Private message"}
            first = json.loads(tools.dispatch("notification:propose", args, 1))
            assert first == json.loads(tools.dispatch("notification:propose", args, 1))
            assert "Private message" not in json.dumps(first)
            with pytest.raises(HeinzelError):
                tools.dispatch("notification:propose", {**args, "body": "changed"}, 1)
            with pytest.raises(HeinzelError):
                tools.dispatch("notification:propose", {**args, "route": "https://example.invalid"}, 2)
        proposal = NotificationProposals(crud).get(session, first["request_id"], current)
        assert (proposal.title, proposal.body, proposal.status) == ("Draft title", "Private message", "proposed")
        assert len([event for event in repo.control_read(session).events if event.kind == "notification.proposed"]) == 1
        stranger = CallIdentity(Principal("u-" + "a" * 32), "api-bearer", "other", "private", frozenset())
        with current.using(stranger), pytest.raises(HeinzelError):
            NotificationProposals(crud).get(session, first["request_id"], current)
        with current.local("mcp-stdio"), pytest.raises(HeinzelError):
            NotificationProposals(crud).get(session, first["request_id"], current)


def test_notification_bounds_and_closed_schema():
    base = {
        "session_id": "hz-" + "0" * 26,
        "request_id": "request",
        "topic": "example",
        "title": "Title",
        "body": "Body",
    }
    for extra in (
        {"topic": "bad topic"},
        {"title": ""},
        {"title": "x" * 121},
        {"body": "x" * 501},
        {"status": "approved"},
        {"urgent": True},
    ):
        with pytest.raises(ValidationError):
            NotificationProposal.model_validate({**base, **extra})


def test_one_exact_human_confirmation_records_once_and_never_replays(tmp_path, monkeypatch):
    from heinzel import Notifications
    from heinzel.kernel._approval import ApprovalConfirmation
    from heinzel.modules.heinzel._notification_delivery import NotificationDelivery

    class FakeNotifications(Notifications):
        def __init__(self):
            self.calls = []
            self.keys = set()

        @override
        def emit_once(self, kind, title, body, *, route, command, key):
            self.calls.append((title, body, route, command, key))
            self.keys.add(key)
            return True

        def has_once(self, key):
            return key in self.keys

    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = crud.new_chat("create", name="Agent").session_id
        owner = current.principal().id
        proposal = NotificationProposal(
            session_id=session, request_id="notice-1", topic="example", title="Hi", body="Private"
        )
        with repo.main_writer(session) as writer:
            NotificationProposals(crud).propose(proposal, owner, writer)
        notices = FakeNotifications()
        delivery = NotificationDelivery(crud, current, notices)
        confirmation = ApprovalConfirmation()
        proof = delivery.issue(session, proposal.request_id, "test-owner")
        review = proposal.model_dump(mode="json")
        args = {"session_id": session, "request_id": proposal.request_id, "review": review, "approval_proof": proof}
        with pytest.raises(HeinzelError):
            delivery.confirm(session, proposal.request_id, review, proof, confirmation)
        changed = {**review, "body": "different"}
        changed_args = {**args, "review": changed}
        with confirmation.using("heinzel", "queue-agent-notification", changed_args), pytest.raises(HeinzelError):
            delivery.confirm(session, proposal.request_id, changed, proof, confirmation)
        assert notices.calls == []
        with confirmation.using("heinzel", "queue-agent-notification", args):
            assert delivery.confirm(session, proposal.request_id, review, proof, confirmation) == "recorded"
            with pytest.raises(HeinzelError):
                delivery.confirm(session, proposal.request_id, review, proof, confirmation)
        assert len(notices.calls) == 1
        assert delivery.recover(session, proposal.request_id) == "recorded"
        assert len(notices.calls) == 1
        real_read = repo.control_read

        def declined(session_id):
            read = real_read(session_id)
            events = [
                event.model_copy(update={"data": {**event.data, "decision": "decline"}})
                if event.kind == "approval.issued"
                else event
                for event in read.events
            ]
            return read.model_copy(update={"events": events})

        monkeypatch.setattr(repo, "control_read", declined)
        with pytest.raises(HeinzelError):
            delivery.recover(session, proposal.request_id)
        with current.local("mcp-stdio"), pytest.raises(HeinzelError):
            delivery.issue(session, proposal.request_id, "model")


def test_uncertain_queue_recovery_inspects_receipt_without_redelivery(tmp_path):
    from heinzel import Notifications
    from heinzel.kernel._approval import ApprovalConfirmation
    from heinzel.modules.heinzel._notification_delivery import NotificationDelivery

    class LostReply(Notifications):
        def __init__(self):
            self.calls = 0
            self.seen = None

        @override
        def emit_once(self, _kind, _title, _body, *, route, command, key):
            self.calls += 1
            msg = "private provider diagnostic"
            raise RuntimeError(msg)

        def has_once(self, _key):
            return self.seen

    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = crud.new_chat("create", name="Agent").session_id
        proposal = NotificationProposal(
            session_id=session, request_id="notice-2", topic="example", title="Hi", body="Private"
        )
        with repo.main_writer(session) as writer:
            NotificationProposals(crud).propose(proposal, current.principal().id, writer)
        provider = LostReply()
        delivery = NotificationDelivery(crud, current, provider)
        proof = delivery.issue(session, proposal.request_id, "test-owner")
        review = proposal.model_dump(mode="json")
        args = {"session_id": session, "request_id": proposal.request_id, "review": review, "approval_proof": proof}
        confirmation = ApprovalConfirmation()
        with confirmation.using("heinzel", "queue-agent-notification", args):
            assert delivery.confirm(session, proposal.request_id, review, proof, confirmation) == "uncertain"
        assert delivery.recover(session, proposal.request_id) == "uncertain"
        provider.seen = True
        assert delivery.recover(session, proposal.request_id) == "recorded"
        assert provider.calls == 1


def test_draft_listing_is_owner_scoped_bounded_and_contains_no_body(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = crud.new_chat("create", name="Agent").session_id
        store = NotificationProposals(crud)
        for request in ("first", "second"):
            draft = NotificationProposal(
                session_id=session, request_id=request, topic="example", title="Title", body="Private body"
            )
            with repo.main_writer(session) as writer:
                store.propose(draft, current.principal().id, writer)
        first = store.list(session, current, limit=1)
        assert [item.request_id for item in first.items] == ["first"]
        assert first.next_cursor is not None
        second = store.list(session, current, cursor=first.next_cursor, limit=1)
        assert [item.request_id for item in second.items] == ["second"]
        assert second.next_cursor is None
        assert "Private body" not in first.model_dump_json()
        with pytest.raises(HeinzelError):
            store.list(session, current, limit=101)
        stranger = CallIdentity(Principal("u-" + "a" * 32), "api-bearer", "other", "private", frozenset())
        with current.using(stranger), pytest.raises(HeinzelError):
            store.list(session, current)
