"""Offline Pydantic requests reserve worst-case bounds before a fake provider can run."""

import asyncio
import sys
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from heinzel import CurrentUser, HeinzelError
from heinzel.modules.heinzel._engine import TurnLimits
from heinzel.modules.heinzel._interactive_turn import InteractiveTurn
from heinzel.modules.heinzel._priced_model import (
    CHECKED,
    CONTEXT,
    EXPIRES,
    MODEL,
    REVISION,
    ReservedModel,
    resolve_pricing,
)
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


def test_reviewed_snapshot_window_stays_short_and_names_its_check_date():
    assert MODEL == "openai:gpt-4.1-mini-2025-04-14"
    assert timedelta(hours=72) == EXPIRES - CHECKED
    assert f"openai-gpt-4.1-mini-standard-{CHECKED:%Y-%m-%d}" == REVISION
    with pytest.raises(HeinzelError, match="pricing policy"):
        resolve_pricing(MODEL, "standard", CHECKED - timedelta(microseconds=1))
    assert resolve_pricing(MODEL, "standard", EXPIRES - timedelta(microseconds=1)) == (Decimal("0.40"), Decimal("1.60"))


def test_worst_case_tokens_and_dollars_are_reserved_before_any_provider_work(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    calls = []
    transport = CountedModel(lambda *_: calls.append(1) or ModelResponse([]))
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        session = (
            SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=1)).new_chat("new").session_id
        )
        options = {
            "message_cap": Decimal(2),
            "max_requests": 2,
            "session_cap": Decimal(3),
            "owner_cap": Decimal(3),
            "now": lambda: CHECKED,
        }
        messages = [ModelRequest(parts=[UserPromptPart(content="private text")])]
        tiny = ReservedModel(
            transport, repo, session, "small", max_turn_tokens=32_000, max_turn_output_tokens=400, **options
        )
        with pytest.raises(HeinzelError, match="token cap"):
            asyncio.run(tiny.request(messages, {"max_tokens": 200}, ModelRequestParameters()))
        assert calls == []
        assert transport.counted == []
        assert repo.budget_read(session).events == []
        bounded = ReservedModel(
            transport, repo, session, "bounded", max_turn_tokens=CONTEXT + 200, max_turn_output_tokens=400, **options
        )
        asyncio.run(bounded.request(messages, {"max_tokens": 200}, ModelRequestParameters()))
        with pytest.raises(HeinzelError, match="token cap"):
            asyncio.run(bounded.request(messages, {"max_tokens": 200}, ModelRequestParameters()))
        assert calls == [1]
        assert transport.counted == []
        output_bound = ReservedModel(
            transport, repo, session, "output", max_turn_tokens=3_000_000, max_turn_output_tokens=200, **options
        )
        asyncio.run(output_bound.request(messages, {"max_tokens": 200}, ModelRequestParameters()))
        asyncio.run(output_bound.request(messages, {"max_tokens": 200}, ModelRequestParameters()))
        with pytest.raises(HeinzelError, match="request limit"):
            asyncio.run(output_bound.request(messages, {"max_tokens": 200}, ModelRequestParameters()))
        assert calls == [1, 1, 1]
        assert transport.counted == []
        unsupported = ReservedModel(
            transport, repo, session, "image", max_turn_tokens=2_000_000, max_turn_output_tokens=200, **options
        )
        image = ModelRequest(parts=[UserPromptPart(content=[BinaryContent(data=b"image", media_type="image/png")])])
        with pytest.raises(HeinzelError, match="text-only"):
            asyncio.run(unsupported.request([image], {"max_tokens": 200}, ModelRequestParameters()))
        assert calls == [1, 1, 1]
        assert transport.counted == []


