"""Owner-bound schedule policy and selected CLI dispatch, using only temporary fake processes."""

import asyncio
from datetime import UTC, datetime

import pytest

from heinzel import CurrentUser, HeinzelError
from heinzel.kernel._approval import ApprovalConfirmation
from heinzel.modules.heinzel._autonomous_schedule import AutonomousSchedules
from heinzel.modules.heinzel._contract_review import ContractReview
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository
from heinzel.modules.heinzel._user_chat import UserChatService
from heinzel.modules.system.runner._snapshot import RunnerSnapshotService
from tests.unit.modules.user_chat_test import FixtureConfig, fake_claude

pytestmark = pytest.mark.unit


def test_separate_consent_daemon_occurrence_dedup_and_pause(tmp_path, monkeypatch):  # noqa: PLR0915 - one authorization lifecycle
    import heinzel.modules.heinzel._autonomous_schedule as schedule_module

    fixed = datetime.now(UTC)

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(schedule_module, "datetime", FixedClock)
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        crud = SessionCrud(
            PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2)
        )
        session = crud.new_chat("schedule-test").session_id
        proposal = crud.propose_scheduled_agent(session, "contract", "Make a short status report", None, 30)
        contract = ContractReview(crud)
        contract.accept(session, "contract", contract.issue(session, "contract", "test-confirm"))
        executable = fake_claude(tmp_path)
        chat = UserChatService(crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable))
        schedules = AutonomousSchedules(crud, chat)
        binding = schedules.propose(session, "schedule", "contract", 60, 4, 30)
        assert binding.contract_sha256 == proposal.preview_sha256
        assert crud.snapshot(session).schedule is None
        now = fixed
        with current.local("daemon"), pytest.raises(HeinzelError, match="not been separately approved"):
            asyncio.run(schedules.tick(session, "schedule", now))
        proof = schedules.issue(session, "schedule", "chat-confirm:test:schedule")
        review = binding.model_dump(mode="json")
        marker = ApprovalConfirmation()
        with pytest.raises(HeinzelError, match="invalid"):
            schedules.approve(session, "schedule", review, proof, marker)
        args = {"session_id": session, "request_id": "schedule", "review": review, "approval_proof": proof}
        with marker.using("heinzel", "approve-agent-schedule", args):
            schedules.approve(session, "schedule", review, proof, marker)
        events = crud.repository.control_read(session)
        approval = next(e for e in events.events if e.kind == "agent.schedule-approved")
        issued = next(e for e in events.events if e.kind == "approval.issued" and e.key == approval.data["proof_hash"])
        original_read = crud.repository.control_read
        forged = issued.model_copy(update={"data": {**issued.data, "decision": "decline"}})
        altered = [forged if event.seq == issued.seq else event for event in events.events]
        monkeypatch.setattr(
            crud.repository, "control_read", lambda _session: events.model_copy(update={"events": altered})
        )
        with pytest.raises(HeinzelError, match="evidence"):
            schedules.approved(session, "schedule")
        monkeypatch.setattr(crud.repository, "control_read", original_read)
        from heinzel import CallIdentity, Principal

        owner = current.owner()
        readonly = CallIdentity(owner, "api-bearer", "test", "private", frozenset({"private:read"}))
        stranger = CallIdentity(
            Principal("u-" + "a" * 32), "api-bearer", "other", "private", frozenset({"private:write"})
        )
        for identity in (readonly, stranger):
            with current.using(identity), pytest.raises(HeinzelError):
                schedules.pause(session, "schedule")
        with current.using(stranger), pytest.raises(HeinzelError):
            schedules.list_reviews(session)
        with pytest.raises(HeinzelError, match="bounds"):
            schedules.list_reviews(session, limit=0)
        other = schedules.propose(session, "second-schedule", "contract", 60, 4, 30)
        assert other.first_run == "current-slot-after-approval"
        page = schedules.list_reviews(session, limit=1)
        assert page.items[0].binding.request_id == "schedule"
        assert page.next_cursor is not None
        assert (
            schedules.list_reviews(session, cursor=page.next_cursor, limit=1).items[0].binding.request_id
            == "second-schedule"
        )
        with pytest.raises(HeinzelError, match="already been approved"):
            schedules.issue(session, "second-schedule", "test-confirm")
        with pytest.raises(HeinzelError, match="daemon"):
            asyncio.run(schedules.tick(session, "schedule", now))
        with current.local("daemon"):
            assert asyncio.run(schedules.tick(session, "schedule", now)) == "A real recorded reply"
            count = (tmp_path / "model-calls").read_text()
            reopened = SessionCrud(
                PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2)
            )
            schedules = AutonomousSchedules(
                reopened,
                UserChatService(reopened, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable)),
            )
            assert asyncio.run(schedules.tick(session, "schedule", now)) == "A real recorded reply"
            assert (tmp_path / "model-calls").read_text() == count
        assert crud.snapshot(session).schedule is None
        activity = chat.activity(session).items
        assert [(event.tool, event.status) for event in activity] == [("unavailable", "denied")]
        schedules.pause(session, "schedule")
        assert schedules.list_reviews(session).items[0].status == "paused"
        assert schedules.issue(session, "second-schedule", "test-confirm")
        with current.local("daemon"), pytest.raises(HeinzelError, match="paused"):
            asyncio.run(schedules.tick(session, "schedule", now))


