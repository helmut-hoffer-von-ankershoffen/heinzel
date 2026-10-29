"""One bounded, typed Pydantic turn; persistence is supplied by the session service.

The engine never publishes an outcome. A checkpoint sink must durably save each snapshot before returning; a sink
failure escapes and stops the turn. Only an already policy-checked Model belongs here (ADR 0050, ADR 0067).
"""

import asyncio
import hashlib
import json
from collections.abc import Callable
from copy import copy
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, model_validator
from pydantic_ai import Agent, Conversation, ConversationTypeAdapter, ModelRequestContext, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import RunCancelled, RunTimedOut, UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model
from pydantic_ai.run import AgentRun
from pydantic_ai.usage import RunUsage, UsageLimits


class DoneCheck(BaseModel):
    """A named postcondition; one unmet condition prevents placement of a draft."""

    model_config = ConfigDict(extra="forbid", strict=True)
    item: str = Field(min_length=1, max_length=2000)
    met: bool
    evidence: str = Field(min_length=1, max_length=4000)


class TurnDraft(BaseModel):
    """An unplaced draft tied to the exact approved contract version."""

    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, max_length=100_000)
    output: str = Field(min_length=1, max_length=128)
    contract_version: int = Field(ge=1)
    done_when: list[DoneCheck] = Field(min_length=1)


class TurnLesson(BaseModel):
    """A visible suggested improvement; it cannot silently change the contract."""

    model_config = ConfigDict(extra="forbid", strict=True)
    sentence: str = Field(min_length=1, max_length=4000)
    hunks: list[str] = Field(default_factory=list)


class TurnAmendment(BaseModel):
    """Proposed OpenProse text for a later explicit approval."""

    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, max_length=100_000)
    reason: str = Field(min_length=1, max_length=4000)


class Outcome(BaseModel):
    """Validated content to project into a session; no field authorizes an effect."""

    model_config = ConfigDict(extra="forbid", strict=True)
    report: str = Field(min_length=1, max_length=2000)
    draft: TurnDraft | None = None
    lessons: list[TurnLesson] = Field(default_factory=list[TurnLesson])
    amendment: TurnAmendment | None = None
    question: str | None = Field(default=None, min_length=1, max_length=8000)
    summary: str | None = Field(default=None, min_length=1, max_length=8000)
    nothing_to_do: bool = False
    reason: str | None = Field(default=None, min_length=1, max_length=4000)

    @model_validator(mode="after")
    def explicit_noop(self) -> "Outcome":
        """A no-op needs an explanation and cannot also request work.

        Raises:
            ValueError: If a no-op is unexplained or contradicts its proposed work.
        """
        if self.nothing_to_do and (not self.reason or self.draft or self.amendment or self.question):
            msg = "Nothing to do needs a reason and cannot include a draft, amendment or question"
            raise ValueError(msg)
        return self


class TurnAuthority(BaseModel):
    """Trusted service revisions and backend identity; a model never supplies or widens these values."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    principal_id: str = Field(min_length=1)
    backend_id: str = Field(min_length=1)
    binding_revision: int = Field(ge=1)
    grant_revision: int = Field(ge=1)
    policy_revision: int = Field(ge=1)


@dataclass(frozen=True)
class TurnDeps:
    """The application identity and frozen inputs for this bounded turn."""

    session_id: str
    turn_id: str
    cause: Literal["asked", "scheduled", "manual", "answer", "approval", "resume"]
    authority: TurnAuthority
    contract_version: int | None = None
    inputs: dict[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    outputs: tuple[str, ...] = ()
    done_when: tuple[str, ...] = ()


class TurnLimits(BaseModel):
    """Hard request/token/time bounds; a cost bound additionally needs priced model usage."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    requests: int = Field(default=20, ge=1, le=100)
    tool_calls: int = Field(default=50, ge=1, le=1000)
    total_tokens: int | None = Field(default=32_000, ge=1)
    output_tokens: int | None = Field(default=8000, ge=1)
    timeout_seconds: float = Field(default=120, gt=0, le=7200, allow_inf_nan=False)
    cost_usd: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)

    def usage_limits(self) -> UsageLimits:
        """Keep Pydantic's accounting across resumed Conversation snapshots."""
        return UsageLimits(
            request_limit=self.requests,
            tool_calls_limit=self.tool_calls,
            total_tokens_limit=self.total_tokens,
            output_tokens_limit=self.output_tokens,
            cost_limit=self.cost_usd,
        )


