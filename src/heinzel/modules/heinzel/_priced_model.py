"""Experimental request-by-request reservation for one verified Responses API pricing snapshot.

This adapter is not registered or exposed. Pricing snapshots are not contractual guarantees against changes;
provider conformance, trusted grants and all billing classes require independent verification before product use.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from heinzel import HeinzelError
from heinzel.modules.heinzel._budget_reservation import PriceEnvelope, reserve_model_spend
from heinzel.modules.heinzel._session_protocol import SessionRepository

MODEL = "openai:gpt-4.1-mini-2025-04-14"
REVISION = "openai-gpt-4.1-mini-standard-2026-10-29"
CHECKED = datetime(2026, 10, 29, tzinfo=UTC)
EXPIRES = datetime(2026, 11, 1, tzinfo=UTC)
CONTEXT = 1_047_576
MAX_OUTPUT = 32_768
MAX_REQUESTS = 100


def resolve_pricing(model: str, mode: str, now: datetime) -> tuple[Decimal, Decimal]:
    """Return a short-lived published standard text-rate snapshot for the one pinned model.

    Raises:
        HeinzelError: If the model, billing mode or snapshot freshness is unsupported.
    """
    if now.tzinfo is None or not CHECKED <= now.astimezone(UTC) < EXPIRES or model != MODEL or mode != "standard":
        msg = "The model pricing policy is missing, stale or unsupported"
        raise HeinzelError(msg)
    return Decimal("0.40"), Decimal("1.60")


class ReservedModel(WrapperModel):
    """Reserve the model's full context and bounded output before each generation request.

    Only a trusted caller may construct this internal wrapper; it must validate provider identity, exact endpoint,
    model snapshot, credential, policy revisions and grants. Fake transports are used solely in offline tests.
    """

    def __init__(  # noqa: PLR0913 - distinct trusted policy fields
        self,
        model: Model,
        repository: SessionRepository,
        session: str,
        turn: str,
        *,
        message_cap: Decimal,
        max_requests: int,
        max_turn_tokens: int,
        max_turn_output_tokens: int,
        session_cap: Decimal,
        owner_cap: Decimal,
        now: Callable[[], datetime],
        require_initialized: bool = False,
        verify_authority: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(model)
        self._verify_authority = verify_authority
        self._require_initialized = require_initialized
        self._repository = repository
        self._session = session
        self._turn = turn
        self._owner = repository.principal_id
        if type(max_requests) is not int or not 1 <= max_requests <= MAX_REQUESTS:
            msg = "The model requires a bounded request count"
            raise HeinzelError(msg)
        self._max_requests = max_requests
        self._max_turn_tokens = max_turn_tokens
        self._max_turn_output_tokens = max_turn_output_tokens
        self._message_cap = message_cap
        self._session_cap = session_cap
        self._owner_cap = owner_cap
        self._now = now
        resolve_pricing(MODEL, "standard", self._now())
        self._calls = 0

    @classmethod
    def for_openai(  # noqa: PLR0913 - every budget is an independent policy value
        cls,
        model: Model,
        repository: SessionRepository,
        session: str,
        turn: str,
        *,
        message_cap: Decimal,
        max_requests: int,
        max_turn_tokens: int,
        max_turn_output_tokens: int,
        session_cap: Decimal,
        owner_cap: Decimal,
        now: Callable[[], datetime],
        verify_authority: Callable[[], None] | None = None,
    ) -> "ReservedModel":
        """Accept only the pinned direct OpenAI Responses model and canonical endpoint.

        Raises:
            HeinzelError: If the provider, snapshot or endpoint cannot be established.
        """
        if (
            not isinstance(model, OpenAIResponsesModel)
            or bool(model.settings)
            or model.model_name != "gpt-4.1-mini-2025-04-14"
            or (model.base_url or "").rstrip("/") != "https://api.openai.com/v1"
        ):
            msg = "This provider, model snapshot or endpoint is not supported by the pricing policy"
            raise HeinzelError(msg)
        return cls(
            model,
            repository,
            session,
            turn,
            message_cap=message_cap,
            max_requests=max_requests,
            max_turn_tokens=max_turn_tokens,
            max_turn_output_tokens=max_turn_output_tokens,
            session_cap=session_cap,
            owner_cap=owner_cap,
            now=now,
            require_initialized=True,
            verify_authority=verify_authority,
        )

    async def request(  # noqa: C901, PLR0912 - one request passes every limit before dispatch
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        """Validate text-only requests; reserve worst-case context and output before every provider dispatch.

        Raises:
            HeinzelError: If policy, request shape, counting or reservation cannot be verified.
        """
        if self._repository.principal_id != self._owner:
            msg = "The model reservation belongs to a different owner"
            raise HeinzelError(msg)
        if self._verify_authority is not None:
            self._verify_authority()
        input_rate, output_rate = resolve_pricing(MODEL, "standard", self._now())
        if self._calls >= self._max_requests:
            msg = "The model request limit has been reached"
            raise HeinzelError(msg)
        settings = dict(model_settings or {})
        if set(settings) - {"max_tokens"} or model_request_parameters.native_tools:
            msg = "This request uses unsupported provider settings or hosted tools"
            raise HeinzelError(msg)
        output = settings.get("max_tokens")
        if type(output) is not int or not 1 <= output <= MAX_OUTPUT:
            msg = "The model requires an explicit supported output-token bound"
            raise HeinzelError(msg)
        for message in messages:
            if isinstance(message, ModelRequest):
                for part in message.parts:
                    if not isinstance(
                        part, (SystemPromptPart, UserPromptPart, ToolReturnPart, RetryPromptPart)
                    ) or not isinstance(part.content, str):
                        msg = "This pricing policy supports text-only input"
                        raise HeinzelError(msg)
            elif isinstance(message, ModelResponse):
                if any(not isinstance(part, (TextPart, ToolCallPart)) for part in message.parts):
                    msg = "This pricing policy supports text-only input"
                    raise HeinzelError(msg)
            else:
                msg = "This pricing policy supports text-only input"  # type: ignore[unreachable]  # runtime guard
                raise HeinzelError(msg)
        # Counting is a remote provider call. Reserve the full supported context without contacting the provider.
        settings.update({"openai_service_tier": "default", "max_retries": 0})
        resolve_pricing(MODEL, "standard", self._now())
        self._calls += 1
        request = f"{self._turn}-{self._calls}"
        envelope = PriceEnvelope(
            model=MODEL,
            price_revision=REVISION,
            max_input_tokens=CONTEXT,
            max_output_tokens=output,
            max_requests=1,
            input_usd_per_million=input_rate,
            output_usd_per_million=output_rate,
        )
        reserved = reserve_model_spend(
            self._repository,
            self._session,
            request,
            envelope,
            turn_id=self._turn,
            max_turn_tokens=self._max_turn_tokens,
            max_turn_output_tokens=self._max_turn_output_tokens,
            message_cap=self._message_cap,
            require_initialized=self._require_initialized,
            session_cap=self._session_cap,
            owner_cap=self._owner_cap,
        )
        if reserved.disposition != "reserved":
            msg = "A prior reservation cannot authorize another model request"
            raise HeinzelError(msg)
        resolve_pricing(MODEL, "standard", self._now())
        if self._verify_authority is not None:
            self._verify_authority()
        return await self.wrapped.request(messages, settings, model_request_parameters)  # type: ignore[arg-type]
