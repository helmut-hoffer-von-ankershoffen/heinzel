"""Internal Claude Code structured-response bridge; no native tool is an authorized action.

No production verifier, subscription grant or product wiring is provided here.
The verifier is a trusted host boundary, not an assertion from the CLI, model, user prompt or adapter.
"""

import asyncio
import contextlib
import json
import logging
import math
import re
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic_ai._instrumentation import get_instructions
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    RetryFeedbackPart,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from heinzel import HeinzelError
from heinzel.kernel import ProcessOutcome as Outcome
from heinzel.kernel import SubscriptionUsageLimitError
from heinzel.kernel import run_bounded_process as run_bounded
from heinzel.kernel._claude_code import (
    _MAX_HELP,
    _PROTOCOL,
    COMMAND_FLAGS,
    CONTRACT_OPTIONS,
    SUPPORTED_VERSIONS,
    VERSION,
    Denial,
    SubscriptionDeniedError,
    _proven_before,
    _remember_proof,
    _structured,
    check_access,
    executable_digest,
    prove_contract,
    require_no_model_override,
    require_subscription_login,
    subscription_environment,
    supported_version,
)
from heinzel.modules.heinzel._subscription_slots import SubscriptionSlots
from heinzel.modules.heinzel._turn_authority_fence import TurnAuthorityFence

logger = logging.getLogger(__name__)
_MAX_INPUT = 96 * 1024
_MAX_RESPONSE = 128 * 1024
_MAX_REQUESTS = 100
_MAX_SECONDS = 7200
__all__ = [
    "COMMAND_FLAGS",
    "CONTRACT_OPTIONS",
    "SUPPORTED_VERSIONS",
    "VERSION",
    "ClaudeSubscriptionModel",
    "Denial",
    "SubscriptionAdmission",
    "SubscriptionDeniedError",
    "SubscriptionUsageLimitError",
    "SubscriptionVerifier",
    "check_access",
    "executable_digest",
    "prove_contract",
    "require_no_model_override",
    "require_subscription_login",
    "subscription_environment",
    "supported_version",
]


@dataclass(frozen=True, slots=True)
class SubscriptionAdmission:
    """Trusted host policy: no monetary or token ceiling is silently reinterpreted as an estimate."""

    owner: str
    session: str
    revision: int
    max_requests: int
    timeout_seconds: float
    max_output_bytes: int
    dollar_cap: float | None = None
    total_token_cap: int | None = None


class SubscriptionVerifier(Protocol):
    """Independent host verifier; inspect exact executable and enforce its profile, not just reported flags."""

    def verify(
        self, executable: Path, digest: str, version: str, flags: tuple[str, ...], policy: SubscriptionAdmission
    ) -> bool:
        """Admit exact identity, subscription billing, isolation and current owner authority."""
        ...


async def _run_cancelable(execute: Callable[..., Outcome], command: list[str], **kwargs: object) -> Outcome:
    cancel = threading.Event()
    worker = asyncio.create_task(asyncio.to_thread(execute, command, **kwargs, cancel=cancel))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        cancel.set()
        with contextlib.suppress(Exception):  # Process details remain private.
            await asyncio.shield(worker)
        raise


async def _admit(
    execute: Callable[..., Outcome], executable: Path, digest: str, cwd: Path, environment: dict[str, str]
) -> str:
    """Admit this exact executable: a reviewed release whose help lists every option, proven once per digest.

    Raises:
        SubscriptionDeniedError: If the release, its options or its bytes cannot be admitted.
    """
    version = supported_version(
        await _run_cancelable(
            execute, [str(executable), "--version"], input=None, cwd=cwd, env=environment, timeout=5, limit=1024
        )
    )
    if not _proven_before(executable, digest):
        prove_contract(
            await _run_cancelable(
                execute, [str(executable), "--help"], input=None, cwd=cwd, env=environment, timeout=10, limit=_MAX_HELP
            ),
            version,
        )
        if executable_digest(executable) != digest:
            raise SubscriptionDeniedError(Denial.EXECUTABLE_CHANGED)
        _remember_proof(executable, digest)
    return version