TurnStatus = Literal["completed", "skipped", "waiting", "failed", "budget", "timeout", "cancelled"]


class _SavedCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    format: Literal["hz-turn-checkpoint-1"] = "hz-turn-checkpoint-1"
    engine: Literal["pydantic-ai:2.61.0"] = "pydantic-ai:2.61.0"
    step: int = Field(ge=1)
    node: str = Field(min_length=1, max_length=128)
    context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    conversation: dict[str, JsonValue]
    status: TurnStatus | None = None
    outcome: Outcome | None = None
    problem: str | None = None

    @model_validator(mode="after")
    def terminal_outcome(self) -> "_SavedCheckpoint":
        if (self.status is None and self.outcome is not None) or (
            self.status in {"completed", "skipped", "waiting"} and self.outcome is None
        ):
            msg = "A terminal checkpoint must preserve its typed outcome"
            raise ValueError(msg)
        return self


@dataclass(frozen=True)
class EngineCheckpoint:
    """A private body snapshot after a completed node; never put its messages in spine metadata."""

    step: int
    node: str
    conversation: Conversation
    context_hash: str
    status: TurnStatus | None = None
    outcome: Outcome | None = None
    problem: str | None = None

    def body(self) -> JsonValue:
        """Use the pinned Conversation schema rather than a hand-written vendor message translation."""
        messages = TypeAdapter(dict[str, JsonValue]).validate_json(ConversationTypeAdapter.dump_json(self.conversation))
        saved = _SavedCheckpoint(
            step=self.step,
            node=self.node,
            conversation=messages,
            context_hash=self.context_hash,
            status=self.status,
            outcome=self.outcome,
            problem=self.problem,
        )
        return TypeAdapter(JsonValue).validate_python(saved.model_dump(mode="json"))

    @classmethod
    def from_body(cls, value: JsonValue) -> "EngineCheckpoint":
        """Validate a body whose exact bytes the session service already checked against its spine hash."""
        saved = _SavedCheckpoint.model_validate(value)
        return cls(
            saved.step,
            saved.node,
            ConversationTypeAdapter.validate_json(json.dumps(saved.conversation)),
            saved.context_hash,
            saved.status,
            saved.outcome,
            saved.problem,
        )


@dataclass(frozen=True)
class EngineResult:
    """A typed result for the service to persist, validate against its contract and present."""

    status: TurnStatus
    conversation: Conversation
    checkpoints: int
    outcome: Outcome | None = None
    problem: str | None = None


@dataclass
class _Accounting(AbstractCapability[TurnDeps]):
    """Keep a response that hit a cap before Pydantic appended it or incremented its request counter."""

    previous_requests: int
    cost_limit: Decimal | None
    responses: int = 0
    last_response: ModelResponse | None = None
    unpriced: bool = False

    async def after_model_request(
        self, ctx: RunContext[TurnDeps], *, request_context: ModelRequestContext, response: ModelResponse
    ) -> ModelResponse:
        del ctx, request_context  # These keyword names are fixed by Pydantic's public protocol.
        self.responses += 1
        self.last_response = response
        if self.cost_limit is not None and response.usage.cost is None:
            self.unpriced = True
            msg = "The model did not report priced usage for the requested monetary cap"
            raise UsageLimitExceeded(msg)
        return response

    def conversation(self, messages: list[ModelMessage], usage: RunUsage, identity: str) -> Conversation:
        result = Conversation(messages=list(messages), usage=copy(usage), conversation_id=identity)
        result.usage.requests = max(result.usage.requests, self.previous_requests + self.responses)
        if self.last_response is not None and not any(message is self.last_response for message in messages):
            result.messages.append(self.last_response)
        return result


class _SinkFailureError(Exception):
    """Keep a storage exception out of Pydantic's cancellation translation."""

    def __init__(self, error: Exception) -> None:
        super().__init__("A turn checkpoint could not be persisted")
        self.error = error