def test_cancelled_occurrence_never_relaunches_and_changed_access_refuses(tmp_path, monkeypatch):  # noqa: PLR0915
    import heinzel.modules.heinzel._autonomous_schedule as schedule_module

    fixed = datetime.now(UTC)

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(schedule_module, "datetime", FixedClock)
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        crud = SessionCrud(
            PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2)
        )
        session = crud.new_chat("cancel-test").session_id
        crud.propose_scheduled_agent(session, "contract", "Status", None, 30)
        contract = ContractReview(crud)
        contract.accept(session, "contract", contract.issue(session, "contract", "test-confirm"))
        executable = fake_claude(tmp_path, pause=True)
        chat = UserChatService(crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable))
        schedules = AutonomousSchedules(crud, chat)
        binding = schedules.propose(session, "schedule", "contract", 60, 4, 30)
        proof = schedules.issue(session, "schedule", "chat-confirm:test:schedule")
        marker = ApprovalConfirmation()
        review = binding.model_dump(mode="json")
        args = {"session_id": session, "request_id": "schedule", "review": review, "approval_proof": proof}
        with marker.using("heinzel", "approve-agent-schedule", args):
            schedules.approve(session, "schedule", review, proof, marker)
        now = fixed

        async def cancel():
            task = asyncio.create_task(schedules.tick(session, "schedule", now))
            for _ in range(200):
                if (tmp_path / "model-calls").exists():
                    break
                if task.done():
                    await task
                    pytest.fail("Scheduled turn finished before the fake model started")
                await asyncio.sleep(0.01)
            assert (tmp_path / "model-calls").exists()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        with current.local("daemon"):
            asyncio.run(cancel())
            with pytest.raises(HeinzelError, match="uncertain"):
                asyncio.run(schedules.tick(session, "schedule", now))
            from datetime import timedelta

            later = now + timedelta(seconds=60)

            class LaterClock(datetime):
                @classmethod
                def now(cls, tz=None):
                    return later

            monkeypatch.setattr(schedule_module, "datetime", LaterClock)
            with pytest.raises(HeinzelError, match="earlier message"):
                asyncio.run(schedules.tick(session, "schedule", later))
            assert sum(e.kind == "message.accepted" for e in crud.repository.control_read(session).events) == 1
            monkeypatch.setattr(schedule_module, "datetime", datetime)
        assert (tmp_path / "model-calls").read_text() == "x"
        events = crud.repository.control_read(session).events
        occurrence = next(
            e.data["message_id"] for e in events if e.kind == "message.accepted" and e.data.get("origin") == "daemon"
        )
        assert chat.turn_state(session, occurrence).status == "unresolved"
        assert chat.abandon(session, occurrence).status == "abandoned"
        executable.write_text(executable.read_text() + "\n# changed")
        with current.local("daemon"), pytest.raises(HeinzelError, match="changed"):
            asyncio.run(schedules.tick(session, "schedule", now))
        assert (tmp_path / "model-calls").read_text() == "x"


