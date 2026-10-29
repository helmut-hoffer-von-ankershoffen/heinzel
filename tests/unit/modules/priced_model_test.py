"""Offline Pydantic requests are counted and reserved before a fake provider can run."""

import asyncio
from decimal import Decimal

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from heinzel import CurrentUser, HeinzelError
from heinzel.modules.heinzel._engine import TurnLimits
from heinzel.modules.heinzel._interactive_turn import InteractiveTurn
from heinzel.modules.heinzel._priced_model import CHECKED, EXPIRES, MODEL, ReservedModel, resolve_pricing
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import MessageRequest, SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

pytestmark = pytest.mark.unit


class CountedModel(FunctionModel):
    def __init__(self, handler):
        super().__init__(handler)
        self.counted = []

    async def count_tokens(self, messages, model_settings, model_request_parameters):
        self.counted.append((messages, model_settings, model_request_parameters))
        return RequestUsage(input_tokens=100)


def test_policy_refuses_unknown_stale_or_unapproved_modes():
    assert resolve_pricing(MODEL, "standard", CHECKED) == (Decimal("0.40"), Decimal("1.60"))
    for model, mode, when in [
        (MODEL, "standard", EXPIRES),
        (MODEL, "priority", CHECKED),
        ("unknown", "standard", CHECKED),
    ]:
        with pytest.raises(HeinzelError, match="pricing policy"):
            resolve_pricing(model, mode, when)


def test_actual_request_count_and_reservation_block_next_generation(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    calls = []

    def model(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        calls.append(1)
        return ModelResponse([ToolCallPart("use_tool", {"name": "text.word_count", "arguments": {"text": "one two"}})])

    transport = CountedModel(model)
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = crud.new_chat("create").session_id
        crud.accept(session, MessageRequest(request_id="one", text="Count these words"))
        wrapped = ReservedModel(
            transport,
            repo,
            session,
            "turn-one",
            message_cap=Decimal("0.0004"),
            max_requests=3,
            session_cap=Decimal(1),
            owner_cap=Decimal(1),
            now=lambda: CHECKED,
        )
        with pytest.raises(HeinzelError, match="budget cap"):
            asyncio.run(
                InteractiveTurn(crud).run(session, "one", wrapped, limits=TurnLimits(requests=3, output_tokens=200))
            )
        assert calls == [1]
        assert len(transport.counted) == 2
        messages, settings, parameters = transport.counted[0]
        assert "Count these words" in str(messages)
        assert [tool.name for tool in parameters.function_tools] == ["use_tool"]
        assert settings["max_tokens"] == 200
        assert settings["max_retries"] == 0
        assert len(repo.budget_read(session).events) == 1
        # A fresh process cannot replay the already reserved provider request.
        again = ReservedModel(
            transport,
            repo,
            session,
            "turn-one",
            message_cap=Decimal(1),
            max_requests=3,
            session_cap=Decimal(1),
            owner_cap=Decimal(1),
            now=lambda: CHECKED,
        )
        with pytest.raises(HeinzelError, match="prior reservation"):
            asyncio.run(again.request(messages, {"max_tokens": 200}, parameters))
        assert calls == [1]


def test_direct_provider_factory_rejects_other_model_and_endpoint(tmp_path):
    from pydantic_ai.models.openai import OpenAIResponsesModel
    from pydantic_ai.providers.openai import OpenAIProvider

    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = crud.new_chat("create").session_id
        options = {
            "message_cap": Decimal(1),
            "max_requests": 3,
            "session_cap": Decimal(2),
            "owner_cap": Decimal(3),
            "now": lambda: CHECKED,
        }
        direct = OpenAIProvider(api_key="test-only")
        model = OpenAIResponsesModel("gpt-4.1-mini-2025-04-14", provider=direct)
        assert isinstance(ReservedModel.for_openai(model, repo, session, "turn", **options), ReservedModel)
        other = OpenAIResponsesModel("gpt-4.1-mini", provider=direct)
        with pytest.raises(HeinzelError, match="not supported"):
            ReservedModel.for_openai(other, repo, session, "turn", **options)
        fake = OpenAIProvider(api_key="test-only", base_url="https://example.com/v1")
        wrong_host = OpenAIResponsesModel("gpt-4.1-mini-2025-04-14", provider=fake)
        with pytest.raises(HeinzelError, match="not supported"):
            ReservedModel.for_openai(wrong_host, repo, session, "turn", **options)


def test_fake_provider_completes_chat_after_reservation(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    calls = []

    def reply(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(1)
        return ModelResponse([ToolCallPart(info.output_tools[0].name, {"report": "Completed safely"})])

    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = crud.new_chat("create").session_id
        crud.accept(session, MessageRequest(request_id="one", text="Hello"))
        transport = CountedModel(reply)
        wrapped = ReservedModel(
            transport,
            repo,
            session,
            "turn",
            max_requests=1,
            message_cap=Decimal("0.001"),
            session_cap=Decimal(2),
            owner_cap=Decimal(3),
            now=lambda: CHECKED,
        )
        result = asyncio.run(
            InteractiveTurn(crud).run(session, "one", wrapped, limits=TurnLimits(requests=1, output_tokens=200))
        )
        assert result.text == "Completed safely"
        assert calls == [1]
        assert len(repo.budget_read(session).events) == 1


def test_changed_authority_after_reservation_blocks_provider_call(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    checks = []
    calls = []

    def verify():
        checks.append(1)
        if len(checks) > 1:
            msg = "grant revoked"
            raise HeinzelError(msg)

    def reply(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(1)
        return ModelResponse([ToolCallPart(info.output_tools[0].name, {"report": "never"})])

    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = crud.new_chat("create").session_id
        crud.accept(session, MessageRequest(request_id="one", text="Hello"))
        wrapped = ReservedModel(
            CountedModel(reply),
            repo,
            session,
            "turn",
            max_requests=1,
            message_cap=Decimal(1),
            session_cap=Decimal(2),
            owner_cap=Decimal(3),
            now=lambda: CHECKED,
            verify_authority=verify,
        )
        with pytest.raises(HeinzelError, match="grant revoked"):
            asyncio.run(
                InteractiveTurn(crud).run(session, "one", wrapped, limits=TurnLimits(requests=1, output_tokens=200))
            )
        assert calls == []
        assert len(repo.budget_read(session).events) == 1