def test_actual_request_count_and_reservation_block_next_generation(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    calls = []
    captured = []

    def model(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        calls.append(1)
        captured.append(_messages)
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
            message_cap=Decimal("0.5"),
            max_requests=3,
            max_turn_tokens=3_200_000,
            max_turn_output_tokens=600,
            session_cap=Decimal(1),
            owner_cap=Decimal(1),
            now=lambda: CHECKED,
        )
        with pytest.raises(HeinzelError, match="budget cap"):
            asyncio.run(
                InteractiveTurn(crud).run(session, "one", wrapped, limits=TurnLimits(requests=3, output_tokens=200))
            )
        assert calls == [1]
        assert transport.counted == []
        messages = captured[0]
        parameters = ModelRequestParameters()
        assert "Count these words" in str(messages)
        assert len(repo.budget_read(session).events) == 1
        # A fresh process cannot replay the already reserved provider request.
        again = ReservedModel(
            transport,
            repo,
            session,
            "turn-one",
            message_cap=Decimal(1),
            max_requests=3,
            max_turn_tokens=3_200_000,
            max_turn_output_tokens=600,
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
            "max_turn_tokens": 3_200_000,
            "max_turn_output_tokens": 600,
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
            max_turn_tokens=2_000_000,
            max_turn_output_tokens=200,
            message_cap=Decimal("0.5"),
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


def test_two_step_tool_turn_reserves_separate_output_allocations_and_replays_without_calls(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    allocations = []

    def reply(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        allocations.append(info.model_settings["max_tokens"])
        if len(allocations) == 1:
            return ModelResponse(
                [ToolCallPart("use_tool", {"name": "text.word_count", "arguments": {"text": "one two"}})]
            )
        return ModelResponse([ToolCallPart(info.output_tools[0].name, {"report": "Two words."})])

    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=3, message_max_usd=2))
        session = crud.new_chat("two-step").session_id
        crud.accept(session, MessageRequest(request_id="one", text="Count the words"))
        model = CountedModel(reply)
        wrapped = ReservedModel(
            model,
            repo,
            session,
            "turn",
            max_requests=2,
            max_turn_tokens=2 * CONTEXT + 200,
            max_turn_output_tokens=200,
            message_cap=Decimal(1),
            session_cap=Decimal(2),
            owner_cap=Decimal(3),
            now=lambda: CHECKED,
        )
        runner = InteractiveTurn(crud)
        limits = TurnLimits(requests=2, total_tokens=2 * CONTEXT + 200, output_tokens=200)
        result = asyncio.run(runner.run(session, "one", wrapped, limits=limits))
        assert result.text == "Two words."
        assert allocations == [100, 100]
        assert model.counted == []
        reservations = repo.budget_read(session).events
        assert len(reservations) == 2
        assert [event.data["envelope"]["max_output_tokens"] for event in reservations] == [100, 100]
        assert asyncio.run(runner.run(session, "one", wrapped, limits=limits)) == result
        assert allocations == [100, 100]


def test_direct_responses_payload_enforces_the_reserved_output_allocation(tmp_path, monkeypatch):
    import httpx2
    import pydantic_ai.models
    from pydantic_ai.models.openai import OpenAIResponsesModel
    from pydantic_ai.providers.openai import OpenAIProvider

    from heinzel.modules.heinzel._budget_reservation import initialize_budget

    monkeypatch.setattr(pydantic_ai.models, "ALLOW_MODEL_REQUESTS", True)
    bodies = []

    def reply(request):
        import json

        bodies.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "id": "resp-test",
                "object": "response",
                "created_at": 1793300000,
                "status": "completed",
                "model": "gpt-4.1-mini-2025-04-14",
                "output": [
                    {
                        "id": "msg-test",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "ok", "annotations": []}],
                    }
                ],
                "usage": {
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "total_tokens": 2,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(reply))
    model = OpenAIResponsesModel(
        "gpt-4.1-mini-2025-04-14", provider=OpenAIProvider(api_key="test-only", http_client=client)
    )
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        session = (
            SessionCrud(repo, lambda: SessionCosts(session_max_usd=3, message_max_usd=2)).new_chat("wire").session_id
        )
        initialize_budget(repo, session)
        wrapped = ReservedModel.for_openai(
            model,
            repo,
            session,
            "wire-turn",
            max_requests=2,
            max_turn_tokens=2 * CONTEXT + 200,
            max_turn_output_tokens=200,
            message_cap=Decimal(1),
            session_cap=Decimal(2),
            owner_cap=Decimal(3),
            now=lambda: CHECKED,
        )
        messages = [ModelRequest(parts=[UserPromptPart(content="test")])]
        asyncio.run(wrapped.request(messages, {"max_tokens": 200}, ModelRequestParameters()))
        asyncio.run(wrapped.request(messages, {"max_tokens": 200}, ModelRequestParameters()))
        assert [body["max_output_tokens"] for body in bodies] == [100, 100]
        assert len(repo.budget_read(session).events) == 3  # initialization and two reservations
    asyncio.run(client.aclose())


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
            max_turn_tokens=2_000_000,
            max_turn_output_tokens=200,
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