def test_amended_contract_invalidates_old_schedule_without_model_launch(tmp_path, monkeypatch):
    import heinzel.modules.heinzel._autonomous_schedule as schedule_module

    fixed = datetime.now(UTC)

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(schedule_module, "datetime", FixedClock)
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        crud = SessionCrud(
            PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2)
        )
        session = crud.new_chat("stale-test").session_id
        crud.propose_scheduled_agent(session, "contract", "Old terms", None, 30)
        contract = ContractReview(crud)
        contract.accept(session, "contract", contract.issue(session, "contract", "test-confirm"))
        executable = fake_claude(tmp_path)
        schedules = AutonomousSchedules(
            crud, UserChatService(crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable))
        )
        binding = schedules.propose(session, "schedule", "contract", 60, 4, 30)
        proof = schedules.issue(session, "schedule", "test-confirm")
        marker = ApprovalConfirmation()
        review = binding.model_dump(mode="json")
        args = {"session_id": session, "request_id": "schedule", "review": review, "approval_proof": proof}
        with marker.using("heinzel", "approve-agent-schedule", args):
            schedules.approve(session, "schedule", review, proof, marker)
        crud.amend_scheduled_agent(session, "new-contract", 1, "Changed terms", None, 30)
        with current.local("daemon"), pytest.raises(HeinzelError, match="changed"):
            asyncio.run(schedules.tick(session, "schedule", fixed))
        assert not (tmp_path / "model-calls").exists()


def test_owner_can_resolve_unstarted_daemon_occurrence_without_replaying_it(tmp_path):
    from heinzel.modules.heinzel._session_models import MessageRequest

    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        crud = SessionCrud(
            PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2)
        )
        session = crud.new_chat("gap-test").session_id
        executable = fake_claude(tmp_path)
        chat = UserChatService(crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable))
        with current.local("daemon"):
            crud.accept(session, MessageRequest(request_id="scheduled-gap", text="Scheduled instruction"))
        assert chat.turn_state(session, "scheduled-gap").status == "pending"
        assert chat.abandon(session, "scheduled-gap").status == "abandoned"
        assert chat.turn_state(session, "scheduled-gap").status == "abandoned"
        crud.accept(session, MessageRequest(request_id="after-gap", text="Continue"))
        answer = asyncio.run(chat.answer(session, "after-gap"))
        assert answer.message.text == "A real recorded reply"
        assert "set aside with an uncertain outcome" in (tmp_path / "prompt-input").read_text()
        with pytest.raises(HeinzelError):
            asyncio.run(chat.answer(session, "scheduled-gap"))


