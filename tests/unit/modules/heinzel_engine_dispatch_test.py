"""A host dispatcher is the only way a turn runs a tool, and it blocks resume; offline FunctionModel throughout."""

import asyncio

import pytest
from pydantic import JsonValue
from pydantic_ai import Conversation
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from heinzel.modules.heinzel._engine import (
    EngineCheckpoint,
    TurnAuthority,
    TurnDeps,
    TurnLimits,
    run_turn,
)

pytestmark = pytest.mark.unit


def _deps() -> TurnDeps:
    return TurnDeps(
        "hz-example",
        "turn-1",
        "asked",
        TurnAuthority(
            principal_id="local-owner",
            backend_id="offline.function",
            binding_revision=1,
            grant_revision=1,
            policy_revision=1,
        ),
        contract_version=1,
        outputs=("summary",),
        done_when=("Sources present",),
    )


def _run(model: FunctionModel, checkpoints: list[EngineCheckpoint], **extra: object) -> object:
    return asyncio.run(
        run_turn(
            model,
            _deps(),
            "Hello",
            instructions="Be useful",
            limits=TurnLimits(),
            checkpoint=checkpoints.append,
            **extra,  # type: ignore[arg-type]
        )
    )


def test_dispatcher_runs_the_named_tool_with_the_models_arguments_and_the_turn_finishes() -> None:
    seen: list[tuple[str, dict[str, JsonValue]]] = []
    checkpoints: list[EngineCheckpoint] = []

    def dispatch(name: str, arguments: dict[str, JsonValue]) -> str:
        seen.append((name, arguments))
        return "tool answer"

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        assert [tool.name for tool in info.function_tools] == ["use_tool"]
        if len(messages) == 1:
            return ModelResponse([ToolCallPart("use_tool", {"name": "calendar.read", "arguments": {"day": "today"}})])
        return ModelResponse([ToolCallPart(info.output_tools[0].name, {"report": "Used the tool", "summary": "Done"})])

    result = _run(FunctionModel(model), checkpoints, dispatch=dispatch)
    assert result.status == "completed"  # type: ignore[attr-defined]
    assert seen == [("calendar.read", {"day": "today"})]
    assert checkpoints[-1].conversation.usage.tool_calls == 1


def test_a_dispatched_turn_refuses_to_resume_and_never_asks_the_model() -> None:
    calls = 0
    checkpoints: list[EngineCheckpoint] = []

    def model(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        return ModelResponse([])

    first = FunctionModel(model)
    saved = EngineCheckpoint(1, "UserPromptNode", Conversation(), "0" * 64)
    with pytest.raises(ValueError, match="cannot resume"):
        _run(first, checkpoints, dispatch=lambda _name, _args: "never", resume_from=saved)
    assert calls == 0
    assert checkpoints == []
