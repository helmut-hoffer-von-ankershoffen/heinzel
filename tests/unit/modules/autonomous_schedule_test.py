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
from tests.unit.modules.user_chat_test import FixtureConfig, fake_claude

pytestmark = pytest.mark.unit


def test_separate_consent_daemon_occurrence_dedup_and_pause(tmp_path):
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
        chat = UserChatService(crud, current, load=lambda: FixtureConfig(executable))
        schedules = AutonomousSchedules(crud, chat)
        binding = schedules.propose(session, "schedule", "contract", 60, 4, 30)
        assert binding.contract_sha256 == proposal.preview_sha256
        assert crud.snapshot(session).schedule is None
        now = datetime.now(UTC)
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
        with pytest.raises(HeinzelError, match="daemon"):
            asyncio.run(schedules.tick(session, "schedule", now))
        with current.local("daemon"):
            assert asyncio.run(schedules.tick(session, "schedule", now)) == "A real recorded reply"
            count = (tmp_path / "model-calls").read_text()
            reopened = SessionCrud(
                PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2)
            )
            schedules = AutonomousSchedules(
                reopened, UserChatService(reopened, current, load=lambda: FixtureConfig(executable))
            )
            assert asyncio.run(schedules.tick(session, "schedule", now)) == "A real recorded reply"
            assert (tmp_path / "model-calls").read_text() == count
        assert crud.snapshot(session).schedule is None
        activity = chat.activity(session).items
        assert [(event.tool, event.status) for event in activity] == [("unavailable", "denied")]
        schedules.pause(session, "schedule")
        with current.local("daemon"), pytest.raises(HeinzelError, match="paused"):
            asyncio.run(schedules.tick(session, "schedule", now))


def test_cancelled_occurrence_never_relaunches_and_changed_access_refuses(tmp_path):
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
        chat = UserChatService(crud, current, load=lambda: FixtureConfig(executable))
        schedules = AutonomousSchedules(crud, chat)
        binding = schedules.propose(session, "schedule", "contract", 60, 4, 30)
        proof = schedules.issue(session, "schedule", "chat-confirm:test:schedule")
        marker = ApprovalConfirmation()
        review = binding.model_dump(mode="json")
        args = {"session_id": session, "request_id": "schedule", "review": review, "approval_proof": proof}
        with marker.using("heinzel", "approve-agent-schedule", args):
            schedules.approve(session, "schedule", review, proof, marker)
        now = datetime.now(UTC)

        async def cancel():
            task = asyncio.create_task(schedules.tick(session, "schedule", now))
            for _ in range(200):
                if (tmp_path / "model-calls").exists():
                    break
                await asyncio.sleep(0.01)
            assert (tmp_path / "model-calls").exists()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        with current.local("daemon"):
            asyncio.run(cancel())
            with pytest.raises(HeinzelError, match="uncertain"):
                asyncio.run(schedules.tick(session, "schedule", now))
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


def test_amended_contract_invalidates_old_schedule_without_model_launch(tmp_path):
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
        schedules = AutonomousSchedules(crud, UserChatService(crud, current, load=lambda: FixtureConfig(executable)))
        binding = schedules.propose(session, "schedule", "contract", 60, 4, 30)
        proof = schedules.issue(session, "schedule", "test-confirm")
        marker = ApprovalConfirmation()
        review = binding.model_dump(mode="json")
        args = {"session_id": session, "request_id": "schedule", "review": review, "approval_proof": proof}
        with marker.using("heinzel", "approve-agent-schedule", args):
            schedules.approve(session, "schedule", review, proof, marker)
        crud.amend_scheduled_agent(session, "new-contract", 1, "Changed terms", None, 30)
        with current.local("daemon"), pytest.raises(HeinzelError, match="changed"):
            asyncio.run(schedules.tick(session, "schedule", datetime.now(UTC)))
        assert not (tmp_path / "model-calls").exists()
