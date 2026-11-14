"""hz's side of a turn on a plugin's model: a Pydantic AI capability, never a wrapper around the model (ADR 0159).

The plugin built a plain `pydantic_ai.models.Model`; hz's engine runs it with Pydantic AI's own `Agent`. This
capability hooks each model request: before it, it checks (off the event loop, without calling the plugin) that what
hz owns still holds: the owner, the access, the chosen model and effort, the budget mode and the plugin's version.
It adds the chosen effort in the model's own style and hz's output cap. After a response it records the tokens the
model reported, with no dollar cost. A failure becomes a clean `PluginModelError` that never repeats a provider's
response body, an exception's text or a header.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from pydantic_ai import ModelRequestContext, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.capabilities.abstract import WrapModelRequestHandler
from pydantic_ai.exceptions import (
    AgentRunError,
    ModelAPIError,
    ModelConnectionError,
    ModelHTTPError,
    ModelRateLimitError,
    ModelServerError,
)
from pydantic_ai.exceptions import ModelUnavailableError as ProviderUnavailableError
from pydantic_ai.messages import ModelResponse
from pydantic_ai.settings import ModelSettings

from heinzel import HeinzelError, UsageEntry
from heinzel.kernel import ModelOffer, UsageCause
from heinzel.kernel._ai_models import ModelUnavailableError, effort_settings
from heinzel.modules.heinzel._engine import TurnDeps

UNAUTHORIZED = frozenset({401, 403})
BUSY = frozenset({429, 503, 529})


class PluginModelError(HeinzelError):
    """A plugin's model failed a request; the message says what kind of failure, never the provider's words."""

    def __init__(self, message: str, *, before_response: bool) -> None:
        """Keep whether it failed before any response of the turn: then nothing was answered and no tool ran."""
        super().__init__(message)
        self.before_response = before_response


def _status(exc: BaseException) -> int | None:
    return exc.status_code if isinstance(exc, ModelHTTPError) else None


def failure(exc: Exception, model: str, *, before_response: bool) -> HeinzelError:
    """What hz says about a failed request on a plugin's model, from its kind and status code only."""
    status = _status(exc)
    code = f" ({status})" if status is not None else ""
    if status in UNAUTHORIZED:
        message = f"The provider refused the request{code}: check the plugin's sign-in."
    elif status == 404 or (isinstance(exc, ProviderUnavailableError) and status not in BUSY):  # noqa: PLR2004
        return ModelUnavailableError(model, [], "the provider doesn't know it or can't serve it now")
    elif status in BUSY or isinstance(exc, ModelRateLimitError):
        message = f"The provider is busy or rate-limited{code}; try again shortly."
    elif isinstance(exc, ModelConnectionError):
        message = "Couldn't reach the plugin's model; check the network and the plugin's endpoint."
    elif isinstance(exc, ModelServerError) or (status is not None and status >= 500):  # noqa: PLR2004
        message = f"The provider failed the request{code}; try again later."
    elif isinstance(exc, ModelAPIError):
        message = f"The provider rejected the request{code}."
    else:
        message = f"The plugin's model failed ({type(exc).__name__})."
    return PluginModelError(message, before_response=before_response)


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
            AgentRunError: Pydantic AI's own outcomes, such as a limit or invalid output, for the engine to judge.
        """
        del ctx  # The keyword names are fixed by Pydantic's public protocol.
        await asyncio.to_thread(self.verify)
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
        self.record(
            UsageEntry(
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
        )
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
