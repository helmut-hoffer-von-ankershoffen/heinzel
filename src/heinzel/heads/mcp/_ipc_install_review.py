"""Local review of a client adapter's exact, read-only Install plan."""

import secrets
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from heinzel.heads.mcp._ipc_cli_review import require_local_mcp_terminal, review_mcp_lifecycle_from_terminal
from heinzel.kernel._logging import get_logger, step
from heinzel.kernel._mcp_handoff import mcp_handoff, require_mcp_handoff

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import JsonValue

log = get_logger("mcp")


@dataclass(frozen=True, slots=True)
class McpInstallReview:
    """Trusted installed composition must provide the exact SDK, grant, target and client preimage."""

    snapshot: "Callable[[str], dict[str, JsonValue]]" = field(repr=False)

    def _exact(self, registration: str) -> "dict[str, JsonValue]":
        return {
            "kind": "foreground-lifecycle",
            "action": "install",
            "registration": registration,
            "review_mode": "review-only",
            "install_plan": self.snapshot(registration),
        }

    def prepare(self, registration: str, expires_at: int) -> str:
        """Commit to a client config preimage and plan without applying or approving it."""
        return mcp_handoff(self._exact(registration), expires_at)

    def review(self, registration: str, handoff: str) -> None:
        """Review the exact plan locally and revalidate it. No apply capability is issued."""
        with step(log, "mcp.ipc.install_review", operation="ipc", step="install_review"):
            require_local_mcp_terminal()
            exact = self._exact(registration)
            require_mcp_handoff(handoff, exact)
            review_mcp_lifecycle_from_terminal(
                exact | {"action_id": secrets.token_urlsafe(24), "review_started": time.time()}
            )
            require_mcp_handoff(handoff, self._exact(registration))
