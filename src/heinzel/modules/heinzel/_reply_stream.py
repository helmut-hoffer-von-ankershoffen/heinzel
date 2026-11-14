"""A Heinzel's reply as the model writes it: the outcome's `summary` or `question`, relayed while it streams.

A turn's model answers with hz's typed `Outcome` through Pydantic AI's output tool, so the reply people read is a
field of that tool call's arguments. This capability listens to Pydantic AI's own run events (listening is what
makes `AgentRun.next()` stream each model request), parses the output call's arguments so far as partial JSON and
relays the reply's new text through the operation's `Progress` events: `text` with `{"text": delta}`, the same event
the old general chat streamed (ADR 0045, ADR 0159). When the field being shown changes, or a retried request starts
the reply over, one `text` event carries the whole text with `"replace": true`.

Nothing here is persisted: the journal keeps only the final, validated message (ADR 0122), and a relay that fails
is logged, never a failed turn. A model that can't stream (Claude Code's subscription model) gets no capability and
answers whole, as before.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import cast

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import (
    AgentStreamEvent,
    FinalResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    ToolCallPart,
    ToolCallPartDelta,
)
from pydantic_ai.models import Model
from pydantic_ai.models.function import FunctionModel
from pydantic_core import from_json

from heinzel.kernel import Emit
from heinzel.kernel._logging import log_event
from heinzel.modules.heinzel._engine import TurnDeps

logger = logging.getLogger(__name__)

REPLY_FIELDS = ("summary", "question")
"""The outcome's fields people read, in the order the final message picks them."""


def streams(model: Model) -> bool:
    """Whether `model` answers a streamed request: it implements `request_stream`, with a function if it needs one."""
    if isinstance(model, FunctionModel):
        return model.stream_function is not None
    return type(model).request_stream is not Model.request_stream


def reply_text(arguments: str) -> str | None:
    """The reply in the output call's arguments so far, or None while none of its fields has begun."""
    try:
        value: object = from_json(arguments, allow_partial="trailing-strings")
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    fields = cast("dict[str, object]", value)
    for name in REPLY_FIELDS:
        text = fields.get(name)
        if isinstance(text, str) and text:
            return text
    return None


@dataclass
class ReplyStream(AbstractCapability[TurnDeps]):
    """Relay the reply of a streamed turn as it grows; drop everything else the model streams."""

    emit: Emit
    _arguments: dict[int, str] = field(default_factory=dict[int, str], init=False)
    _calls: dict[int, str] = field(default_factory=dict[int, str], init=False)
    _output: int | None = field(default=None, init=False)
    _sent: str = field(default="", init=False)

    async def on_event(self, ctx: RunContext[TurnDeps], *, event: AgentStreamEvent) -> None:
        """Follow the output tool call's arguments and relay the reply's new text."""
        del ctx  # The keyword names are fixed by Pydantic's public protocol.
        if isinstance(event, PartStartEvent):
            self._start(event)
        elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, ToolCallPartDelta):
            self._delta(event.index, event.delta)
        elif isinstance(event, FinalResultEvent) and event.tool_name is not None:
            self._output = next(
                (index for index, call in self._calls.items() if call == event.tool_call_id),
                max(self._arguments, default=None),
            )
            self._relay()

    def _start(self, event: PartStartEvent) -> None:
        if event.index == self._output:  # a new response reuses the index: its own output call is announced again
            self._output = None
        if isinstance(event.part, ToolCallPart):
            self._arguments[event.index] = event.part.args_as_json_str()
            self._calls[event.index] = event.part.tool_call_id
        else:
            self._arguments.pop(event.index, None)
            self._calls.pop(event.index, None)

    def _delta(self, index: int, delta: ToolCallPartDelta) -> None:
        if index not in self._arguments:
            return
        if isinstance(delta.args_delta, dict):
            self._arguments[index] = json.dumps(delta.args_delta)
        elif delta.args_delta:
            self._arguments[index] += delta.args_delta
        if delta.tool_call_id:
            self._calls[index] = delta.tool_call_id
        if index == self._output:
            self._relay()

    def _relay(self) -> None:
        if self._output is None:
            return
        text = reply_text(self._arguments.get(self._output, ""))
        if text is None or text == self._sent:
            return
        if text.startswith(self._sent):
            data: dict[str, object] = {"text": text[len(self._sent) :]}
        else:
            data = {"text": text, "replace": True}
        self._sent = text
        try:
            self.emit("text", data)
        except Exception as exc:  # noqa: BLE001 - a relay that fails never fails the turn: the final message shows
            log_event(logger, "heinzel.reply_stream.failed", level=logging.WARNING, error=type(exc).__name__)


def reply_streams(emit: Emit | None, model: Model) -> list[ReplyStream]:
    """The capability that relays `model`'s reply to `emit`, if anyone listens and the model streams; else none."""
    return [ReplyStream(emit)] if emit is not None and streams(model) else []
