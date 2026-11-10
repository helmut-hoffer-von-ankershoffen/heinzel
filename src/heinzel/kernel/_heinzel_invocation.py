"""Shared identity and provider contract for one reviewed Heinzel invocation (ADR 0146)."""

import base64
import hashlib
import re
from collections.abc import Callable
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from heinzel.kernel._services import ServiceContract

type HeinzelKind = Literal["personal", "contract"]
_PERSONAL = re.compile(r"hz-[0-9A-HJKMNP-TV-Z]{26}")
_CONTRACT = re.compile(r"(?:me|team):[a-z](?:-?[a-z0-9])*")
_MAX_CONTRACT_NAME = 64
_HASH = re.compile(r"sha256:[0-9a-f]{64}")
MAX_MESSAGE_LENGTH = 32000


class HeinzelTarget(BaseModel):
    """The exact stable identity and, for a contract, its reviewed source digest."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    kind: HeinzelKind
    identity: str
    source_digest: str | None = None

    @model_validator(mode="after")
    def check_binding(self) -> Self:
        """Reject ambiguous identities or contracts without a source pin.

        Raises:
            ValueError: If the target is not a canonical supported identity.
        """
        personal = self.kind == "personal" and _PERSONAL.fullmatch(self.identity) and self.source_digest is None
        contract = (
            self.kind == "contract"
            and len(self.identity.partition(":")[2]) <= _MAX_CONTRACT_NAME
            and _CONTRACT.fullmatch(self.identity)
            and _HASH.fullmatch(self.source_digest or "")
        )
        if not (personal or contract):
            msg = "Invalid reviewed Heinzel target"
            raise ValueError(msg)
        return self

    @property
    def key(self) -> str:
        """The typed target identity used by the broker grant and journal."""
        return f"{self.kind}:{self.identity}"

    @property
    def tool_name(self) -> str:
        """A stable, MCP-safe name unaffected by display names or source edits."""
        digest = hashlib.sha256(self.key.encode()).digest()
        return "heinzel_" + base64.b32encode(digest).decode("ascii").rstrip("=").lower()

    @property
    def operation(self) -> str:
        """Only the owning provider's fixed operation for this kind."""
        return "chat" if self.kind == "personal" else "wake"


class HeinzelInput(BaseModel):
    """A caller-supplied idempotency key and optional personal message."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    request_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    message: str | None = Field(default=None, min_length=1, max_length=MAX_MESSAGE_LENGTH)


class HeinzelInvocations(ServiceContract):
    """Resolve and execute only an already-reviewed individual target."""

    def require_current(self, target: HeinzelTarget) -> None:
        """Verify the target still exists in the authenticated owner's scope."""
        raise NotImplementedError

    def execute(
        self, target: HeinzelTarget, request: HeinzelInput, check_active: Callable[[], None]
    ) -> dict[str, JsonValue]:
        """Run one target using the broker's scoped request identity."""
        raise NotImplementedError