class ClaudeSubscriptionModel(Model):
    """Turn closed CLI text/tool requests into Pydantic messages; host dispatches any proposed tool."""

    def __init__(  # noqa: PLR0913 - host admission requires explicit trusted dependencies
        self,
        executable: Path,
        policy: SubscriptionAdmission,
        verifier: SubscriptionVerifier,
        current_policy: Callable[[], SubscriptionAdmission],
        *,
        slots: SubscriptionSlots,
        turn_id: str,
        execute: Callable[..., Outcome] = run_bounded,
    ) -> None:
        super().__init__()
        self._fence: TurnAuthorityFence | None = None
        self._executable = executable
        self._policy = policy
        self._verifier = verifier
        self._current_policy = current_policy
        self._execute = execute
        self._slots = slots
        self._turn_id = turn_id
        self._calls = 0
        self._blocked = False
        self._inflight = False
        self._digest: str | None = None
        if (
            not executable.is_absolute()
            or re.fullmatch(r"u-[0-9a-f]{32}", policy.owner) is None
            or re.fullmatch(r"hz-[0-9A-HJKMNP-TV-Z]{26}", policy.session) is None
            or type(policy.revision) is not int
            or policy.revision < 1
            or type(policy.max_requests) is not int
            or not 1 <= policy.max_requests <= _MAX_REQUESTS
            or type(policy.timeout_seconds) not in {int, float}
            or not math.isfinite(policy.timeout_seconds)
            or not 0 < policy.timeout_seconds <= _MAX_SECONDS
            or type(policy.max_output_bytes) is not int
            or not 1 <= policy.max_output_bytes <= _MAX_RESPONSE
            or policy.dollar_cap is not None
            or policy.total_token_cap is not None
        ):
            msg = "This subscription policy cannot be enforced by the bridge"
            raise HeinzelError(msg)

    def bind_fence(self, fence: TurnAuthorityFence) -> None:
        """Bind the owner-bound turn lease; this does not supply subscription authorization.

        Raises:
            HeinzelError: If the policy or lease does not belong to this owner and session.
        """
        fence.bind(self._slots.repository, self._policy.session)
        if self._slots.repository.principal_id != self._policy.owner:
            msg = "The subscription policy belongs to another owner"
            raise HeinzelError(msg)
        self._fence = fence

    @property
    def model_name(self) -> str:
        return "claude-code:subscription"

    @property
    def system(self) -> str:
        return "claude-code"

    async def request(  # noqa: C901, PLR0912, PLR0915 - one fail-closed admission boundary
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        """Request a structured response only after independent host admission; never execute returned tools.

        Raises:
            SubscriptionDeniedError: If any requested bound, tool, process or response cannot be verified; its reason
                says which, its words what to do.
            SubscriptionUsageLimitError: If the CLI reports its usage limit.
            asyncio.CancelledError: If the caller stops waiting; the reserved slot remains consumed.
        """
        settings = dict(model_settings or {})
        if (
            settings
            or model_request_parameters.native_tools
            or self._blocked
            or self._inflight
            or self._calls >= self._policy.max_requests
        ):
            raise SubscriptionDeniedError(Denial.REFUSED)
        if self._fence is not None:
            self._fence.check()
        self._calls += 1
        ordinal = self._calls  # Capture before any await; concurrent calls must not share an ordinal.
        tools = [*model_request_parameters.function_tools, *model_request_parameters.output_tools]
        names = [tool.name for tool in tools]
        if len(set(names)) != len(names):
            raise SubscriptionDeniedError(Denial.REFUSED)
        for message in messages:
            if isinstance(message, ModelRequest):
                if any(
                    not isinstance(part, (SystemPromptPart, UserPromptPart, ToolReturnPart, RetryFeedbackPart))
                    or not isinstance(part.content, str)
                    for part in message.parts
                ):
                    raise SubscriptionDeniedError(Denial.REFUSED)
            elif isinstance(message, ModelResponse) and any(  # type: ignore[redundant-expr]  # runtime guard
                not isinstance(part, (TextPart, ToolCallPart)) for part in message.parts
            ):
                raise SubscriptionDeniedError(Denial.REFUSED)
        try:
            payload = json.dumps(
                {
                    "protocol": _PROTOCOL,
                    "instructions": get_instructions(messages, model_request_parameters),
                    # Every request in the history repeats the instructions; once at the top is what the model needs.
                    "messages": [
                        {key: value for key, value in message.items() if key != "instructions"}
                        for message in json.loads(ModelMessagesTypeAdapter.dump_json(messages))
                    ],
                    "tools": [
                        {"name": tool.name, "description": tool.description, "schema": tool.parameters_json_schema}
                        for tool in tools
                    ],
                    "allow_text": model_request_parameters.allow_text_output,
                },
                ensure_ascii=True,
                separators=(",", ":"),
            )
        except Exception:  # noqa: BLE001 - payload errors can contain private conversation content
            raise SubscriptionDeniedError(Denial.REFUSED) from None
        if len(payload.encode()) > _MAX_INPUT:
            raise SubscriptionDeniedError(Denial.REFUSED)
        self._inflight = True
        try:
            try:
                executable = self._executable.resolve(strict=True)
                digest = executable_digest(executable)
            except (OSError, HeinzelError):
                raise SubscriptionDeniedError(Denial.UNAVAILABLE) from None
            if self._digest is not None and digest != self._digest:
                raise SubscriptionDeniedError(Denial.EXECUTABLE_CHANGED)  # noqa: TRY301 - typed, redacted at this boundary
            self._digest = digest
            if self._current_policy() != self._policy or not self._verifier.verify(
                executable, digest, VERSION, COMMAND_FLAGS, self._policy
            ):
                raise SubscriptionDeniedError(Denial.ACCESS_CHANGED)  # noqa: TRY301
            environment = subscription_environment()
            with tempfile.TemporaryDirectory(prefix="hz-model-") as directory:
                await _admit(self._execute, executable, digest, Path(directory), environment)
                if self._current_policy() != self._policy:
                    raise SubscriptionDeniedError(Denial.ACCESS_CHANGED)  # noqa: TRY301
                if executable_digest(executable) != digest:
                    raise SubscriptionDeniedError(Denial.EXECUTABLE_CHANGED)  # noqa: TRY301
                if not self._verifier.verify(executable, digest, VERSION, COMMAND_FLAGS, self._policy):
                    raise SubscriptionDeniedError(Denial.ACCESS_CHANGED)  # noqa: TRY301
                self._slots.reserve(
                    self._policy.owner,
                    self._policy.session,
                    self._turn_id,
                    self._policy.revision,
                    self._policy.max_requests,
                    ordinal,
                )
                if self._fence is not None:
                    self._fence.check()
                # The ledger lock can block; do not launch on a policy revoked while waiting.
                if self._current_policy() != self._policy:
                    raise SubscriptionDeniedError(Denial.ACCESS_CHANGED)  # noqa: TRY301
                outcome = await _run_cancelable(
                    self._execute,
                    [str(executable), *COMMAND_FLAGS],
                    input=payload,
                    cwd=Path(directory),
                    env=environment,
                    timeout=self._policy.timeout_seconds,
                    limit=self._policy.max_output_bytes,
                )
            wire = _structured(outcome)
            parts: list[TextPart | ToolCallPart]
            outputs = model_request_parameters.output_tools
            # The real CLI can omit the name of the only output: that delivers a validated result, never a host tool.
            name = wire.name if wire.name is not None or len(outputs) != 1 else outputs[0].name
            if wire.kind == "tool" and name in names and wire.arguments is not None and wire.text is None:
                parts = [ToolCallPart(name, wire.arguments)]
            elif (
                wire.kind == "text"
                and model_request_parameters.allow_text_output
                and wire.text
                and wire.name is None
                and wire.arguments is None
            ):
                parts = [TextPart(wire.text)]
            else:
                raise SubscriptionDeniedError(Denial.OUTPUT_INVALID)  # noqa: TRY301
            return ModelResponse(parts, model_name=self.model_name, usage=RequestUsage(unmeasured_requests=1))
        except asyncio.CancelledError:
            self._blocked = True
            raise
        except Exception as error:  # noqa: BLE001 - verifier, process and response errors can contain private data
            self._blocked = True
            if isinstance(error, SubscriptionUsageLimitError):
                logger.warning("Claude Code subscription request refused: usage-limit")
                raise SubscriptionUsageLimitError from None
            reason = error.reason if isinstance(error, SubscriptionDeniedError) else Denial.REFUSED
            logger.warning("Claude Code subscription request refused: %s", reason.value)
            if isinstance(error, SubscriptionDeniedError):
                raise SubscriptionDeniedError(reason, error.version, older=error.older) from None
            raise SubscriptionDeniedError(reason) from None
        finally:
            self._inflight = False