def test_expired_price_after_reservation_blocks_provider_call(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    calls = []
    clock_calls = []

    def clock():
        clock_calls.append(1)
        return CHECKED if len(clock_calls) <= 3 else EXPIRES

    model = CountedModel(lambda *_: calls.append(1) or ModelResponse([]))
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        session = (
            SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=1)).new_chat("one").session_id
        )
        wrapped = ReservedModel(
            model,
            repo,
            session,
            "turn",
            message_cap=Decimal(1),
            max_requests=1,
            max_turn_tokens=2_000_000,
            max_turn_output_tokens=200,
            session_cap=Decimal(2),
            owner_cap=Decimal(3),
            now=clock,
        )
        with pytest.raises(HeinzelError, match="pricing policy"):
            asyncio.run(
                wrapped.request(
                    [ModelRequest(parts=[UserPromptPart(content="private")])],
                    {"max_tokens": 200},
                    ModelRequestParameters(),
                )
            )
        assert calls == []
        assert model.counted == []
        assert len(repo.budget_read(session).events) == 1


def test_modern_retry_feedback_remains_text_only_and_reserved(tmp_path):
    from pydantic_ai.messages import RetryFeedbackPart, RetryInfo, ToolReturnPart

    current = CurrentUser(tmp_path / "identity")
    calls = []

    class FakeModel(CountedModel):
        async def request(self, *_args):
            calls.append(1)
            return ModelResponse([])

    model = FakeModel(lambda *_: ModelResponse([]))
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        session = (
            SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=1)).new_chat("retry").session_id
        )
        wrapped = ReservedModel(
            model,
            repo,
            session,
            "retry",
            message_cap=Decimal(2),
            max_requests=2,
            max_turn_tokens=3_000_000,
            max_turn_output_tokens=400,
            session_cap=Decimal(3),
            owner_cap=Decimal(3),
            now=lambda: CHECKED,
        )
        retry = RetryFeedbackPart(content="Correct the response", retry=RetryInfo(cause="no_output"))
        tool_retry = ToolReturnPart(tool_name="test", content="Try again", outcome="retried")
        asyncio.run(
            wrapped.request([ModelRequest(parts=[retry, tool_retry])], {"max_tokens": 200}, ModelRequestParameters())
        )
        assert calls == [1]
        assert len(repo.budget_read(session).events) > 0
        structured = RetryFeedbackPart(
            content=[{"type": "value_error", "loc": (), "input": "private", "msg": "bad"}],
            retry=RetryInfo(cause="validation_error"),
        )
        with pytest.raises(HeinzelError, match="text-only"):
            asyncio.run(
                wrapped.request([ModelRequest(parts=[structured])], {"max_tokens": 200}, ModelRequestParameters())
            )
        assert calls == [1]


