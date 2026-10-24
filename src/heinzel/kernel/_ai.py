"""The AI access capability: how hz reaches AI, whatever the way (ADR 0057).

An option is one way to reach AI: the Claude Code CLI, a model API through Pydantic AI, a company gateway CLI. Its
adapter says whether it is there and signed in without spending anything (`status`), and runs one cheap live check
when asked (`test`). `[capabilities] ai_access` is the one choice; the `ai` module lists every installed option.
"""

from typing import Protocol

from pydantic import BaseModel, Field

from heinzel.kernel._agents import Billing, Effort


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
    efforts: list[Effort] = Field(
        default_factory=list[Effort], description="The efforts it can be asked for, low to max; empty when none."
    )
    billing: Billing | None = Field(
        default=None,
        description="`plan`: a subscription login, so a cost shown is what it would cost at API prices, covered by "
        "the plan; `api`: billed per token; none when it can't tell.",
    )


class AccessTest(BaseModel):
    """The outcome of one live check of an AI access option."""

    ok: bool = Field(description="Whether the option answered.")
    detail: str = Field(default="", description="What happened, e.g. the answer or why it failed.")
    fix: str = Field(default="", description="A command that fixes why it failed; often empty.")
    model: str | None = Field(default=None, description="The model that answered, when the option reports it.")
    cost_usd: float | None = Field(default=None, description="What the check cost in US dollars, when reported.")
    duration_seconds: float = Field(default=0.0, description="Wall-clock time of the check.")


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
