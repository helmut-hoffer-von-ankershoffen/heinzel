"""A made-up plugin that brings its own AI models (ADR 0159): a fixture for browser and CLI tests.

Its models answer through a `FunctionModel`, so nothing leaves the machine; one is listed but marked unavailable, as a
company proxy's model list overstates what answers.
"""

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from heinzel import ModelOffer, Module

module = Module("models-demo", "Made-up models reached through a made-up company proxy.")


def _answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    if info.output_tools:
        reply = {"report": "Answered", "summary": "Hello from the demo models"}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, reply)])
    return ModelResponse(parts=[TextPart("OK")])


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
        return FunctionModel(_answer, model_name=model_id)
