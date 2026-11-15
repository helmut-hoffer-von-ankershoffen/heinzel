"""hz's side of a turn on a plugin's model: a Pydantic AI capability, never a wrapper around the model (ADR 0159).

The plugin built a plain `pydantic_ai.models.Model`; hz's engine runs it with Pydantic AI's own `Agent`. This
capability hooks each model request: before it, it checks (off the event loop, without calling the plugin) that what
hz owns still holds: the owner, the access, the chosen model and effort, the budget mode and the plugin's version.
It adds the chosen effort in the model's own style and hz's output cap. After a response it records the tokens the
model reported, with no dollar cost; a usage log that fails is logged, never a failed answer. A failure becomes a
clean `PluginModelError` that never repeats a provider's response body, an exception's text or a header.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from pydantic_ai import ModelRequestContext, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.capabilities.abstract import WrapModelRequestHandler
from pydantic_ai.exceptions import AgentRunError, ModelAPIError
from pydantic_ai.messages import ModelResponse
from pydantic_ai.settings import ModelSettings

from heinzel import HeinzelError, UsageEntry
from heinzel.kernel import ModelOffer, UsageCause
from heinzel.kernel._ai_models import PluginModelError, effort_settings, model_failure
from heinzel.kernel._logging import log_event
from heinzel.modules.heinzel._engine import TurnDeps

logger = logging.getLogger(__name__)


def failure(exc: Exception, model: str, *, before_response: bool) -> HeinzelError:
    """What hz says about a failed request on a plugin's model: `model_failure`, kept for this turn."""
    return model_failure(exc, model, before_response=before_response)


@dataclass
class PluginTurnGuard(AbstractCapability[TurnDeps]):
    """Continuity, effort and output cap before each request; usage after; clean errors on failure."""

    access: str
    offer: ModelOffer
    settings: dict[str, Any]
    verify: Callable[[], None]
    record: Callable[[UsageEntry], None]
    cause: UsageCause = "chat"
    responses: int = field(default=0, init=False)

    def get_model_settings(self) -> ModelSettings:
        """The chosen effort in the model's style, and hz's output cap."""
        return cast("ModelSettings", dict(self.settings))  # Anthropic's keys pass through to its model

    async def wrap_model_request(
        self,
        ctx: RunContext[TurnDeps],
        *,
        request_context: ModelRequestContext,
        handler: WrapModelRequestHandler,
    ) -> ModelResponse:
        """Check the turn still holds, run the request, record its tokens; a failure is a clean error.

        Raises:
            HeinzelError: If the turn's access, model or owner changed, or the request failed (`PluginModelError`,
                `ModelUnavailableError`), never with the provider's own words.
            PluginModelError: If the turn no longer holds, or the provider failed the request.
            AgentRunError: Pydantic AI's own outcomes, such as a limit or invalid output, for the engine to judge.
        """
        del ctx  # The keyword names are fixed by Pydantic's public protocol.
        try:
            await asyncio.to_thread(self.verify)
        except HeinzelError as exc:  # before the first response nothing ran: the turn can be set aside
            raise PluginModelError(str(exc), before_response=self.responses == 0) from None
        try:
            response = await handler(request_context)
        except (HeinzelError, AgentRunError) as exc:
            if not isinstance(exc, ModelAPIError):  # the engine's own outcomes: limits, invalid output, cancellation
                raise
            mapped = failure(exc, self.offer.id, before_response=self.responses == 0)
            raise mapped from None
        except Exception as exc:  # noqa: BLE001 - the provider's error never reaches people verbatim
            mapped = failure(exc, self.offer.id, before_response=self.responses == 0)
            raise mapped from None
        self.responses += 1
        usage = response.usage
        entry = UsageEntry(
            at=datetime.now(UTC),
            cause=self.cause,
            access=self.access,
            model=response.model_name or self.offer.id,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            cost_usd=None,
        )
        try:
            self.record(entry)
        except Exception as exc:  # noqa: BLE001 - a failing usage log never fails a good answer
            log_event(logger, "ai.usage.record_failed", level=logging.WARNING, error=type(exc).__name__)
        return response


def guard(  # noqa: PLR0913 - every part of the turn's policy, by keyword
    *,
    access: str,
    offer: ModelOffer,
    effort: Any,  # noqa: ANN401 - an Effort or None
    verify: Callable[[], None],
    record: Callable[[UsageEntry], None],
    cause: UsageCause,
) -> PluginTurnGuard:
    """The capability for one turn on `offer` through the AI access option `access`."""
    return PluginTurnGuard(access, offer, effort_settings(offer, effort), verify, record, cause)
