"""Slots fence uncertain subscription launches; fake CLI only, never a paid call."""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart

from heinzel import CurrentUser, HeinzelError
from heinzel.modules.heinzel._claude_subscription_model import ClaudeSubscriptionModel
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository
from heinzel.modules.heinzel._subscription_slots import SubscriptionSlots
from tests.unit.modules.claude_subscription_model_test import FixtureVerifier, admission, fake_cli, parameters

pytestmark = pytest.mark.unit


def test_real_fake_cli_restart_refuses_replay(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        session = (
            SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=1)).new_chat("new").session_id
        )
        policy = replace(admission(), owner=repo.principal_id, session=session)
        executable = fake_cli(tmp_path)
        first = [ModelRequest(parts=[UserPromptPart(content="hello")])]

        def model():
            return ClaudeSubscriptionModel(
                executable,
                policy,
                FixtureVerifier(),
                lambda: policy,
                slots=SubscriptionSlots(PersonalSessionRepository(current)),
                turn_id="turn",
            )

        running = model()
        answer = asyncio.run(running.request(first, None, parameters()))
        followup = [*first, answer, ModelRequest(parts=[ToolReturnPart(tool_name="use_tool", content="result")])]
        asyncio.run(running.request(followup, None, parameters()))
        restarted = model()
        with pytest.raises(HeinzelError, match="safely admitted"):
            asyncio.run(restarted.request(first, None, parameters()))
        # A denied replay cannot advance to the next ordinal.
        with pytest.raises(HeinzelError, match="safely admitted"):
            asyncio.run(restarted.request(first, None, parameters()))
        assert len(repo.subscription_read(session).events) == 2


def test_concurrent_same_slot_and_cap(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        session = (
            SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=1)).new_chat("new").session_id
        )
        owner = repo.principal_id

    def reserve():
        with current.local("cli"):
            try:
                SubscriptionSlots(PersonalSessionRepository(current)).reserve(owner, session, "turn", 1, 1, 1)
            except HeinzelError:
                return "denied"
            return "reserved"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: reserve(), range(2))) == ["denied", "reserved"]
    with current.local("cli"), pytest.raises(HeinzelError):
        SubscriptionSlots(repo).reserve(owner, session, "turn", 1, 1, 2)


def test_cancelled_fake_cli_keeps_slot_and_never_replays(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        session = (
            SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=1)).new_chat("new").session_id
        )
        policy = replace(admission(), owner=repo.principal_id, session=session)
        marker = tmp_path / "launched"
        executable = fake_cli(
            tmp_path,
            extra=(
                f"import time\nopen({str(marker)!r}, 'a').write('x')\ntime.sleep(5)\n"
                f"open({str(marker)!r}, 'a').write('late')"
            ),
        )
        first = [ModelRequest(parts=[UserPromptPart(content="hello")])]

        def model():
            return ClaudeSubscriptionModel(
                executable,
                policy,
                FixtureVerifier(),
                lambda: policy,
                slots=SubscriptionSlots(PersonalSessionRepository(current)),
                turn_id="turn",
            )

        async def cancel():
            task = asyncio.create_task(model().request(first, None, parameters()))
            for _ in range(200):
                if marker.exists():
                    break
                await asyncio.sleep(0.01)
            assert marker.exists()
            start = time.monotonic()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert time.monotonic() - start < 2

        asyncio.run(cancel())
        with pytest.raises(HeinzelError):
            asyncio.run(model().request(first, None, parameters()))
        assert marker.read_text() == "x"
        assert len(repo.subscription_read(session).events) == 1


def test_slots_reject_another_owner_and_policy_change(tmp_path):
    from heinzel import CallIdentity, Principal

    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        session = (
            SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=1)).new_chat("new").session_id
        )
        owner = repo.principal_id
        SubscriptionSlots(repo).reserve(owner, session, "turn", 1, 2, 1)
        with pytest.raises(HeinzelError):
            SubscriptionSlots(repo).reserve(owner, session, "turn", 2, 2, 2)
    stranger = CallIdentity(Principal("u-" + "a" * 32), "api-bearer", "other", "private", frozenset({"private:read"}))
    with current.using(stranger), pytest.raises(HeinzelError):
        SubscriptionSlots(repo).reserve(owner, session, "turn", 1, 2, 2)


def test_timed_out_fake_cli_keeps_slot(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        session = (
            SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=1)).new_chat("new").session_id
        )
        policy = replace(admission(), owner=repo.principal_id, session=session, timeout_seconds=0.5)
        marker = tmp_path / "launched"
        executable = fake_cli(tmp_path, extra=f"import time\nopen({str(marker)!r}, 'a').write('x')\ntime.sleep(3)")
        first = [ModelRequest(parts=[UserPromptPart(content="hello")])]

        def model():
            return ClaudeSubscriptionModel(
                executable,
                policy,
                FixtureVerifier(),
                lambda: policy,
                slots=SubscriptionSlots(PersonalSessionRepository(current)),
                turn_id="turn",
            )

        with pytest.raises(HeinzelError):
            asyncio.run(model().request(first, None, parameters()))
        with pytest.raises(HeinzelError):
            asyncio.run(model().request(first, None, parameters()))
        assert marker.read_text() == "x"
        assert len(repo.subscription_read(session).events) == 1
        assert repo.budget_read(session).events == []