@contextmanager
def _reserved(tmp_path, **overrides):
    """A ReservedModel over a fake provider, the repository and its session, with generous budgets."""
    current = CurrentUser(tmp_path / "identity")
    calls = []
    transport = CountedModel(lambda *_: calls.append(1) or ModelResponse([]))
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        session = (
            SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=1)).new_chat("new").session_id
        )
        options = {
            "message_cap": Decimal(2),
            "max_requests": 3,
            "max_turn_tokens": 3_200_000,
            "max_turn_output_tokens": 600,
            "session_cap": Decimal(3),
            "owner_cap": Decimal(3),
            "now": lambda: CHECKED,
            **overrides,
        }
        yield repo, session, calls, ReservedModel(transport, repo, session, "turn", **options)


def _ask(model, messages=None, settings=None, parameters=None):
    messages = messages or [ModelRequest(parts=[UserPromptPart(content="hello")])]
    settings = {"max_tokens": 200} if settings is None else settings
    return asyncio.run(model.request(messages, settings, parameters or ModelRequestParameters()))


@pytest.mark.parametrize("requests", [0, 101, True])
def test_the_request_count_must_be_a_bounded_integer(tmp_path, requests):
    with pytest.raises(HeinzelError, match="bounded request count"), _reserved(tmp_path, max_requests=requests):
        pass


def test_the_openai_provider_must_be_installed_for_the_pinned_model(tmp_path, monkeypatch):
    with _reserved(tmp_path) as (repo, session, _calls, model):
        monkeypatch.setitem(sys.modules, "pydantic_ai.models.openai", None)
        options = {
            "message_cap": Decimal(1),
            "max_requests": 3,
            "max_turn_tokens": 3_200_000,
            "max_turn_output_tokens": 600,
            "session_cap": Decimal(2),
            "owner_cap": Decimal(3),
            "now": lambda: CHECKED,
        }
        with pytest.raises(HeinzelError, match="provider is not installed"):
            ReservedModel.for_openai(model, repo, session, "turn", **options)


def test_a_reservation_belongs_to_the_owner_that_made_it(tmp_path, monkeypatch):
    with _reserved(tmp_path) as (repo, _session, calls, model):
        monkeypatch.setattr(type(repo), "principal_id", property(lambda _self: "someone-else"))
        with pytest.raises(HeinzelError, match="different owner"):
            _ask(model)
        assert calls == []


def test_authority_is_verified_before_and_after_the_reservation_without_a_fence(tmp_path):
    verified = []
    with _reserved(tmp_path, verify_authority=lambda: verified.append(1)) as (_repo, _session, calls, model):
        _ask(model)
        assert (verified, calls) == ([1, 1], [1])


@pytest.mark.parametrize(
    ("settings", "parameters", "match"),
    [
        ({"max_tokens": 200, "temperature": 1.0}, None, "unsupported provider settings"),
        ({"max_tokens": 200}, SimpleNamespace(native_tools=[object()]), "unsupported provider settings"),
        ({}, None, "explicit supported output-token bound"),
        ({"max_tokens": 0}, None, "explicit supported output-token bound"),
        ({"max_tokens": 32_769}, None, "explicit supported output-token bound"),
    ],
)
def test_unsupported_settings_and_unbounded_output_are_refused_before_any_provider_work(
    tmp_path, settings, parameters, match
):
    with _reserved(tmp_path) as (_repo, _session, calls, model):
        with pytest.raises(HeinzelError, match=match):
            _ask(model, settings=settings, parameters=parameters)
        assert calls == []


def test_only_text_and_tool_calls_may_appear_in_earlier_responses(tmp_path):
    with _reserved(tmp_path) as (_repo, _session, calls, model):
        thought = ModelResponse(parts=[ThinkingPart(content="private")])
        with pytest.raises(HeinzelError, match="text-only"):
            _ask(model, messages=[thought])
        with pytest.raises(HeinzelError, match="text-only"):
            _ask(model, messages=["not a model message"])
        assert calls == []
        answered = ModelResponse(parts=[TextPart(content="hi"), ToolCallPart(tool_name="t", args={})])
        _ask(model, messages=[answered])
        assert calls == [1]
