"""Internal Claude Code structured-response bridge; no native tool is an authorized action.

No production verifier, subscription grant or product wiring is provided here.
The verifier is a trusted host boundary, not an assertion from the CLI, model, user prompt or adapter.
"""

import asyncio
import contextlib
import hashlib
import json
import math
import os
import re
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from pydantic_ai._instrumentation import get_instructions
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
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
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from heinzel import HeinzelError
from heinzel.modules.heinzel._subscription_slots import SubscriptionSlots
from heinzel.modules.system.runner._process import Outcome, run_bounded

_VERSION = "2.1.312"
_MAX_INPUT = 96 * 1024
_MAX_RESPONSE = 128 * 1024
_MAX_REQUESTS = 100
_MAX_SECONDS = 7200
_FLAGS = (
    "-p",
    "--output-format",
    "json",
    "--safe-mode",
    "--restricted",
    "--strict-mcp-config",
    "--setting-sources",
    "",
    "--disable-slash-commands",
    "--no-session-persistence",
    "--permission-mode",
    "dontAsk",
    "--tools",
    "",
    "--allowed-tools",
    "",
)


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


class _Wire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: str
    name: str | None = Field(default=None, max_length=128)
    arguments: dict[str, JsonValue] | None = None
    text: str | None = None


_WIRE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "kind": {"type": "string", "enum": ["tool", "text"]},
        "name": {"type": ["string", "null"]},
        "arguments": {"type": ["object", "null"]},
        "text": {"type": ["string", "null"]},
    },
    "required": ["kind", "name", "arguments", "text"],
}
_COMMAND_FLAGS = (*_FLAGS, "--json-schema", json.dumps(_WIRE_SCHEMA, separators=(",", ":")))


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
            HeinzelError: If any requested bound, tool, process or response cannot be verified.
            asyncio.CancelledError: If the caller stops waiting; the reserved slot remains consumed.
        """
        denied = "The subscription response could not be safely admitted or verified"
        settings = dict(model_settings or {})
        if (
            settings
            or model_request_parameters.native_tools
            or self._blocked
            or self._inflight
            or self._calls >= self._policy.max_requests
        ):
            raise HeinzelError(denied)
        self._calls += 1
        ordinal = self._calls  # Capture before any await; concurrent calls must not share an ordinal.
        tools = [*model_request_parameters.function_tools, *model_request_parameters.output_tools]
        names = [tool.name for tool in tools]
        if len(set(names)) != len(names):
            raise HeinzelError(denied)
        for message in messages:
            if isinstance(message, ModelRequest):
                if any(
                    not isinstance(part, (SystemPromptPart, UserPromptPart, ToolReturnPart, RetryPromptPart))
                    or not isinstance(part.content, str)
                    for part in message.parts
                ):
                    raise HeinzelError(denied)
            elif isinstance(message, ModelResponse) and any(  # type: ignore[redundant-expr]  # runtime guard
                not isinstance(part, (TextPart, ToolCallPart)) for part in message.parts
            ):
                raise HeinzelError(denied)
        try:
            payload = json.dumps(
                {
                    "instructions": get_instructions(messages, model_request_parameters),
                    "messages": json.loads(ModelMessagesTypeAdapter.dump_json(messages)),
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
            raise HeinzelError(denied) from None
        if len(payload.encode()) > _MAX_INPUT:
            raise HeinzelError(denied)
        self._inflight = True
        try:
            executable = self._executable.resolve(strict=True)
            if executable.stat().st_size > 256 * 1024 * 1024:
                raise HeinzelError(denied)  # noqa: TRY301 - all process failures are redacted at this boundary
            digest = hashlib.sha256(executable.read_bytes()).hexdigest()
            if self._current_policy() != self._policy or not self._verifier.verify(
                executable, digest, _VERSION, _COMMAND_FLAGS, self._policy
            ):
                raise HeinzelError(denied)  # noqa: TRY301 - all process failures are redacted at this boundary
            environment = {key: os.environ[key] for key in ("HOME", "LANG", "TMPDIR") if key in os.environ}
            environment["PATH"] = "/usr/bin:/bin"
            environment.update({"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1"})
            with tempfile.TemporaryDirectory(prefix="hz-model-") as directory:
                version = await _run_cancelable(
                    self._execute,
                    [str(executable), "--version"],
                    input=None,
                    cwd=Path(directory),
                    env=environment,
                    timeout=5,
                    limit=1024,
                )
                if (
                    version.returncode != 0
                    or version.truncated
                    or version.stdout.strip() != f"{_VERSION} (Claude Code)"
                ):
                    raise HeinzelError(denied)  # noqa: TRY301
                if (
                    self._current_policy() != self._policy
                    or hashlib.sha256(executable.read_bytes()).hexdigest() != digest
                    or not self._verifier.verify(executable, digest, _VERSION, _COMMAND_FLAGS, self._policy)
                ):
                    raise HeinzelError(denied)  # noqa: TRY301 - all process failures are redacted at this boundary
                self._slots.reserve(
                    self._policy.owner,
                    self._policy.session,
                    self._turn_id,
                    self._policy.revision,
                    self._policy.max_requests,
                    ordinal,
                )
                # The ledger lock can block; do not launch on a policy revoked while waiting.
                if self._current_policy() != self._policy:
                    raise HeinzelError(denied)  # noqa: TRY301
                outcome = await _run_cancelable(
                    self._execute,
                    [str(executable), *_COMMAND_FLAGS],
                    input=payload,
                    cwd=Path(directory),
                    env=environment,
                    timeout=self._policy.timeout_seconds,
                    limit=self._policy.max_output_bytes,
                )
            if outcome.returncode != 0 or outcome.truncated:
                raise HeinzelError(denied)  # noqa: TRY301 - all process failures are redacted at this boundary
            result = json.loads(outcome.stdout)
            if (
                not isinstance(result, dict)
                or result.get("type") != "result"
                or result.get("subtype") != "success"
                or result.get("is_error") is not False
            ):
                raise HeinzelError(denied)  # noqa: TRY301 - all process failures are redacted at this boundary
            if result.get("permission_denials", []) != []:
                raise HeinzelError(denied)  # noqa: TRY301 - all process failures are redacted at this boundary
            wire = _Wire.model_validate(result.get("structured_output"))
            parts: list[TextPart | ToolCallPart]
            if wire.kind == "tool" and wire.name in names and wire.arguments is not None and wire.text is None:
                parts = [ToolCallPart(wire.name, wire.arguments)]
            elif (
                wire.kind == "text"
                and model_request_parameters.allow_text_output
                and wire.text
                and wire.name is None
                and wire.arguments is None
            ):
                parts = [TextPart(wire.text)]
            else:
                raise HeinzelError(denied)  # noqa: TRY301 - all process failures are redacted at this boundary
            return ModelResponse(parts, model_name=self.model_name, usage=RequestUsage(unmeasured_requests=1))
        except asyncio.CancelledError:
            self._blocked = True
            raise
        except Exception:  # noqa: BLE001 - verifier, process and response errors can contain private data
            self._blocked = True
            raise HeinzelError(denied) from None
        finally:
            self._inflight = False
