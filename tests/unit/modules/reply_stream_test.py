"""A Heinzel's turn streams its reply as the model writes it, and the journal keeps only the final message.

The model streams hz's typed outcome through Pydantic AI's output tool; `ReplyStream` relays the reply field's new
text as `text` events through the operation's `Progress` (ADR 0045, ADR 0159). A model that can't stream answers
whole, a failing relay never fails the turn, and no partial text reaches the journal (ADR 0122).
"""

import asyncio
import json
from collections.abc import AsyncIterator, Mapping

import pytest
from pydantic_ai.messages import (
    FinalResultEvent,
    ModelMessage,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    ToolCallPart,
    ToolCallPartDelta,
)
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

from heinzel import CurrentUser, UsageEntry
from heinzel.modules.heinzel._reply_stream import ReplyStream, reply_streams, reply_text, streams
from tests.unit.modules.user_chat_plugin_test import REPLY, Plugin, Settings, answer, service
from tests.unit.modules.user_chat_test import setup_chat

pytestmark = pytest.mark.unit

STREAMED = "Hello from the plugin's model, word by word"


async def chunks(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[DeltaToolCalls]:
    """The outcome's arguments in pieces of five characters, as a provider streams a tool call."""
    raw = json.dumps({"report": "Answered", "summary": STREAMED})
    name = info.output_tools[0].name
    for index in range(0, len(raw), 5):
        yield {0: DeltaToolCall(name=name if index == 0 else None, json_args=raw[index : index + 5])}


def streaming(model_id: str) -> Model:
    return FunctionModel(answer, stream_function=chunks, model_name=model_id)


class Events(list[tuple[str, dict[str, object]]]):
    """What the operation's stream received."""

    def __call__(self, kind: str, data: Mapping[str, object]) -> None:
        self.append((kind, dict(data)))

    def text(self) -> str:
        shown = ""
        for kind, data in self:
            assert kind == "text"
            shown = str(data["text"]) if data.get("replace") else shown + str(data["text"])
        return shown


def turn(tmp_path, plugin: Plugin, emit):
    current = CurrentUser(tmp_path / "identity")
    usage = list[UsageEntry]()
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        chat = service(crud, current, plugin, Settings(), usage)
        chat._emitter = lambda: emit
        replied = asyncio.run(chat.answer(session, "hello"))
        kinds = [event.kind for event in crud.repository.control_read(session).events]
        messages = crud.snapshot(session).messages
    return replied, kinds, messages, usage


def test_a_plugin_turn_streams_its_reply_piece_by_piece_and_keeps_only_the_final_message(tmp_path):
    events = Events()
    replied, kinds, messages, usage = turn(tmp_path, Plugin(model_factory=streaming), events)
    assert replied.message.text == STREAMED
    assert len(events) > 3  # word by word, not one piece
    assert events.text() == replied.message.text  # what streamed is the final message
    assert kinds.count("message.replied") == 1
    assert [m.text for m in messages if m.role == "assistant"] == [STREAMED]  # no partial reached the journal
    assert [(u.cause, u.access, u.model) for u in usage] == [("chat", "acme", "fast")]  # the guard ran on the stream


def test_a_model_that_cannot_stream_answers_whole_with_nothing_relayed(tmp_path):
    events = Events()
    replied, _kinds, _messages, usage = turn(tmp_path, Plugin(), events)
    assert replied.message.text == REPLY
    assert events == []
    assert len(usage) == 1


def test_a_failing_relay_never_fails_the_turn(tmp_path, caplog):
    def broken(kind: str, data: Mapping[str, object]) -> None:
        msg = "the browser left"
        raise OSError(msg)

    with caplog.at_level("WARNING"):
        replied, *_ = turn(tmp_path, Plugin(model_factory=streaming), broken)
    assert replied.message.text == STREAMED
    assert any(getattr(r, "event", "") == "heinzel.reply_stream.failed" for r in caplog.records)


@pytest.mark.parametrize("factory", [None, streaming])
def test_attended_turn_streams_diagnostics_without_message_content(tmp_path, factory):
    events = Events()
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        chat = service(crud, current, Plugin(**({"model_factory": factory} if factory else {})), Settings(), [])
        chat._emitter = lambda: events
        answer = asyncio.run(
            chat.dispatch(
                session, "hello", lambda: chat._owner(session, "hello"), 10, 30, user_proposals=True, attended=True
            )
        )
    assert answer.message.text == (STREAMED if factory else REPLY)
    snapshots = [data for kind, data in events if kind == "diagnostics"]
    assert snapshots
    assert snapshots[-1]["requests"] == 1
    assert snapshots[-1]["context"]["messages"] > 0
    assert REPLY not in repr(snapshots)
    assert STREAMED not in repr(snapshots)


class Whole(Model):
    """A model that answers only whole requests, like Claude Code's subscription model."""

    async def request(self, *args, **kwargs):  # pragma: no cover - never called here
        raise NotImplementedError

    @property
    def model_name(self) -> str:
        return "whole"

    @property
    def system(self) -> str:
        return "test"


def test_only_a_model_that_streams_gets_the_relay():
    events = Events()
    assert streams(streaming("fast"))
    assert not streams(FunctionModel(answer))
    assert not streams(Whole())
    assert reply_streams(events, Whole()) == []
    assert reply_streams(None, streaming("fast")) == []
    assert len(reply_streams(events, streaming("fast"))) == 1


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ('{"report": "Answered", "summary": "Good morning, ev', "Good morning, ev"),
        ('{"report": "Answ', None),
        ('{"question": "Which day?"}', "Which day?"),
        ('{"summary": "Done", "question": "Next?"}', "Done"),
        ('{"summary": ""', None),
        ("[1, 2", None),
        ("not json", None),
        ("", None),
    ],
)
def test_the_reply_is_the_summary_or_question_written_so_far(arguments, expected):
    assert reply_text(arguments) == expected