@dataclass
class _Progress:
    current: Conversation
    sink: Callable[[EngineCheckpoint], None]
    accounting: _Accounting
    context_hash: str
    steps: int = 0

    def save(
        self,
        node: str,
        messages: list[ModelMessage],
        usage: RunUsage,
        identity: str,
        terminal: EngineResult | None = None,
    ) -> None:
        self.steps += 1
        self.current = self.accounting.conversation(messages, usage, identity)
        try:
            self.sink(
                EngineCheckpoint(
                    self.steps,
                    node,
                    self.current,
                    self.context_hash,
                    terminal.status if terminal else None,
                    terminal.outcome if terminal else None,
                    terminal.problem if terminal else None,
                )
            )
        except Exception as exc:
            raise _SinkFailureError(exc) from exc

    def cancelled(self, state: RunCancelled, status: Literal["timeout", "cancelled"]) -> EngineResult:
        terminal = EngineResult(status, self.current, self.steps + 1, problem=f"The turn was {status}")
        self.save(
            type(state).__name__,
            state.all_messages(),
            state.usage,
            state.conversation_id or self.current.conversation_id,
            terminal,
        )
        return EngineResult(status, self.current, self.steps, problem=f"The turn was {status}")


def build_agent(
    model: Model, instructions: str, dispatch: Callable[[str, dict[str, JsonValue]], str] | None = None
) -> Agent[TurnDeps, Outcome]:
    """Build a typed turn; only an explicitly supplied host dispatcher can execute a tool."""
    agent = Agent(model, output_type=Outcome, deps_type=TurnDeps, instructions=instructions, retries=2)
    if dispatch is not None:

        def use_tool(name: str, arguments: dict[str, JsonValue]) -> str:
            """Request one host-controlled tool by name with JSON arguments."""
            return dispatch(name, arguments)

        agent.tool_plain(use_tool)
    return agent


def new_conversation_turn(previous: Conversation) -> Conversation:
    """Carry visible history into a new logical turn with a fresh per-turn usage baseline.

    Raises:
        ValueError: If a deferred decision still belongs to the previous turn.
    """
    if previous.deferred_tool_requests is not None:
        msg = "Resolve the previous turn's deferred decision before starting another turn"
        raise ValueError(msg)
    return Conversation(messages=list(previous.messages), conversation_id=previous.conversation_id)


def _finished(outcome: Outcome, deps: TurnDeps, current: Conversation, steps: int) -> EngineResult:
    if outcome.draft is not None:
        if outcome.draft.contract_version != deps.contract_version:
            return EngineResult("failed", current, steps, problem="The draft names a different contract version")
        if outcome.draft.output not in deps.outputs:
            return EngineResult("failed", current, steps, problem="The draft output is not declared by the contract")
        checked = [check.item for check in outcome.draft.done_when]
        if not deps.done_when or len(set(checked)) != len(checked) or set(checked) != set(deps.done_when):
            return EngineResult(
                "failed", current, steps, problem="Draft checks do not match the contract postconditions"
            )
        if not all(check.met for check in outcome.draft.done_when):
            return EngineResult("skipped", current, steps, outcome=outcome, problem="A draft postcondition is unmet")
    return EngineResult("waiting" if outcome.question else "completed", current, steps, outcome)


