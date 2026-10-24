"""The AI access capability: how hz reaches AI, whatever the way (ADR 0057).

An option is one way to reach AI: the Claude Code CLI, a model API through Pydantic AI, a company gateway CLI. Its
adapter says whether it is there and signed in without spending anything (`status`), and runs one cheap live check
when asked (`test`). `[capabilities] ai_access` is the one choice; the `ai` module lists every installed option.
"""

import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

from pydantic import BaseModel, Field, model_validator

from heinzel.kernel._agents import AgentResult

if TYPE_CHECKING:
    from heinzel.kernel._kernel import Kernel


class AccessStatus(BaseModel):
    """What an AI access option says about itself without calling a model."""

    title: str = Field(description="What people pick it by, e.g. `Claude Code` or `Acme AI gateway`.")
    summary: str = Field(description="One sentence on how it reaches AI, e.g. `The Claude Code CLI and its login`.")
    installed: bool = Field(description="Whether what it needs is there, e.g. its CLI on PATH or its configuration.")
    signed_in: bool | None = Field(
        default=None, description="Whether it is signed in, as far as it can tell offline; none when it can't."
    )
    detail: str = Field(default="", description="What it found, e.g. the CLI's path or who is signed in.")
    fix: str = Field(default="", description="A command that makes it usable, e.g. `claude auth login`; often empty.")
    models: list[str] = Field(
        default_factory=list[str], description="Models it offers or is configured with, e.g. `sonnet`; may be empty."
    )
    runner: str | None = Field(
        default=None,
        description="The `agent-runner` adapter that serves its chat and coding uses, e.g. `claude-code`; none when it "
        "has no agent.",
    )


class AccessTest(BaseModel):
    """The outcome of one live check of an AI access option."""

    ok: bool = Field(description="Whether the option answered.")
    detail: str = Field(default="", description="What happened, e.g. the answer or why it failed.")
    fix: str = Field(default="", description="A command that fixes why it failed; often empty.")
    model: str | None = Field(default=None, description="The model that answered, when the option reports it.")
    cost_usd: float | None = Field(default=None, description="What the check cost in US dollars, when reported.")
    duration_seconds: float = Field(default=0.0, description="Wall-clock time of the check.")
    input_tokens: int | None = Field(default=None, description="Tokens sent, when the option reports them.")
    output_tokens: int | None = Field(default=None, description="Tokens produced, when the option reports them.")


UsageCause = Literal["chat", "ask", "scheduled", "render", "handoff", "test"]
"""Why a run spent tokens: a chat message, a one-off question, a scheduled or a manual render, a coding handoff, or a
live test of an AI access option."""


COUNTS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


class UsageEntry(BaseModel):
    """What one run spent, for `hz ai usage` and the footer meter: counts and ids only, never prompt or answer text."""

    at: datetime = Field(description="When the run ended, in UTC.")
    cause: UsageCause = Field(description="Why it ran, e.g. `chat` or `scheduled`.")
    access: str | None = Field(default=None, description="The AI access option it went through, e.g. `claude-code`.")
    runner: str | None = Field(default=None, description="The agent runner that ran it, e.g. `claude-code`.")
    model: str | None = Field(default=None, description="The model that answered, when reported.")
    heinzel: str | None = Field(default=None, description="The Heinzelmännchen it ran for, e.g. `release-notes`.")
    input_tokens: int = Field(default=0, ge=0, description="Tokens sent, without cache reads.")
    output_tokens: int = Field(default=0, ge=0, description="Tokens produced.")
    cache_read_tokens: int = Field(default=0, ge=0, description="Input tokens read from the prompt cache.")
    cache_write_tokens: int = Field(default=0, ge=0, description="Input tokens written to the prompt cache.")
    cost_usd: float | None = Field(default=None, ge=0, description="What it cost in US dollars; none when unknown.")

    @model_validator(mode="before")
    @classmethod
    def _reported(cls, data: Any) -> Any:  # noqa: ANN401 - raw input
        """What a runner or gateway reported, made safe, so building an entry never fails the run it records.

        A count that isn't a whole non-negative number is 0; a cost that isn't a finite non-negative number is unknown.
        """
        if not isinstance(data, dict):
            return data
        fields = dict(cast("dict[str, Any]", data))
        for name in COUNTS:
            value = fields.get(name)
            fields[name] = value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
        cost: object = fields.get("cost_usd")
        number = float(cost) if isinstance(cost, int | float) and not isinstance(cost, bool) else math.nan
        fields["cost_usd"] = number if math.isfinite(number) and number >= 0 else None
        return fields

    @property
    def tokens(self) -> int:
        """Every token the run moved, cache reads and writes included."""
        return self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_write_tokens

    @classmethod
    def of(cls, result: AgentResult, cause: UsageCause, heinzel: str | None = None) -> "UsageEntry":
        """The entry of an agent run, from what its runner reported."""
        usage = result.usage
        return cls(
            at=datetime.now(UTC),
            cause=cause,
            runner=result.runner or None,
            model=result.model,
            heinzel=heinzel,
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            cache_read_tokens=usage.cache_read_tokens or 0,
            cache_write_tokens=usage.cache_write_tokens or 0,
            cost_usd=usage.cost_usd,
        )


class UsageLog:
    """Where runs record what they spent; the `ai` module provides the subclass (ADR 0057).

    A base class rather than a protocol, so that the kernel can inject it by type, like `Notifications`.
    """

    def record(self, entry: UsageEntry) -> None:
        """Keep `entry`; it never raises, so recording never fails a run.

        Raises:
            NotImplementedError: Always, here; the subclass records.
        """
        raise NotImplementedError


def record_usage(kernel: "Kernel", entry: UsageEntry) -> None:
    """Record `entry` when a module provides the usage log; nothing happens when none does (the `ai` module is off)."""
    if kernel.provides(UsageLog):
        kernel.resolve(UsageLog).record(entry)


class AiAccess(Protocol):
    """One way hz reaches AI (capability `ai-access`)."""

    def status(self) -> AccessStatus:
        """Whether the option is there and signed in, without calling a model or spending anything. Mode: read."""
        ...

    def test(self) -> AccessTest:
        """Run the smallest live check the option has; this may cost a little. Mode: read.

        A failing check is a result, not an error.
        """
        ...
