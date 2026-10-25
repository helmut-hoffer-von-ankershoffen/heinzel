"""The turn loop is real, but every model response is an offline FunctionModel."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_ai import Conversation, ConversationTypeAdapter
from pydantic_ai.exceptions import RunTimedOut, UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.tools import DeferredToolRequests
from pydantic_ai.usage import RequestUsage, RunUsage

from heinzel.modules.heinzel._engine import (
    EngineCheckpoint,
    Outcome,
    TurnAuthority,
    TurnDeps,
    TurnLimits,
    new_conversation_turn,
    run_turn,
)

pytestmark = pytest.mark.unit


def _response(info: AgentInfo, payload: dict[str, object], usage: RequestUsage | None = None) -> ModelResponse:
    return ModelResponse([ToolCallPart(info.output_tools[0].name, payload)], usage=usage or RequestUsage())


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


def test_valid_turn_checkpoints_every_node_and_roundtrips_conversation() -> None:
    checkpoints: list[EngineCheckpoint] = []

    def model(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return _response(info, {"report": "Prepared a useful answer", "summary": "Hello"})

    result = asyncio.run(
        run_turn(
            FunctionModel(model),
            _deps(),
            "Hello",
            instructions="Be useful",
            limits=TurnLimits(),
            checkpoint=checkpoints.append,
        )
    )
    assert result.status == "completed"
    assert result.outcome is not None
    assert result.outcome.summary == "Hello"
    assert [checkpoint.node for checkpoint in checkpoints] == ["UserPromptNode", "ModelRequestNode", "CallToolsNode"]
    assert [checkpoint.step for checkpoint in checkpoints] == [1, 2, 3]
    assert result.checkpoints == len(checkpoints)
    assert result.conversation.usage.requests == 1
    for checkpoint in checkpoints:
        raw = ConversationTypeAdapter.dump_json(checkpoint.conversation)
        assert ConversationTypeAdapter.dump_json(ConversationTypeAdapter.validate_json(raw)) == raw
        assert isinstance(checkpoint.body(), dict)
    assert checkpoints[-1].conversation.messages == result.conversation.messages


def test_invalid_outcome_retries_then_fails_without_returning_unvalidated_draft() -> None:
    calls = 0
    checkpoints: list[EngineCheckpoint] = []

    def model(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        return _response(info, {"draft": {"text": "incomplete"}})

    result = asyncio.run(
        run_turn(
            FunctionModel(model),
            _deps(),
            "Draft",
            instructions="Be useful",
            limits=TurnLimits(),
            checkpoint=checkpoints.append,
        )
    )
    assert calls == 3
    assert result.status == "failed"
    assert result.outcome is None
    assert result.problem == "The model did not return a valid typed outcome"
    assert checkpoints[-1].node == "CallToolsNode.interrupted"
    assert result.conversation.usage.requests == 3


@pytest.mark.parametrize(
    ("limits", "usage"),
    [
        (TurnLimits(requests=1), RequestUsage()),
        (TurnLimits(total_tokens=1), RequestUsage(input_tokens=2)),
        (TurnLimits(output_tokens=1), RequestUsage(output_tokens=2)),
        (TurnLimits(cost_usd=Decimal("0.01")), RequestUsage(cost=Decimal("0.02"))),
    ],
)
def test_hard_caps_stop_before_another_request(limits: TurnLimits, usage: RequestUsage) -> None:
    calls = 0
    checkpoints: list[EngineCheckpoint] = []

    def model(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        # Invalid structured output requires another model request, which the cap must prevent.
        return _response(info, {"invalid": True}, usage)

    result = asyncio.run(
        run_turn(
            FunctionModel(model),
            _deps(),
            "Hello",
            instructions="Be useful",
            limits=limits,
            checkpoint=checkpoints.append,
        )
    )
    assert result.status == "budget"
    assert result.outcome is None
    assert calls == 1
    assert result.conversation.usage.requests == 1
    assert checkpoints[-1].node.endswith(".interrupted")


def test_conversation_usage_survives_resume_and_prevents_spending_again() -> None:
    def model(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        pytest.fail("A spent turn must not make a second request")

    conversation = Conversation(usage=RunUsage(requests=1), conversation_id="conversation-1")
    result = asyncio.run(
        run_turn(
            FunctionModel(model),
            _deps(),
            "Continue",
            instructions="Be useful",
            limits=TurnLimits(requests=1),
            checkpoint=lambda _c: None,
            conversation=conversation,
        )
    )
    assert result.status == "budget"
    assert result.conversation.conversation_id == "conversation-1"
    assert result.conversation.usage.requests == 1


def test_model_timeout_is_a_durable_result() -> None:
    checkpoints: list[EngineCheckpoint] = []

    async def model(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        await asyncio.Event().wait()
        return _response(info, {"report": "Too late"})

    result = asyncio.run(
        run_turn(
            FunctionModel(model),
            _deps(),
            "Hello",
            instructions="Be useful",
            limits=TurnLimits(timeout_seconds=0.01),
            checkpoint=checkpoints.append,
        )
    )
    assert result.status == "timeout"
    assert result.outcome is None
    assert checkpoints[-1].node == "RunTimedOut"


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("disk full"),
        ValidationError.from_exception_data("sink", []),
        UnexpectedModelBehavior("checkpoint error"),
        RunTimedOut("checkpoint error"),
    ],
)
def test_checkpoint_failure_escapes_and_no_model_runs(failure: Exception) -> None:
    def model(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        pytest.fail("The model cannot run before the input checkpoint is durable")

    def broken_sink(_checkpoint: EngineCheckpoint) -> None:
        raise failure

    with pytest.raises(type(failure)):
        asyncio.run(
            run_turn(
                FunctionModel(model),
                _deps(),
                "Hello",
                instructions="Be useful",
                limits=TurnLimits(),
                checkpoint=broken_sink,
            )
        )


@pytest.mark.parametrize(
    ("version", "met", "status"), [(1, True, "completed"), (2, True, "failed"), (1, False, "skipped")]
)
def test_contract_version_and_postconditions_gate_drafts(version: int, met: bool, status: str) -> None:
    def model(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return _response(
            info,
            {
                "report": "Drafted",
                "draft": {
                    "text": "Draft",
                    "output": "summary",
                    "contract_version": version,
                    "done_when": [{"item": "Sources present", "met": met, "evidence": "Checked"}],
                },
            },
        )

    result = asyncio.run(
        run_turn(
            FunctionModel(model),
            _deps(),
            "Draft",
            instructions="Be useful",
            limits=TurnLimits(),
            checkpoint=lambda _c: None,
        )
    )
    assert result.status == status
    if status == "failed":
        assert result.outcome is None


@pytest.mark.parametrize(
    ("output", "checks"),
    [
        ("undeclared", ["Sources present"]),
        ("summary", ["Invented condition"]),
        ("summary", ["Sources present", "Sources present"]),
    ],
)
def test_model_cannot_omit_substitute_or_duplicate_the_approved_postconditions(output: str, checks: list[str]) -> None:
    def model(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return _response(
            info,
            {
                "report": "Drafted",
                "draft": {
                    "text": "Draft",
                    "output": output,
                    "contract_version": 1,
                    "done_when": [{"item": item, "met": True, "evidence": "Checked"} for item in checks],
                },
            },
        )

    result = asyncio.run(
        run_turn(
            FunctionModel(model),
            _deps(),
            "Draft",
            instructions="Be useful",
            limits=TurnLimits(),
            checkpoint=lambda _c: None,
        )
    )
    assert result.status == "failed"
    assert result.outcome is None


def test_question_returns_waiting_without_keeping_an_engine_alive() -> None:
    def model(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return _response(info, {"report": "Need an input", "question": "Which report?"})

    result = asyncio.run(
        run_turn(
            FunctionModel(model),
            _deps(),
            "Help",
            instructions="Be useful",
            limits=TurnLimits(),
            checkpoint=lambda _c: None,
        )
    )
    assert result.status == "waiting"
    assert result.outcome is not None
    assert result.outcome.question == "Which report?"


def test_noop_requires_a_reason_and_refuses_contradictory_work() -> None:
    with pytest.raises(ValidationError, match="Nothing to do"):
        Outcome(report="No work", nothing_to_do=True)
    with pytest.raises(ValidationError, match="Nothing to do"):
        Outcome(report="No work", nothing_to_do=True, reason="Unchanged", question="Which report?")
    assert Outcome(report="No work", nothing_to_do=True, reason="No input changed").nothing_to_do


def test_model_cannot_coerce_a_truthy_number_into_a_met_postcondition() -> None:
    with pytest.raises(ValidationError, match="valid boolean"):
        Outcome.model_validate(
            {
                "report": "Drafted",
                "draft": {
                    "text": "Draft",
                    "output": "summary",
                    "contract_version": 1,
                    "done_when": [{"item": "Sources present", "met": 1, "evidence": "Checked"}],
                },
            }
        )


def test_external_cancellation_keeps_its_semantics_and_saves_completed_state() -> None:
    checkpoints: list[EngineCheckpoint] = []

    async def exercise() -> None:
        started = asyncio.Event()

        async def model(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            started.set()
            await asyncio.Event().wait()
            return _response(info, {"report": "Not reached"})

        task = asyncio.create_task(
            run_turn(
                FunctionModel(model),
                _deps(),
                "Hello",
                instructions="Be useful",
                limits=TurnLimits(),
                checkpoint=checkpoints.append,
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert checkpoints[-1].node == "RunCancelled"
    assert checkpoints[-1].conversation.messages


def test_requested_monetary_cap_refuses_unpriced_usage_instead_of_completing() -> None:
    calls = 0
    saved: list[EngineCheckpoint] = []

    def model(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        return _response(info, {"report": "Would otherwise succeed"})

    result = asyncio.run(
        run_turn(
            FunctionModel(model),
            _deps(),
            "Hello",
            instructions="Be useful",
            limits=TurnLimits(cost_usd=Decimal("0.01")),
            checkpoint=saved.append,
        )
    )
    assert calls == 1
    assert result.status == "budget"
    assert result.problem == "The model did not report priced usage"
    assert result.outcome is None
    assert result.conversation.usage.requests == 1
    assert result.conversation.usage.cost is None
    assert saved[-1].status == "budget"


def test_unpriced_saved_usage_refuses_monetary_cap_before_another_request() -> None:
    def model(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        pytest.fail("Unknown prior spending cannot be assumed to fit a hard cap")

    result = asyncio.run(
        run_turn(
            FunctionModel(model),
            _deps(),
            "Continue",
            instructions="Be useful",
            limits=TurnLimits(cost_usd=Decimal("0.01")),
            checkpoint=lambda _c: None,
            conversation=Conversation(usage=RunUsage(requests=1)),
        )
    )
    assert result.status == "budget"
    assert result.problem == "Saved usage has no priced cost"


class _SimulatedCrashError(BaseException):
    """A process dying after the sink saved a checkpoint, without returning an EngineResult."""


@pytest.mark.parametrize("crash_after", [1, 2, 3])
def test_restart_after_every_durable_node_preserves_terminal_outcome_without_repeating_model(
    tmp_path: Path, crash_after: int
) -> None:
    calls = 0
    path = tmp_path / "checkpoint.json"

    def model(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        return _response(info, {"report": "Prepared", "summary": "The exact answer"})

    def persist_then_crash(checkpoint: EngineCheckpoint) -> None:
        path.write_text(json.dumps(checkpoint.body()))
        if checkpoint.step == crash_after:
            raise _SimulatedCrashError

    with pytest.raises(_SimulatedCrashError):
        asyncio.run(
            run_turn(
                FunctionModel(model),
                _deps(),
                "Hello",
                instructions="Be useful",
                limits=TurnLimits(),
                checkpoint=persist_then_crash,
            )
        )
    saved = EngineCheckpoint.from_body(json.loads(path.read_text()))
    if crash_after == 3:
        assert saved.status == "completed"
        assert saved.outcome == Outcome(report="Prepared", summary="The exact answer")
    resumed = asyncio.run(
        run_turn(
            FunctionModel(model),
            replace(_deps(), cause="resume"),
            None,
            instructions="Be useful",
            limits=TurnLimits(),
            checkpoint=lambda _c: None,
            resume_from=saved,
        )
    )
    assert calls == 1
    assert resumed.status == "completed"
    assert resumed.outcome == Outcome(report="Prepared", summary="The exact answer")
    assert resumed.conversation.usage.requests == 1


def test_resume_refuses_raw_conversation_and_a_different_turn_context() -> None:
    saved: list[EngineCheckpoint] = []
    calls = 0

    def model(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        return _response(info, {"report": "Prepared"})

    asyncio.run(
        run_turn(
            FunctionModel(model),
            _deps(),
            "Hello",
            instructions="Be useful",
            limits=TurnLimits(),
            checkpoint=saved.append,
        )
    )
    raw = asyncio.run(
        run_turn(
            FunctionModel(model),
            replace(_deps(), cause="resume"),
            None,
            instructions="Be useful",
            limits=TurnLimits(),
            checkpoint=lambda _c: None,
            conversation=saved[-1].conversation,
        )
    )
    assert raw.status == "failed"
    changed = asyncio.run(
        run_turn(
            FunctionModel(model),
            replace(_deps(), turn_id="different-turn", cause="resume"),
            None,
            instructions="Be useful",
            limits=TurnLimits(),
            checkpoint=lambda _c: None,
            resume_from=saved[-1],
        )
    )
    assert changed.status == "failed"
    assert calls == 1


@pytest.mark.parametrize(
    "changed", ["principal_id", "backend_id", "binding_revision", "grant_revision", "policy_revision"]
)
def test_resume_is_bound_to_the_trusted_backend_and_authority_revisions(changed: str) -> None:
    saved: list[EngineCheckpoint] = []
    calls = 0

    def model(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        return _response(info, {"report": "Prepared"})

    asyncio.run(
        run_turn(
            FunctionModel(model),
            _deps(),
            "Hello",
            instructions="Be useful",
            limits=TurnLimits(),
            checkpoint=saved.append,
        )
    )
    authority = _deps().authority.model_dump()
    authority[changed] = "different" if changed.endswith("_id") else 2
    result = asyncio.run(
        run_turn(
            FunctionModel(model),
            replace(_deps(), cause="resume", authority=TurnAuthority.model_validate(authority)),
            None,
            instructions="Be useful",
            limits=TurnLimits(),
            checkpoint=lambda _c: None,
            resume_from=saved[-1],
        )
    )
    assert result.status == "failed"
    assert calls == 1


def test_new_turn_keeps_history_but_gets_its_own_request_allowance() -> None:
    calls = 0

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        assert info.model_settings is not None
        assert info.model_settings.get("max_tokens") == 8000
        if calls == 2:
            assert len(messages) > 1
        return _response(info, {"report": "Prepared"})

    first = asyncio.run(
        run_turn(
            FunctionModel(model),
            _deps(),
            "Hello",
            instructions="Be useful",
            limits=TurnLimits(requests=1),
            checkpoint=lambda _c: None,
        )
    )
    second = asyncio.run(
        run_turn(
            FunctionModel(model),
            replace(_deps(), turn_id="turn-2"),
            "Another question",
            instructions="Be useful",
            limits=TurnLimits(requests=1),
            checkpoint=lambda _c: None,
            conversation=new_conversation_turn(first.conversation),
        )
    )
    assert calls == 2
    assert first.status == "completed"
    assert second.status == "completed"
    assert first.conversation.usage.requests == 1
    assert second.conversation.usage.requests == 1


def test_new_turn_cannot_discard_a_deferred_decision_to_reset_its_budget() -> None:
    previous = Conversation(deferred_tool_requests=DeferredToolRequests(approvals=[ToolCallPart("publish", {})]))
    with pytest.raises(ValueError, match="Resolve the previous turn"):
        new_conversation_turn(previous)


def test_checkpoint_body_validates_pin_format_and_terminal_outcome() -> None:
    saved = EngineCheckpoint(3, "CallToolsNode", Conversation(), "a" * 64, "completed", Outcome(report="Prepared"))
    body = saved.body()
    assert isinstance(body, dict)
    assert body["format"] == "hz-turn-checkpoint-1"
    assert body["engine"] == "pydantic-ai:2.61.0"
    assert EngineCheckpoint.from_body(body).outcome == saved.outcome
    with pytest.raises(ValidationError):
        EngineCheckpoint.from_body({**body, "engine": "pydantic-ai:2.62.0"})
    with pytest.raises(ValidationError):
        EngineCheckpoint.from_body({**body, "format": "hz-turn-checkpoint-2"})
    with pytest.raises(ValidationError, match="terminal checkpoint"):
        EngineCheckpoint.from_body({**body, "outcome": None})


def test_conversation_serialization_golden() -> None:
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    conversation = Conversation(
        messages=[ModelRequest(parts=[UserPromptPart("Hello", timestamp=stamp)], timestamp=stamp)],
        usage=RunUsage(requests=1, input_tokens=2, output_tokens=3, cost=Decimal("0.001")),
        conversation_id="conversation-1",
    )
    raw = ConversationTypeAdapter.dump_json(conversation)
    assert raw.decode() == (
        '{"messages":[{"parts":[{"content":"Hello","timestamp":"2026-01-01T00:00:00Z","part_kind":"user-prompt"}],'
        '"timestamp":"2026-01-01T00:00:00Z","instructions":null,"kind":"request","run_id":null,'
        '"conversation_id":null,"metadata":null,"state":"complete"}],"usage":{"input_tokens":2,'
        '"cache_write_tokens":0,"cache_write_1h_tokens":0,"cache_read_tokens":0,"output_tokens":3,'
        '"output_reasoning_tokens":0,"input_audio_tokens":0,"cache_audio_read_tokens":0,"output_audio_tokens":0,'
        '"audio_seconds":0.0,"details":{},"cost":"0.001","requests":1,"tool_calls":0},'
        '"conversation_id":"conversation-1","deferred_tool_requests":null}'
    )
    assert ConversationTypeAdapter.dump_json(ConversationTypeAdapter.validate_json(raw)) == raw
