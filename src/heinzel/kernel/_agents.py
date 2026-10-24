"""The agent runner capability: one bounded agent session, whatever the agent (ADR 0029).

A run has a prompt, the contract it serves, a read-only tool profile, a timeout and a budget. It reports its draft,
the tool calls it made and what it cost, in one shape for every agent, so receipts read the same. A run that exceeds
its timeout or budget is stopped and reported, never extended. Adapters call agent CLIs; the core has no agent SDK.
"""

from datetime import timedelta
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field

RunStatus = Literal["completed", "timeout", "budget", "failed"]


class ToolProfile(BaseModel):
    """The tools an agent session may use: always read-only; writes happen only in publish operations."""

    tools: list[str] = Field(
        default_factory=list,
        description="Read-only tools to allow, in the runner's own naming (e.g. `Read`, `Grep`); empty for the "
        "runner's read-only default. A runner refuses tools it doesn't know to be read-only.",
    )
    working_dir: Path | None = Field(
        default=None, description="Directory the agent works in and may read; the current one when none."
    )


class Budget(BaseModel):
    """How much a run may spend; the runner stops it when the agent lets it enforce that."""

    max_usd: float | None = Field(default=None, gt=0, description="Most a run may cost in US dollars; none: no cap.")


class TokenUsage(BaseModel):
    """What a run consumed, as far as the agent reports it; unknown values are none."""

    input_tokens: int | None = Field(default=None, description="Tokens sent to the model, without cache reads.")
    output_tokens: int | None = Field(default=None, description="Tokens the model produced.")
    cache_read_tokens: int | None = Field(default=None, description="Input tokens read from the prompt cache.")
    cache_write_tokens: int | None = Field(default=None, description="Input tokens written to the prompt cache.")
    cost_usd: float | None = Field(default=None, description="Cost of the run in US dollars, as the agent reports it.")


class AgentResult(BaseModel):
    """The outcome of one agent session, for the job's result and its receipt."""

    runner: str = Field(description="The adapter that ran it, e.g. `claude-code` or `command`.")
    model: str | None = Field(default=None, description="The model the agent reported using; none if unknown.")
    status: RunStatus = Field(
        description="`completed`; `timeout` or `budget` when stopped for exceeding a limit; `failed` otherwise."
    )
    draft: str = Field(default="", description="The text the agent produced; empty unless completed.")
    tool_calls: dict[str, int] = Field(
        default_factory=dict, description="Tool calls the agent made, by tool name; empty if the runner can't tell."
    )
    turns: int | None = Field(default=None, description="Agent turns taken, if reported.")
    usage: TokenUsage = Field(default_factory=TokenUsage, description="Tokens and cost, as far as reported.")
    duration_seconds: float = Field(description="Wall-clock time of the run.")
    detail: str = Field(default="", description="Why the run did not complete; empty when it did.")
    warnings: list[str] = Field(
        default_factory=list, description="Limits the runner could not enforce, e.g. a budget the agent ignores."
    )


class AgentRunner(Protocol):
    """Runs one bounded agent session with read-only tools (capability `agent-runner`)."""

    def run(self, prompt: str, *, contract: str, tools: ToolProfile, timeout: timedelta, budget: Budget) -> AgentResult:
        """Run `prompt` under `contract` with read-only `tools`; stop at `timeout` or `budget`. Mode: read.

        Raises:
            HeinzelError: If the runner is misconfigured (its CLI is missing, a tool isn't read-only); a session that
                fails, times out or runs over budget is a result, not an error.
        """
        ...
