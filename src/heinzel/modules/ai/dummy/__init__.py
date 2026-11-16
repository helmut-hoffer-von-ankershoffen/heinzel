"""A local, explicitly selected model for measuring the chat path (ADR 0159)."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage, ModelResponse
    from pydantic_ai.models.function import AgentInfo, FunctionModel

from heinzel import ModelOffer, ModelUnavailableError, Module

module = Module("dummy", "A local test model that always answers 42.")


def _answer(_messages: "list[ModelMessage]", info: "AgentInfo") -> "ModelResponse":
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart  # noqa: PLC0415 - optional model SDK

    if info.output_tools:
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"report": "Answered", "summary": "42"})])
    return ModelResponse(parts=[TextPart("42")])


@module.provides("ai-models", modes={"read"}, name="dummy")
class DummyModels:
    """A predictable local test model, never selected automatically."""

    title = "Dummy (always answers 42)"
    summary = "Local test model; it does not answer questions or use tools."

    def models(self) -> list[ModelOffer]:
        """Offer the single local test model."""
        return [ModelOffer(id="dummy-42", title="Dummy (42)")]

    def model(self, model_id: str) -> "FunctionModel":
        """Build the local model.

        Raises:
            ModelUnavailableError: If the model identifier is not offered.
        """
        from pydantic_ai.models.function import FunctionModel  # noqa: PLC0415 - optional model SDK

        if model_id != "dummy-42":
            raise ModelUnavailableError(model_id, ["dummy-42"])
        return FunctionModel(_answer, model_name=model_id)
