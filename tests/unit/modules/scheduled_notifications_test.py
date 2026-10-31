"""Scheduled notification attempts are owner-bound, rate-limited and never replayed."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import override

import pytest

from heinzel import CurrentUser, HeinzelError, Notifications
from heinzel.kernel._approval import ApprovalConfirmation
from heinzel.modules.heinzel._autonomous_schedule import AutonomousSchedules, _binding_intent
from heinzel.modules.heinzel._contract_review import ContractReview
from heinzel.modules.heinzel._scheduled_notifications import (
    ScheduledNotificationPolicy,
    ScheduledNotificationRecovery,
    ScheduledNotifications,
)
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository
from heinzel.modules.heinzel._user_chat import UserChatService
from heinzel.modules.system.runner._snapshot import RunnerSnapshotService
from tests.unit.modules.user_chat_test import FixtureConfig, fake_claude

pytestmark = pytest.mark.unit


class FakeNotifications(Notifications):
    def __init__(self, result=True):
        self.calls = []
        self.compacted = []
        self.result = result

    @override
    def emit_once(self, _kind, title, body, *, route, command, key, helper=None):
        self.calls.append((title, body, route, key))
        return self.result

    @override
    def forget_completed(self, keys):
        self.compacted.extend(keys)
        return True


def test_rate_topic_uncertainty_and_daemon_ownership(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    clock = [datetime(2026, 1, 1, tzinfo=UTC)]
    with current.local("cli"):
        repo = PersonalSessionRepository(current, clock=lambda: clock[0])
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2), clock=lambda: clock[0])
        session = crud.new_chat("new").session_id
        notice = FakeNotifications()
        policy = ScheduledNotificationPolicy(max_per_24h=2, topic_cooldown_seconds=300)

        def runner(turn, provider=notice):
            return ScheduledNotifications(
                crud,
                current,
                provider,
                session=session,
                turn=turn,
                contract_sha256="a" * 64,
                schedule_request="schedule",
                schedule_sha256="b" * 64,
                policy=policy,
                verify=lambda: None,
            )

        args = {"topic": "topic-one", "title": "Title", "body": "Private"}
        with repo.main_writer(session) as writer, pytest.raises(HeinzelError, match="authorized"):
            runner("one").send(args, writer)
        with current.local("daemon"), repo.main_writer(session) as writer:
            assert runner("one").send(args, writer)[0] == "recorded"
            with pytest.raises(HeinzelError, match="already attempted"):
                runner("one").send({**args, "topic": "other"}, writer)
        with (
            current.local("daemon"),
            repo.main_writer(session) as writer,
            pytest.raises(HeinzelError, match="cooldown"),
        ):
            runner("cooldown").send(args, writer)
        clock[0] += timedelta(seconds=301)
        unknown = FakeNotifications(result=False)
        with current.local("daemon"), repo.main_writer(session) as writer:
            assert runner("two", unknown).send(args, writer)[0] == "uncertain"
        with (
            current.local("daemon"),
            repo.main_writer(session) as writer,
            pytest.raises(HeinzelError, match="rate limit"),
        ):
            runner("rate").send({**args, "topic": "different"}, writer)
        clock[0] += timedelta(days=1, seconds=1)
        with (
            current.local("daemon"),
            repo.main_writer(session) as writer,
            pytest.raises(HeinzelError, match="uncertain"),
        ):
            runner("three").send(args, writer)
        assert len(unknown.calls) == 1
        assert len(notice.compacted) == 1
        assert unknown.compacted == []
        changed_contract = ScheduledNotifications(
            crud,
            current,
            notice,
            session=session,
            turn="new-contract-turn",
            contract_sha256="c" * 64,
            schedule_request="new-schedule",
            schedule_sha256="d" * 64,
            policy=policy,
            verify=lambda: None,
        )
        with (
            current.local("daemon"),
            repo.main_writer(session) as writer,
            pytest.raises(HeinzelError, match="uncertain"),
        ):
            changed_contract.send(args, writer)
        recovery = ScheduledNotificationRecovery(crud, current, unknown)
        page = recovery.list(session, limit=1)
        assert page.items[0].status == "recorded"
        assert page.next_cursor is not None
        events = repo.control_read(session).events
        original = next(event for event in events if event.kind == "scheduled-notification.attempted")
        with pytest.raises(HeinzelError, match="inconsistent"):
            recovery.validated_view(session, original.model_copy(update={"turn": "another-turn"}), events)
        pending = recovery.list(session, cursor=page.next_cursor).items[0]
        assert (pending.status, pending.proposal.body) == ("unresolved", "Private")
        with pytest.raises(HeinzelError, match="Acknowledge"):
            recovery.resolve(session, pending.proposal.request_id, acknowledge_uncertain=False)
        resolved = recovery.resolve(session, pending.proposal.request_id, acknowledge_uncertain=True)
        assert resolved.status == "abandoned"
        assert len(unknown.calls) == 1
        with current.local("daemon"), repo.main_writer(session) as writer:
            assert runner("three").send(args, writer)[0] == "recorded"


def test_exact_schedule_consent_dispatch_and_legacy_has_no_tool(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = crud.new_chat("new").session_id
        proposal = crud.propose_scheduled_agent(session, "contract", "Produce a concise status", None, 30)
        contracts = ContractReview(crud)
        contracts.accept(session, "contract", contracts.issue(session, "contract", "owner"))
        executable = fake_claude(
            tmp_path, tool="notification:send", arguments={"topic": "example", "title": "Title", "body": "Private"}
        )
        provider = FakeNotifications()
        chat = UserChatService(
            crud,
            current,
            RunnerSnapshotService(),
            load=lambda: FixtureConfig(executable),
            notifications=lambda: provider,
        )
        schedules = AutonomousSchedules(crud, chat)
        legacy = schedules.propose(session, "legacy", "contract", 3600, 4, 30)
        assert "notification_policy" not in _binding_intent(legacy)
        policy = ScheduledNotificationPolicy(max_per_24h=2, topic_cooldown_seconds=300)
        binding = schedules.propose(session, "schedule", "contract", 3600, 4, 30, notification_policy=policy)
        assert binding.contract_sha256 == proposal.preview_sha256
        assert _binding_intent(binding)["notification_policy"] == policy.model_dump(mode="json")
        now = datetime.now(UTC)
        with current.local("daemon"), pytest.raises(HeinzelError):
            asyncio.run(schedules.tick(session, "schedule", now))
        assert provider.calls == []
        proof = schedules.issue(session, "schedule", "owner")
        review = binding.model_dump(mode="json")
        confirmation = ApprovalConfirmation()
        params = {"session_id": session, "request_id": "schedule", "review": review, "approval_proof": proof}
        with confirmation.using("heinzel", "approve-agent-schedule", params):
            schedules.approve(session, "schedule", review, proof, confirmation)
        with current.local("daemon"):
            result = asyncio.run(schedules.tick(session, "schedule", now))
            assert result == "A real recorded reply"
            assert asyncio.run(schedules.tick(session, "schedule", now)) == result
        assert len(provider.calls) == 1
        activity = chat.activity(session).items
        assert [(event.tool, event.status) for event in activity] == [
            ("notification:send", "requested"),
            ("notification:send", "completed"),
        ]
        schedules.pause(session, "schedule")
        with current.local("daemon"), pytest.raises(HeinzelError):
            asyncio.run(schedules.tick(session, "schedule", now))
        assert len(provider.calls) == 1


def test_prior_receipt_reconciles_without_repeat_and_rejects_foreign_recovery(tmp_path):
    from heinzel import CallIdentity, Principal

    class LostReply(FakeNotifications):
        @override
        def has_once(self, _key):
            return True

    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = crud.new_chat("new").session_id
        provider = LostReply(result=False)
        policy = ScheduledNotificationPolicy(max_per_24h=2, topic_cooldown_seconds=60)
        notice = ScheduledNotifications(
            crud,
            current,
            provider,
            session=session,
            turn="turn",
            contract_sha256="a" * 64,
            schedule_request="schedule",
            schedule_sha256="b" * 64,
            policy=policy,
            verify=lambda: None,
        )
        with current.local("daemon"), repo.main_writer(session) as writer:
            assert notice.send({"topic": "example", "title": "Title", "body": "Private"}, writer)[0] == "uncertain"
        recovery = ScheduledNotificationRecovery(crud, current, provider)
        attempt = recovery.list(session).items[0]
        stranger = CallIdentity(
            Principal("u-" + "a" * 32), "api-bearer", "other", "private", frozenset({"private:write"})
        )
        with current.using(stranger), pytest.raises(HeinzelError):
            recovery.resolve(session, attempt.proposal.request_id, acknowledge_uncertain=True)
        resolved = recovery.resolve(session, attempt.proposal.request_id, acknowledge_uncertain=True)
        assert resolved.status == "recorded"
        assert len(provider.calls) == 1
        assert len(provider.compacted) == 1


def test_legacy_schedule_rejects_hallucinated_notification_tool(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        crud = SessionCrud(
            PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2)
        )
        session = crud.new_chat("new").session_id
        crud.propose_scheduled_agent(session, "contract", "Produce a short status", None, 30)
        contracts = ContractReview(crud)
        contracts.accept(session, "contract", contracts.issue(session, "contract", "owner"))
        executable = fake_claude(
            tmp_path, tool="notification:send", arguments={"topic": "example", "title": "Title", "body": "Private"}
        )
        provider = FakeNotifications()
        chat = UserChatService(
            crud,
            current,
            RunnerSnapshotService(),
            load=lambda: FixtureConfig(executable),
            notifications=lambda: provider,
        )
        schedules = AutonomousSchedules(crud, chat)
        binding = schedules.propose(session, "legacy", "contract", 3600, 4, 30)
        assert "notification_policy" not in _binding_intent(binding)
        proof = schedules.issue(session, "legacy", "owner")
        review = binding.model_dump(mode="json")
        confirmation = ApprovalConfirmation()
        params = {"session_id": session, "request_id": "legacy", "review": review, "approval_proof": proof}
        with confirmation.using("heinzel", "approve-agent-schedule", params):
            schedules.approve(session, "legacy", review, proof, confirmation)
        with current.local("daemon"):
            assert asyncio.run(schedules.tick(session, "legacy", datetime.now(UTC))) == "A real recorded reply"
        assert provider.calls == []
        assert [(event.tool, event.status) for event in chat.activity(session).items] == [
            ("notification:send", "requested"),
            ("notification:send", "denied"),
        ]


def test_policy_is_bounded_and_rejects_boolean_or_float_limit():
    from pydantic import ValidationError

    for invalid in (True, 1.0, 0, 2):
        with pytest.raises(ValidationError):
            ScheduledNotificationPolicy(max_per_24h=1, topic_cooldown_seconds=60, per_turn=invalid)
    for values in (
        {"max_per_24h": 25, "topic_cooldown_seconds": 60},
        {"max_per_24h": 1, "topic_cooldown_seconds": 59},
        {"max_per_24h": 1, "topic_cooldown_seconds": 60, "delivery": "email"},
    ):
        with pytest.raises(ValidationError):
            ScheduledNotificationPolicy.model_validate(values)


def test_observed_item_policy_cannot_activate_without_separate_approved_source(tmp_path):
    from heinzel.modules.heinzel._scheduled_notifications import ItemNotificationPolicy

    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        crud = SessionCrud(
            PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2)
        )
        session = crud.new_chat("item-policy").session_id
        crud.propose_scheduled_agent(session, "contract", "Inspect the approved source", None, 30)
        contracts = ContractReview(crud)
        contracts.accept(session, "contract", contracts.issue(session, "contract", "owner"))
        executable = fake_claude(tmp_path)
        service = UserChatService(crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable))
        schedules = AutonomousSchedules(crud, service)
        policy = ScheduledNotificationPolicy(
            max_per_24h=2,
            topic_cooldown_seconds=60,
            item_evidence=ItemNotificationPolicy(deduplicate_by="item"),
            disclosure=(
                "Content may appear on this device. Item identities are provider-reported "
                "and not completeness guarantees."
            ),
        )
        with pytest.raises(HeinzelError, match="separately approved source"):
            schedules.propose(session, "schedule", "contract", 60, 2, 30, notification_policy=policy)
        assert not any(
            event.kind == "agent.schedule-proposed" for event in crud.repository.control_read(session).events
        )


def test_bulk_inventory_linear_validation_and_inconsistent_outcomes(tmp_path, monkeypatch):
    import hashlib

    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = crud.new_chat("bulk").session_id
        notice = FakeNotifications()
        runner = ScheduledNotifications(
            crud,
            current,
            notice,
            session=session,
            turn="original",
            contract_sha256="a" * 64,
            schedule_request="schedule",
            schedule_sha256="b" * 64,
            policy=ScheduledNotificationPolicy(max_per_24h=2, topic_cooldown_seconds=300),
            verify=lambda: None,
        )
        with current.local("daemon"), repo.main_writer(session) as writer:
            runner.send({"topic": "bulk", "title": "Title", "body": "Private"}, writer)
        events = repo.control_read(session).events
        attempt = next(e for e in events if e.kind == "scheduled-notification.attempted")
        recorded = next(e for e in events if e.kind == "scheduled-notification.recorded")
        template = crud.read_body(session, attempt)
        owner = repo.principal_id
        bodies = {}
        attempts = []
        outcomes = []
        for n in range(200):
            turn = f"turn-{n}"
            topic = f"topic-{n}"
            digest = hashlib.sha256(f"{owner}\0{session}\0{topic}".encode()).hexdigest()
            key = "notice-" + hashlib.sha256(f"{owner}\0{session}\0{turn}\0{digest}".encode()).hexdigest()
            bodies[key] = {**template, "topic": topic, "request_id": key}
            attempts.append(
                attempt.model_copy(
                    update={"key": key, "turn": turn, "seq": n + 1, "data": {**attempt.data, "topic_sha256": digest}}
                )
            )
            outcomes.append(recorded.model_copy(update={"key": key, "turn": turn, "seq": n + 201}))

        class CountedEvents(list):
            scans = 0

            def __iter__(self):
                self.scans += 1
                return super().__iter__()

        reads = []

        def read_body(_session, event):
            reads.append(event.key)
            return bodies[event.key]

        monkeypatch.setattr(crud, "read_body", read_body)
        recovery = ScheduledNotificationRecovery(crud, current, notice)
        journal = CountedEvents(attempts + outcomes)
        assert recovery.unresolved_attempts(journal) == []
        assert journal.scans == 1
        assert reads == []  # a resolved attempt's expired body can never block a later turn
        assert recovery.unresolved_attempts(attempts + outcomes[:-1]) == attempts[-1:]
        forged = outcomes[0].model_copy(
            update={"kind": "scheduled-notification.abandoned", "data": {"acknowledged": True}}
        )
        for events, refusal in (
            (attempts + outcomes + [outcomes[0]], "inconsistent"),
            ([*attempts, attempts[0], *outcomes], "inconsistent"),
            (attempts + outcomes + [outcomes[0].model_copy(update={"key": "orphan"})], "inconsistent"),
            ([*attempts, outcomes[0].model_copy(update={"seq": 1})], "inconsistent"),
            ([*attempts, forged], "invalid"),
        ):
            with pytest.raises(HeinzelError, match=refusal):
                recovery.unresolved_attempts(events)


def test_an_expired_body_blocks_but_its_owner_can_still_set_it_aside(tmp_path, monkeypatch):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = crud.new_chat("new").session_id
        provider = FakeNotifications(result=False)
        notice = ScheduledNotifications(
            crud,
            current,
            provider,
            session=session,
            turn="turn",
            contract_sha256="a" * 64,
            schedule_request="schedule",
            schedule_sha256="b" * 64,
            policy=ScheduledNotificationPolicy(max_per_24h=2, topic_cooldown_seconds=60),
            verify=lambda: None,
        )
        with current.local("daemon"), repo.main_writer(session) as writer:
            assert notice.send({"topic": "example", "title": "Title", "body": "Private"}, writer)[0] == "uncertain"

        def expired(_session, _event):
            raise FileNotFoundError

        monkeypatch.setattr(crud, "read_body", expired)
        recovery = ScheduledNotificationRecovery(crud, current, provider)
        assert len(recovery.unresolved_attempts(repo.control_read(session).events)) == 1
        view = recovery.list(session).items[0]
        assert (view.status, view.proposal.topic, view.proposal.body) == ("unresolved", "unavailable", "")
        assert "Private" not in view.model_dump_json()
        resolved = recovery.resolve(session, view.proposal.request_id, acknowledge_uncertain=True)
        assert resolved.status == "abandoned"
        assert recovery.unresolved_attempts(repo.control_read(session).events) == []
