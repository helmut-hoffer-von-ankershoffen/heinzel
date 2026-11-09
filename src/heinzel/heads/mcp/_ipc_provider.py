"""Durable launch limits for an explicitly admitted private contract backend."""

from collections.abc import Generator, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from heinzel.heads._heinzel_tools import CheckActive
from heinzel.kernel import HeinzelError, HeinzelInvocations
from heinzel.kernel._private_control import private_lock
from heinzel.kernel._session import SessionEvent
from heinzel.kernel._session_store import SessionStore

_REFUSED = "The reviewed contract backend is busy, exhausted or needs reconciliation"


class McpBackendLimit(BaseModel):
    """Exact backend identity and an allowance measured in provider launches."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    launches: int = Field(ge=1)


@dataclass(frozen=True, slots=True)
class McpContractBackend:
    """Host-owned, verified provider and total launch ceiling for one exact backend.

    A host may supply only a backend whose provider limits it can enforce independently. The ordinary
    AgentRunner's advisory budget does not qualify. The fixture binds an inert synthetic backend.
    """

    limit: McpBackendLimit
    provider: HeinzelInvocations


def _completed_launches(events: Iterable[SessionEvent]) -> dict[str, int]:
    counts: dict[str, int] = {}
    started: str | None = None
    for event in events:
        if event.kind == "mcp.provider_start" and event.key and started is None:
            scope = event.data.get("scope")
            if not isinstance(scope, str):
                raise HeinzelError(_REFUSED)
            counts[scope] = counts.get(scope, 0) + 1
            started = event.key
        elif event.kind == "mcp.provider_finish" and started is not None and event.key == started:
            started = None
        else:
            raise HeinzelError(_REFUSED)
    if started is not None:
        raise HeinzelError(_REFUSED)
    return counts


@dataclass(frozen=True, slots=True)
class McpProviderAllowance:
    """One shared physical slot per owner, with retained total and per-grant allowance."""

    root: Path
    scope: str
    total: int
    grant: int

    @contextmanager
    def reserve(self, key: str, check_active: CheckActive) -> Generator[CheckActive]:
        """Hold the slot through completion and retain ambiguous work after lease or process loss.

        Raises:
            HeinzelError: If the policy changed, the allowance is exhausted, or prior work is unresolved.

        Yields:
            A callback that durably consumes one launch immediately before the effect.
        """
        store = SessionStore(self.root / "attempts")
        with private_lock(self.root / "launch.lock"), store.writer("mcp-provider") as writer:
            snapshot = store.read("mcp-provider")
            if snapshot.problem:
                raise HeinzelError(_REFUSED)
            policy: dict[str, JsonValue] = {"total": self.total}
            if snapshot.events:
                first = snapshot.events[0]
                if first.kind != "mcp.provider_policy" or first.key != "limit" or first.data != policy:
                    raise HeinzelError(_REFUSED)
            else:
                writer.append("mcp.provider_policy", key="limit", data=policy)
            counts = _completed_launches(snapshot.events[1:])
            if sum(counts.values()) >= self.total or counts.get(self.scope, 0) >= self.grant:
                raise HeinzelError(_REFUSED)
            started: str | None = None

            def consume() -> None:
                nonlocal started
                if started is not None:
                    raise HeinzelError(_REFUSED)
                check_active()
                writer.append("mcp.provider_start", key=key, data={"scope": self.scope})
                started = key

            yield consume
            if started is not None:
                writer.append("mcp.provider_finish", key=started)
