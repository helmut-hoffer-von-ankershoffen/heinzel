"""A made-up plugin that brings its own AI models (ADR 0159): a fixture for browser and CLI tests.

Its models answer through a `FunctionModel`, so nothing leaves the machine; one is listed but marked unavailable, as a
company proxy's model list overstates what answers.
"""

import json
from collections.abc import AsyncIterator

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

from heinzel import ModelOffer, Module

module = Module("models-demo", "Made-up models reached through a made-up company proxy.")


def _answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    if info.output_tools:
        reply = {"report": "Answered", "summary": "Hello from the demo models"}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, reply)])
    return ModelResponse(parts=[TextPart("OK")])


STREAMED = "Hello from the demo models, word by word"


async def _stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
    """The same answers, streamed in small pieces, as a real provider streams them."""
    if info.output_tools:
        raw = json.dumps({"report": "Answered", "summary": STREAMED})
        name = info.output_tools[0].name
        for index in range(0, len(raw), 8):
            yield {0: DeltaToolCall(name=name if index == 0 else None, json_args=raw[index : index + 8])}
        return
    for word in STREAMED.split(" "):
        yield word + " "


@module.provides("ai-models", modes={"read"}, hosts=["ai-proxy.example.invalid"], name="demo-models")
class DemoModels:
    """Three models from the made-up proxy's list."""

    title = "Demo models"
    summary = "Made-up models through a made-up company proxy, run by Heinzel."

    def models(self) -> list[ModelOffer]:
        """The proxy's list, kept in memory."""
        return [
            ModelOffer(id="demo-fast"),
            ModelOffer(id="demo-deep", thinking="adaptive", efforts=["low", "medium", "high"]),
            ModelOffer(id="demo-retired", available=False),
        ]

    def model(self, model_id: str) -> FunctionModel:
        """A model that answers locally."""
        return FunctionModel(_answer, stream_function=_stream, model_name=model_id)