def test_expired_occurrence_cannot_launch_after_auth_delays(tmp_path, monkeypatch):
    from datetime import timedelta

    import heinzel.modules.heinzel._autonomous_schedule as schedule_module
    from heinzel.modules.system.runner._process import run_bounded

    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        crud = SessionCrud(
            PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2)
        )
        session = crud.new_chat("rollover").session_id
        crud.propose_scheduled_agent(session, "contract", "Status", None, 30)
        contract = ContractReview(crud)
        contract.accept(session, "contract", contract.issue(session, "contract", "test-confirm"))
        executable = fake_claude(tmp_path)
        start = datetime.now(UTC).replace(second=0, microsecond=0) + timedelta(seconds=1)
        clock = [start]

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock[0]

        monkeypatch.setattr(schedule_module, "datetime", Clock)

        def execute(command, **kwargs):
            result = run_bounded(command, **kwargs)
            if command[1:] == ["auth", "status", "--json"]:
                clock[0] = start + timedelta(seconds=60)
            return result

        chat = UserChatService(
            crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable), execute=execute
        )
        schedules = AutonomousSchedules(crud, chat)
        binding = schedules.propose(session, "schedule", "contract", 60, 4, 30)
        proof = schedules.issue(session, "schedule", "test-confirm")
        marker = ApprovalConfirmation()
        review = binding.model_dump(mode="json")
        args = {"session_id": session, "request_id": "schedule", "review": review, "approval_proof": proof}
        with marker.using("heinzel", "approve-agent-schedule", args):
            schedules.approve(session, "schedule", review, proof, marker)
        with current.local("daemon"), pytest.raises(HeinzelError, match="expired"):
            asyncio.run(schedules.tick(session, "schedule", start))
        assert not (tmp_path / "model-calls").exists()
        assert not any(e.kind == "message.accepted" for e in crud.repository.control_read(session).events)


def test_failure_after_acceptance_before_start_can_be_resolved(tmp_path, monkeypatch):
    import heinzel.modules.heinzel._autonomous_schedule as schedule_module
    from heinzel.modules.heinzel._claude_subscription_model import ClaudeSubscriptionModel
    from heinzel.modules.heinzel._session_models import MessageRequest

    fixed = datetime(2026, 1, 5, 12, 0, 1, tzinfo=UTC)

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(schedule_module, "datetime", FixedClock)
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        crud = SessionCrud(
            PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2)
        )
        session = crud.new_chat("prepare-gap").session_id
        crud.propose_scheduled_agent(session, "contract", "Status", None, 30)
        contract = ContractReview(crud)
        contract.accept(session, "contract", contract.issue(session, "contract", "test-confirm"))
        executable = fake_claude(tmp_path)
        chat = UserChatService(crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable))
        schedules = AutonomousSchedules(crud, chat)
        binding = schedules.propose(session, "schedule", "contract", 60, 4, 30)
        proof = schedules.issue(session, "schedule", "test-confirm")
        marker = ApprovalConfirmation()
        review = binding.model_dump(mode="json")
        args = {"session_id": session, "request_id": "schedule", "review": review, "approval_proof": proof}
        with marker.using("heinzel", "approve-agent-schedule", args):
            schedules.approve(session, "schedule", review, proof, marker)
        original = ClaudeSubscriptionModel.bind_fence

        def fail(_model, _fence):
            msg = "simulated admission failure"
            raise HeinzelError(msg)

        monkeypatch.setattr(ClaudeSubscriptionModel, "bind_fence", fail)
        with current.local("daemon"), pytest.raises(HeinzelError, match="simulated"):
            asyncio.run(schedules.tick(session, "schedule", fixed))
        events = crud.repository.control_read(session).events
        occurrence = next(e.data["message_id"] for e in events if e.kind == "message.accepted")
        assert not any(e.kind == "interactive.started" for e in events)
        assert chat.turn_state(session, occurrence).status == "pending"
        assert chat.abandon(session, occurrence).status == "abandoned"
        monkeypatch.setattr(ClaudeSubscriptionModel, "bind_fence", original)
        crud.accept(session, MessageRequest(request_id="continue", text="Continue"))
        assert asyncio.run(chat.answer(session, "continue")).message.text == "A real recorded reply"