def _context_hash(model: Model, deps: TurnDeps, instructions: str, limits: TurnLimits) -> str:
    context = asdict(deps)
    context.pop("cause")  # `resume` is a dispatch cause, not a new logical turn or authority revision.
    context["authority"] = deps.authority.model_dump(mode="json")
    context.update({"instructions": instructions, "limits": limits.model_dump(mode="json"), "model": model.model_name})
    return hashlib.sha256(json.dumps(context, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _restore(
    saved: EngineCheckpoint | None, context_hash: str, conversation: Conversation | None, prompt: str | None, cause: str
) -> Conversation | EngineResult:
    if saved is not None:
        if saved.context_hash != context_hash or conversation is not None or prompt is not None:
            return EngineResult(
                "failed",
                saved.conversation,
                saved.step,
                problem="Resume requires the saved turn context, with no new prompt",
            )
        if saved.status is not None:
            return EngineResult(saved.status, saved.conversation, saved.step, saved.outcome, saved.problem)
        return saved.conversation
    if cause == "resume":
        return EngineResult(
            "failed",
            conversation or Conversation(),
            0,
            problem="Resume requires a verified engine checkpoint, not only Conversation history",
        )
    return conversation or Conversation()


async def run_turn(  # noqa: PLR0913 - explicit inputs and persistence policy for one bounded turn
    model: Model,
    deps: TurnDeps,
    prompt: str | None,
    *,
    instructions: str,
    limits: TurnLimits,
    checkpoint: Callable[[EngineCheckpoint], None],
    conversation: Conversation | None = None,
    resume_from: EngineCheckpoint | None = None,
    dispatch: Callable[[str, dict[str, JsonValue]], str] | None = None,
) -> EngineResult:
    """Drive and checkpoint every node, retaining completed state when a cap or model validation fails.

    External task cancellation keeps propagating. A checkpoint failure also escapes; the caller must not mark the
    turn successful when persistence failed. This slice exposes no world-writing tool.

    Raises:
        asyncio.CancelledError: If the caller cancels the task; a completed partial snapshot is retained first.
        ValueError: If a dispatched turn attempts an unsupported resume.
    """
    if dispatch is not None and resume_from is not None:
        msg = "Tool-dispatched turns cannot resume until tool authority can be revalidated"
        raise ValueError(msg)
    context_hash = _context_hash(model, deps, instructions, limits)
    restored = _restore(resume_from, context_hash, conversation, prompt, deps.cause)
    if isinstance(restored, EngineResult):
        return restored
    current = restored
    accounting = _Accounting(current.usage.requests, limits.cost_usd)
    progress = _Progress(current, checkpoint, accounting, context_hash, resume_from.step if resume_from else 0)
    if limits.cost_usd is not None and current.usage.requests > 0 and current.usage.cost is None:
        terminal = EngineResult("budget", current, progress.steps + 1, problem="Saved usage has no priced cost")
        progress.save("UnpricedUsage", current.messages, current.usage, current.conversation_id, terminal)
        return EngineResult("budget", progress.current, progress.steps, problem=terminal.problem)
    agent = build_agent(model, instructions, dispatch)
    try:
        async with agent.iter(
            prompt,
            conversation=current,
            deps=deps,
            usage_limits=limits.usage_limits(),
            timeout=limits.timeout_seconds,
            capabilities=[accounting],
            model_settings={"max_tokens": limits.output_tokens} if limits.output_tokens is not None else None,
        ) as run:
            return await _drive(run, deps, progress)
    except _SinkFailureError as exc:
        raise exc.error from exc
    except RunTimedOut as exc:
        return progress.cancelled(exc, "timeout")
    except RunCancelled as exc:
        return progress.cancelled(exc, "cancelled")
    except asyncio.CancelledError as exc:
        if state := RunCancelled.from_cancellation(exc):
            progress.cancelled(state, "cancelled")
        raise


async def _drive(run: AgentRun[TurnDeps, Outcome], deps: TurnDeps, progress: _Progress) -> EngineResult:
    node = run.next_node
    while not Agent.is_end_node(node):
        executed = type(node).__name__
        failure: tuple[TurnStatus, str] | None = None
        try:
            node = await run.next(node)
        except UsageLimitExceeded:
            failure = (
                "budget",
                "The model did not report priced usage"
                if progress.accounting.unpriced
                else "The turn reached its request, token or cost limit",
            )
        except UnexpectedModelBehavior:
            failure = ("failed", "The model did not return a valid typed outcome")
        # Keep the sink outside model exception handlers: a storage failure escapes rather than becoming a result.
        terminal: EngineResult | None = None
        if failure:
            terminal = EngineResult(failure[0], progress.current, progress.steps + 1, problem=failure[1])
        elif Agent.is_end_node(node):
            result = run.result
            terminal = (
                _finished(Outcome.model_validate(result.output), deps, result.conversation, progress.steps + 1)
                if result is not None
                else EngineResult(
                    "failed", progress.current, progress.steps + 1, problem="The turn ended without an outcome"
                )
            )
        progress.save(
            f"{executed}.interrupted" if failure else executed,
            run.all_messages(),
            run.usage,
            run.conversation_id,
            terminal,
        )
        if terminal:
            return EngineResult(terminal.status, progress.current, progress.steps, terminal.outcome, terminal.problem)
    return EngineResult("failed", progress.current, progress.steps, problem="The turn ended without an outcome")
