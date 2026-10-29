"""A real typed model loop only executes host-controlled calls and persists one reply."""

import asyncio
import json

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from heinzel import CallIdentity, CurrentUser, HeinzelError, Principal
from heinzel.modules.heinzel._engine import TurnLimits
from heinzel.modules.heinzel._interactive_turn import InteractiveTurn
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import MessageRequest, SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

pytestmark = pytest.mark.unit


def test_real_model_loop_tool_dispatch_reply_and_retry_after_reopen(tmp_path):
    current = CurrentUser(tmp_path / "state" / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = crud.new_chat("create").session_id
        crud.accept(session, MessageRequest(request_id="request", text="How many words?"))
        calls = 0

        def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            nonlocal calls
            calls += 1
            assert [tool.name for tool in info.function_tools] == ["use_tool"]
            if calls == 1:
                return ModelResponse(
                    [ToolCallPart("use_tool", {"name": "text.word_count", "arguments": {"text": "one secret three"}})]
                )
            assert any(
                isinstance(part, ToolReturnPart) and part.content == "3" for msg in messages for part in msg.parts
            )
            return ModelResponse(
                [ToolCallPart(info.output_tools[0].name, {"report": "Counted", "summary": "Three words."})]
            )

        result = asyncio.run(InteractiveTurn(crud).run(session, "request", FunctionModel(model), limits=TurnLimits()))
        assert result.text == "Three words."
        assert result.role == "assistant"
        assert calls == 2
        reopened = SessionCrud(
            PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2)
        )
        retry = asyncio.run(
            InteractiveTurn(reopened).run(session, "request", FunctionModel(model), limits=TurnLimits())
        )
        assert retry == result
        assert calls == 2
        events = repo.control_read(session).events
        activity = [event for event in events if event.kind == "interactive.tool"]
        assert len(activity) == 1
        assert activity[0].data == {"tool": "text.word_count", "status": "completed"}
        assert "one secret three" not in json.dumps(activity[0].data)
        assert [message.text for message in crud.snapshot(session).messages] == ["How many words?", "Three words."]


def test_unknown_tool_and_write_proposal_never_execute_and_owner_is_checked(tmp_path):
    current = CurrentUser(tmp_path / "state" / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = crud.new_chat("create").session_id
        crud.accept(session, MessageRequest(request_id="request", text="Prepare something"))
        turns = 0

        def model(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            nonlocal turns
            turns += 1
            if turns == 1:
                return ModelResponse(
                    [
                        ToolCallPart("use_tool", {"name": "shell", "arguments": {"command": "do not run"}}),
                        ToolCallPart("use_tool", {"name": "propose_action", "arguments": {"target": "private draft"}}),
                    ]
                )
            return ModelResponse([ToolCallPart(info.output_tools[0].name, {"report": "Waiting for review"})])

        runner = InteractiveTurn(crud)
        result = asyncio.run(runner.run(session, "request", FunctionModel(model), limits=TurnLimits()))
        assert result.text == "Waiting for review"
        events = repo.control_read(session).events
        denied = [event for event in events if event.kind == "interactive.tool"]
        proposals = [event for event in events if event.kind == "interactive.proposed"]
        assert len(denied) == len(proposals) == 1
        assert denied[0].data == {"tool": "unavailable", "status": "denied"}
        assert "private draft" not in json.dumps(proposals[0].data)
        stranger = CallIdentity(
            Principal("u-" + "a" * 32), "api-bearer", "other", "private", frozenset({"private:read"})
        )
        with current.using(stranger), pytest.raises(HeinzelError, match="different authenticated owner"):
            asyncio.run(runner.run(session, "request", FunctionModel(model), limits=TurnLimits()))


def test_interrupted_model_is_not_reexecuted_after_reopen(tmp_path):
    current = CurrentUser(tmp_path / "state" / "identity")
    with current.local("cli"):
        crud = SessionCrud(
            PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2)
        )
        session = crud.new_chat("create").session_id
        crud.accept(session, MessageRequest(request_id="request", text="Try once"))
        attempts = 0

        def failing(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
            nonlocal attempts
            attempts += 1
            msg = "transport interrupted"
            raise RuntimeError(msg)

        with pytest.raises(RuntimeError):
            asyncio.run(InteractiveTurn(crud).run(session, "request", FunctionModel(failing), limits=TurnLimits()))
        reopened = SessionCrud(
            PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2)
        )
        with pytest.raises(HeinzelError, match="uncertain outcome"):
            asyncio.run(InteractiveTurn(reopened).run(session, "request", FunctionModel(failing), limits=TurnLimits()))
        assert attempts == 1
