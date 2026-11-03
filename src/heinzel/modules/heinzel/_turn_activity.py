"""Lightweight durable turn identity and redacted activity data; no model runtime is loaded."""

import hashlib
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


def interactive_turn_id(request_id: str) -> str:
    """Derive the stable durable turn identity for one accepted message request."""
    return "interactive-" + hashlib.sha256(request_id.encode()).hexdigest()


class ToolActivity(BaseModel):
    """A redacted host-observed tool invocation or denial."""

    seq: int = Field(description="Sequence of this event in the owner's control journal.")
    at: datetime = Field(description="When the host recorded this event, in UTC.")
    turn_id: str = Field(description="The exact durable turn associated with this tool event.")
    tool: str = Field(description="The host-recorded tool name, without its arguments.")
    status: Literal["requested", "completed", "failed", "denied"] = Field(
        description="Whether dispatch was requested, completed, failed, or refused by the host."
    )
