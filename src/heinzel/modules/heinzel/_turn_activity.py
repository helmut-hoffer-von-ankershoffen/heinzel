"""Lightweight durable turn identity and redacted activity data; no model runtime is loaded."""

import hashlib
import re
from datetime import datetime
from typing import TYPE_CHECKING, Literal, cast

from pydantic import BaseModel, Field

from heinzel import HeinzelError

if TYPE_CHECKING:
    from heinzel.modules.heinzel._session_crud import SessionCrud

_MAX_ACTIVITY = 100


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


class TurnActivityProjection:
    """Project the authenticated owner's durable host events without loading a model runtime."""

    def __init__(self, crud: "SessionCrud") -> None:
        self._crud = crud
        self._owner = crud.repository.principal_id

    def activity(self, session_id: str, *, limit: int = 30) -> list[ToolActivity]:
        """Project only host-recorded, redacted tool events for the current owner.

        Raises:
            HeinzelError: If the owner, limit or event provenance is invalid.
        """
        if self._crud.repository.principal_id != self._owner:
            msg = "This turn belongs to a different authenticated owner"
            raise HeinzelError(msg)
        if type(limit) is not int or not 1 <= limit <= _MAX_ACTIVITY:
            msg = "Choose between 1 and 100 tool activity items"
            raise HeinzelError(msg)
        events = self._crud.repository.control_read(session_id).events
        starts = {
            event.turn: event.seq
            for event in events
            if event.kind == "interactive.started"
            and isinstance(event.data.get("message_id"), str)
            and event.turn == interactive_turn_id(cast("str", event.data["message_id"]))
        }
        selected = [event for event in events if event.kind in {"interactive.tool", "interactive.skill-tool"}][-limit:]
        projected: list[ToolActivity] = []
        for event in selected:
            if event.turn is None:
                msg = "A tool activity event has no durable turn provenance"
                raise HeinzelError(msg)
            name = event.data.get("tool")
            status = event.data.get("status")
            if event.kind == "interactive.skill-tool":
                if (
                    event.turn not in starts
                    or starts[event.turn] >= event.seq
                    or event.key is None
                    or not event.key.startswith("skill-turn-")
                    or name not in {"skill.propose_draft", "skill.propose_sharing"}
                    or status != "completed"
                ):
                    msg = "A skill activity event has invalid provenance"
                    raise HeinzelError(msg)
                reference = event.key.removeprefix("skill-")
                source_kind = "skill.draft" if name == "skill.propose_draft" else "skill.sharing"
                prefix = "skill-draft-" if name == "skill.propose_draft" else "skill-sharing-"
                if not any(
                    item.kind == source_kind
                    and item.key == prefix + reference
                    and starts[event.turn] < item.seq < event.seq
                    for item in events
                ):
                    msg = "A skill activity event lacks its private proposal"
                    raise HeinzelError(msg)
                if name == "skill.propose_draft":
                    self._crud.skill_draft(session_id, reference)
                else:
                    self._crud.skill_sharing(session_id, reference)
                projected.append(
                    ToolActivity(seq=event.seq, at=event.ts, turn_id=event.turn, tool=name, status="completed")
                )
                continue
            if (
                event.turn not in starts
                or starts[event.turn] >= event.seq
                or not isinstance(name, str)
                or (
                    name != "text.word_count"
                    and name not in {"unavailable", "hz.read", "connected_read", "connected_read_set"}
                    and re.fullmatch(r"[a-z][a-z0-9_.-]{0,127}:[a-z][a-z0-9-]{0,127}", name) is None
                )
                or status not in {"requested", "completed", "failed", "denied"}
                or (name == "unavailable" and status != "denied")
            ):
                msg = "A tool activity event has invalid provenance"
                raise HeinzelError(msg)
            projected.append(
                ToolActivity(
                    seq=event.seq,
                    at=event.ts,
                    turn_id=event.turn,
                    tool=name,
                    status=cast("Literal['requested', 'completed', 'failed', 'denied']", status),
                )
            )
        return projected