def test_unstarted_resolution_must_follow_accepted_daemon_message(tmp_path, monkeypatch):
    from pydantic_ai.models.function import FunctionModel

    from heinzel.modules.heinzel._engine import TurnLimits
    from heinzel.modules.heinzel._interactive_turn import InteractiveTurn
    from heinzel.modules.heinzel._session_models import MessageRequest

    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = crud.new_chat("resolution-order").session_id
        chat = UserChatService(
            crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(fake_claude(tmp_path))
        )
        with current.local("daemon"):
            crud.accept(session, MessageRequest(request_id="scheduled", text="Scheduled"))
        chat.abandon(session, "scheduled")
        crud.accept(session, MessageRequest(request_id="next", text="Next"))
        snapshot = repo.control_read(session)
        accepted = next(e for e in snapshot.events if e.kind == "message.accepted" and e.key == "message-scheduled")
        abandoned = next(e for e in snapshot.events if e.kind == "interactive.abandoned")
        altered = abandoned.model_copy(update={"seq": accepted.seq})
        events = [altered if e is abandoned else e for e in snapshot.events]
        monkeypatch.setattr(repo, "control_read", lambda _session: snapshot.model_copy(update={"events": events}))
        with pytest.raises(HeinzelError, match="invalid resolution"):
            chat.turn_state(session, "scheduled")
        with pytest.raises(HeinzelError, match="invalid resolution"):
            asyncio.run(InteractiveTurn(crud).run(session, "next", FunctionModel(lambda *_: None), limits=TurnLimits()))


def test_schedule_binding_tracks_full_runner_configuration(tmp_path):
    from heinzel.modules.heinzel._autonomous_schedule import _fingerprint
    from heinzel.modules.system.runner._models import RunnerSettings

    class ConfigWithRunnerChange(FixtureConfig):
        def __init__(self, executable, codex_model):
            super().__init__(executable)
            self.codex_model = codex_model

        def settings(self, cls, section):
            result = super().settings(cls, section)
            return result.model_copy(update={"codex_model": self.codex_model}) if cls is RunnerSettings else result

    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        crud = SessionCrud(
            PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2)
        )
        executable = fake_claude(tmp_path)
        config = [ConfigWithRunnerChange(executable, None)]
        chat = UserChatService(crud, current, RunnerSnapshotService(), load=lambda: config[0])
        original = _fingerprint(chat)
        config[0] = ConfigWithRunnerChange(executable, "changed-model")
        assert _fingerprint(chat) != original


def test_occurrence_expiring_during_model_call_refuses_reply_and_never_replays(tmp_path, monkeypatch):
    from datetime import timedelta

    import heinzel.modules.heinzel._autonomous_schedule as schedule_module
    from heinzel.modules.system.runner._process import run_bounded

    clock = [datetime.now(UTC)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    monkeypatch.setattr(schedule_module, "datetime", Clock)
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        crud = SessionCrud(
            PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2)
        )
        session = crud.new_chat("rollover-inflight").session_id
        crud.propose_scheduled_agent(session, "contract", "Status", None, 30)
        contract = ContractReview(crud)
        contract.accept(session, "contract", contract.issue(session, "contract", "test-confirm"))
        executable = fake_claude(tmp_path)
        start = clock[0]

        def execute(command, **kwargs):
            result = run_bounded(command, **kwargs)
            if "--safe-mode" in command:
                clock[0] = start + timedelta(seconds=60)
            return result

        chat = UserChatService(
            crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable), execute=execute
        )
        schedules = AutonomousSchedules(crud, chat)
        binding = schedules.propose(session, "schedule", "contract", 60, 4, 30)
        proof = schedules.issue(session, "schedule", "test-confirm")
        marker = ApprovalConfirmation()
        review = binding.model_dump(mode="json")
        args = {"session_id": session, "request_id": "schedule", "review": review, "approval_proof": proof}
        with marker.using("heinzel", "approve-agent-schedule", args):
            schedules.approve(session, "schedule", review, proof, marker)
        with current.local("daemon"), pytest.raises(HeinzelError, match="subscription response"):
            asyncio.run(schedules.tick(session, "schedule", start))
        assert (tmp_path / "model-calls").read_text() == "x"
        assert not any(e.kind == "message.replied" for e in crud.repository.control_read(session).events)
        with current.local("daemon"), pytest.raises(HeinzelError, match="current schedule occurrence"):
            asyncio.run(schedules.tick(session, "schedule", start))
        assert (tmp_path / "model-calls").read_text() == "x"
