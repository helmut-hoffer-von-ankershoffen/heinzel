"""A plugin's model, run by hz's engine for one chat, draft or schedule turn (ADR 0159).

The plugin built the model with its own endpoint and its own authentication; hz never sees a token. This wrapper is
hz's side of the boundary: before every request it checks the selected access, model and owner still hold, adds the
chosen effort in the model's own style, and after every response records the tokens it reported, with no dollar cost
(a plugin's models report none). It never logs or records a header, a prompt or an answer.
"""

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings, merge_model_settings

from heinzel import UsageEntry
from heinzel.kernel import Effort, ModelOffer
from heinzel.kernel._ai_models import effort_settings


class PluginTurnModel(WrapperModel):
    """The plugin's model behind hz's continuity check, effort mapping and usage record."""

    def __init__(  # noqa: PLR0913 - each is a distinct trusted seam of the turn
        self,
        model: Model,
        *,
        access: str,
        offer: ModelOffer,
        effort: Effort | None,
        verify: Callable[[], None],
        record: Callable[[UsageEntry], None],
    ) -> None:
        """Wrap the plugin's `model` for one turn through the AI access option `access`."""
        super().__init__(model)
        self._access = access
        self._offer = offer
        self._extra: ModelSettings | None = effort_settings(offer, effort) or None  # type: ignore[assignment]
        self._verify = verify
        self._record = record

    @property
    def model_name(self) -> str:
        """The plugin's id of the model, as people picked it."""
        return self._offer.id

    @property
    def system(self) -> str:
        """The AI access option the model comes through, e.g. `acme`."""
        return self._access

    def _settings_for(self, settings: ModelSettings | None) -> ModelSettings | None:
        return merge_model_settings(settings, self._extra)

    def _keep(self, response: ModelResponse) -> None:
        usage = response.usage
        self._record(
            UsageEntry(
                at=datetime.now(UTC),
                cause="chat",
                access=self._access,
                runner=None,
                model=self._offer.id,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
                cost_usd=None,
            )
        )

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        """Check the turn still holds, then one request through the plugin's client; its tokens are recorded."""
        self._verify()
        response = await self.wrapped.request(messages, self._settings_for(model_settings), model_request_parameters)
        self._keep(response)
        return response

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        """Check the turn still holds, then one streamed request; its tokens are recorded when it ends.

        Yields:
            The plugin model's stream.
        """
        self._verify()
        async with self.wrapped.request_stream(
            messages, self._settings_for(model_settings), model_request_parameters, run_context
        ) as stream:
            yield stream
        self._keep(stream.get())
