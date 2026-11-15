"""Models of the writes module: what agents changed, and which plugin writes may run directly."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class AgentWrite(BaseModel):
    """One write an agent asked for; the arguments only as a digest."""

    at: datetime = Field(description="When it was asked for.")
    caller: str = Field(description="Who asked: `heinzel:<session>`, `token:<key id>`, `local-agent:mcp-stdio`.")
    attended: bool = Field(description="Whether the person started that turn and watched it.")
    module: str = Field(description="The module of the operation, e.g. `system.bookmark`.")
    operation: str = Field(description="The operation, e.g. `add`.")
    target: str | None = Field(default=None, description="The id of what it changed, where the call named one.")
    arguments: str = Field(description="A digest of the call's arguments; the arguments themselves are not kept.")
    policy: Literal["direct", "propose", "approve", "never"] = Field(description="The operation's rule for agents.")
    outcome: Literal["ran", "failed", "proposed", "refused", "over-budget"] = Field(description="What happened.")


class CallerActivity(BaseModel):
    """What one agent did: for a token, the line shown beside it in Settings."""

    caller: str = Field(description="The agent, as in `AgentWrite.caller`.")
    ran: int = Field(description="Writes that ran, in the entries kept.")
    ran_today: int = Field(description="Writes that ran in the last 24 hours.")
    refused: int = Field(description="Writes refused, proposed or over budget, in the entries kept.")
    last: datetime | None = Field(default=None, description="Its latest write that ran.")


class AgentWrites(BaseModel):
    """The writes agents asked for, latest first, and a line per agent."""

    writes: list[AgentWrite] = Field(default_factory=list, description="The entries asked for, latest first.")
    callers: list[CallerActivity] = Field(default_factory=list, description="One line per agent, busiest first.")
    unattended_ran: int = Field(
        default=0, description="Writes that ran in turns nobody watched, in the last 24 hours: the digest's count."
    )


class PluginWrite(BaseModel):
    """A plugin operation that asks to run directly for agents, and whether you allowed it."""

    plugin: str = Field(description="The plugin's distribution, e.g. `heinzel-crm`.")
    module: str = Field(description="The module of the operation.")
    operation: str = Field(description="The operation.")
    summary: str = Field(description="What the operation does, in its own words.")
    allowed: bool = Field(description="Whether you allowed it to run directly; until then it is proposed.")
    capped: bool = Field(
        description="Whether hz holds it at approval whatever you allow: it writes mail, chat or a calendar."
    )


class PluginWrites(BaseModel):
    """Every plugin operation that asks to run directly for agents."""

    writes: list[PluginWrite] = Field(default_factory=list, description="The operations, by plugin.")
    next: list[str] = Field(default_factory=list, description="Commands that allow what is waiting.")
