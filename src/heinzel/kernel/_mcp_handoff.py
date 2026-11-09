"""Read-only MCP lifecycle planning and commitments carried to explicit local review."""

import hmac
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from heinzel.kernel._approval import arguments_hash
from heinzel.kernel._errors import HeinzelError

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import JsonValue

type McpLifecycleAction = Literal["start", "check", "stop", "install"]
type McpReviewSession = Literal["terminal", "owning-session"]
REGISTRATION_ID = re.compile(r"[A-Za-z0-9_-]{1,96}\Z", re.ASCII)
MCP_HANDOFF_SECONDS = 300
_HANDOFF = re.compile(r"([1-9][0-9]{0,9})\.([a-f0-9]{64})\Z", re.ASCII)
_REFUSED = "The MCP handoff expired or the installed plan changed. Prepare it again."


class McpLifecycleObservation(BaseModel):
    """Sanitized observations for an authorized UI, independent of lifecycle review."""

    host: Literal["unavailable", "observed"] = "unavailable"
    manager: Literal["not-checked", "absent", "conflict", "unavailable"] = "not-checked"
    worker: Literal["unobserved", "running", "stopped", "not-started", "stop-incomplete"] = "unobserved"
    transport: Literal["not-checked", "reachable", "unavailable"] = "not-checked"
    mcp_readiness: Literal["unverified"] = "unverified"
    client: Literal["unverified"] = "unverified"
    actions: tuple[McpLifecycleAction, ...] = ()


@dataclass(frozen=True, slots=True)
class McpLifecyclePlanning:
    """Host-composed read-only service. No approval or apply operation crosses this interface."""

    status: "Callable[[str], McpLifecycleObservation]" = field(repr=False)
    prepare: "Callable[[McpLifecycleAction, str, int], tuple[str, McpReviewSession]]" = field(repr=False)


def mcp_handoff(exact: "dict[str, JsonValue]", expires_at: int) -> str:
    """Bind a plan and expiry for comparison. The result conveys no host capability."""
    return f"{expires_at}.{arguments_hash(['mcp-lifecycle-handoff-v1', expires_at, exact])}"


def mcp_handoff_deadline(value: str) -> int:
    """Read a bounded deadline from a handoff before recomputing its commitment.

    Raises:
        HeinzelError: If the handoff is malformed or outside its permitted lifetime.
    """
    match = _HANDOFF.fullmatch(value)
    if match is None:
        raise HeinzelError(_REFUSED)
    deadline = int(match[1])
    now = time.time()
    if not now < deadline <= now + MCP_HANDOFF_SECONDS:
        raise HeinzelError(_REFUSED)
    return deadline


def require_mcp_handoff(value: str, exact: "dict[str, JsonValue]") -> None:
    """Compare the browser handoff to the current plan before and after local review.

    Raises:
        HeinzelError: If the commitment cannot be verified.
    """
    if not hmac.compare_digest(value, mcp_handoff(exact, mcp_handoff_deadline(value))):
        raise HeinzelError(_REFUSED)