def run(relay: ReplyStream, *events) -> None:
    async def feed() -> None:
        for event in events:
            await relay.on_event(None, event=event)  # type: ignore[arg-type]

    asyncio.run(feed())


def test_only_the_output_call_is_relayed_and_a_retried_reply_starts_over():
    events = Events()
    relay = ReplyStream(events)
    run(
        relay,
        PartStartEvent(index=0, part=TextPart("thinking aloud")),
        PartStartEvent(index=1, part=ToolCallPart("use_tool", '{"summary": "not the reply', tool_call_id="tool")),
        PartDeltaEvent(index=1, delta=ToolCallPartDelta(args_delta='"}')),
        PartStartEvent(index=2, part=ToolCallPart("final_result", '{"summary": "First ', tool_call_id="out")),
        FinalResultEvent(tool_name="final_result", tool_call_id="out"),
        PartDeltaEvent(index=2, delta=ToolCallPartDelta(args_delta='try"}')),
        PartDeltaEvent(index=0, delta=ToolCallPartDelta(args_delta="ignored")),  # not a tool call
        # A retried request: a new response whose output call starts over at the same index.
        PartStartEvent(index=2, part=ToolCallPart("final_result", None, tool_call_id="again")),
        PartDeltaEvent(index=2, delta=ToolCallPartDelta(args_delta='{"summary": "Second ', tool_call_id="again")),
        FinalResultEvent(tool_name="final_result", tool_call_id="again"),
        PartDeltaEvent(index=2, delta=ToolCallPartDelta(args_delta={"summary": "Second try"})),
    )
    assert events == [
        ("text", {"text": "First "}),
        ("text", {"text": "try"}),
        ("text", {"text": "Second ", "replace": True}),
        ("text", {"text": "try"}),
    ]
    assert events.text() == "Second try"


def test_a_text_result_or_an_unknown_call_id_relays_nothing_wrong():
    events = Events()
    relay = ReplyStream(events)
    run(
        relay,
        FinalResultEvent(tool_name=None, tool_call_id=None),  # a text result: hz's outcome is never text
        PartStartEvent(index=0, part=ToolCallPart("final_result", {"summary": "Whole"}, tool_call_id="x")),
        FinalResultEvent(tool_name="final_result", tool_call_id="other"),  # falls back to the newest call
    )
    assert events == [("text", {"text": "Whole"})]
