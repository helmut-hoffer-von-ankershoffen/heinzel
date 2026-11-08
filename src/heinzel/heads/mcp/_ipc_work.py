"""Durable private MCP admission and effect intent, independent of any provider or result body."""

import json
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from heinzel.heads.mcp._ipc_admission import PublishedMcpLease
from heinzel.heads.mcp._ipc_approval import HostMcpConnectionApprovals, PrivateMcpPlan
from heinzel.kernel import HeinzelError
from heinzel.kernel._approval import arguments_hash
from heinzel.kernel._logging import get_logger, step
from heinzel.kernel._private_control import private_lock
from heinzel.kernel._session import SessionEvent
from heinzel.kernel._session_store import SessionStore

log = get_logger("mcp")
_REFUSED = "Local MCP work authority is invalid or unavailable"
_OPERATIONS = TypeAdapter(dict[str, list[str]])
type Disposition = Literal["queued", "running", "completed", "cancelled", "uncertain"]
type Boundary = Literal["reserve", "start", "complete"]
_INTERRUPTED: dict[Disposition, Disposition] = {"queued": "cancelled", "running": "uncertain"}
_EFFECT_STEPS: dict[str, tuple[Disposition, Disposition]] = {
    "mcp.start": ("queued", "running"),
    "mcp.complete": ("running", "completed"),
}


class McpWorkRequest(BaseModel):
    """Exact target, operation and input digest already resolved by a trusted dispatch adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    target: str = Field(min_length=1, max_length=128)
    operation: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class _Work:
    digest: str
    lease_id: str
    disposition: Disposition


def _apply_work(work: dict[str, _Work], event: SessionEvent) -> None:
    key = event.key
    if key is None:
        raise HeinzelError(_REFUSED)
    if event.kind == "mcp.reserve":
        digest, lease_id = event.data.get("digest"), event.data.get("lease")
        if key in work or not isinstance(digest, str) or not isinstance(lease_id, str):
            raise HeinzelError(_REFUSED)
        work[key] = _Work(digest, lease_id, "queued")
        return
    step_states = _EFFECT_STEPS.get(event.kind)
    item = work.get(key)
    if step_states is None or item is None or item.disposition != step_states[0]:
        raise HeinzelError(_REFUSED)
    work[key] = replace(item, disposition=step_states[1])


def _interrupt_work(work: dict[str, _Work], event: SessionEvent) -> None:
    selected = event.data.get("lease")
    if event.kind == "mcp.interrupt" and (not isinstance(selected, str) or not selected):
        raise HeinzelError(_REFUSED)
    if event.kind != "mcp.interrupt" and selected is not None:
        raise HeinzelError(_REFUSED)
    for key, item in work.items():
        if (selected is None or selected == item.lease_id) and item.disposition in _INTERRUPTED:
            work[key] = replace(item, disposition=_INTERRUPTED[item.disposition])


class McpWorkJournal:
    """One private grant ledger, under the supervisor's exclusive process ownership."""

    def __init__(self, store: SessionStore, plan: PrivateMcpPlan) -> None:
        self._store = store
        self._session = "mcp-" + arguments_hash([plan.generation.registration_id, plan.generation.number])
        self._binding = arguments_hash([plan.grant_id, plan.grant_revision, plan.terms_hash])

    def _state(self) -> tuple[bool, dict[str, _Work]]:
        snapshot = self._store.read(self._session)
        if snapshot.problem:
            raise HeinzelError(_REFUSED)
        if snapshot.events:
            binding = snapshot.events[0]
            if binding.kind != "mcp.plan" or binding.key != "binding" or binding.data != {"digest": self._binding}:
                raise HeinzelError(_REFUSED)
        revoked = False
        work: dict[str, _Work] = {}
        for event in snapshot.events[1:]:
            if event.kind not in {"mcp.revoke", "mcp.recover", "mcp.interrupt"}:
                if revoked:
                    raise HeinzelError(_REFUSED)
                _apply_work(work, event)
                continue
            revoked |= event.kind == "mcp.revoke"
            _interrupt_work(work, event)
        return revoked, work

    def fence(self, cause: Literal["recover", "revoke", "interrupt"], lease_id: str | None = None) -> bool:
        """Persist cancellation of queued work and uncertainty for unresolved effect intent."""
        with self._store.writer(self._session, wait=True) as writer:
            revoked, work = self._state()
            if cause == "recover":
                writer.append("mcp.plan", key="binding", data={"digest": self._binding})
            if not revoked and (
                cause == "revoke"
                or any(
                    item.disposition in {"queued", "running"} and (lease_id is None or lease_id == item.lease_id)
                    for item in work.values()
                )
            ):
                writer.append(f"mcp.{cause}", data={"lease": lease_id})
            return revoked or cause == "revoke"

    def transition(
        self, lease: PublishedMcpLease, request: McpWorkRequest, boundary: Boundary, check: Callable[[], None]
    ) -> Disposition:
        """Record one exact idempotent reservation or a single ordered effect boundary.

        Raises:
            HeinzelError: If the exact request, active authority or prior work state does not allow the transition.
        """
        key = arguments_hash([request.target, request.request_id])
        digest = arguments_hash([request.operation, request.input_hash])
        with self._store.writer(self._session, wait=True) as writer:
            revoked, work = self._state()
            check()
            if revoked:
                raise HeinzelError(_REFUSED)
            prior = work.get(key)
            if boundary == "reserve":
                if prior is not None:
                    if prior.digest != digest:
                        raise HeinzelError(_REFUSED)
                    return prior.disposition
                data: dict[str, JsonValue] = {"digest": digest, "lease": lease.id}
                writer.append("mcp.reserve", key=key, data=data)
                return "queued"
            expected = "queued" if boundary == "start" else "running"
            if prior is None or prior.digest != digest or prior.lease_id != lease.id or prior.disposition != expected:
                raise HeinzelError(_REFUSED)
            writer.append(f"mcp.{boundary}", key=key)
            return "running" if boundary == "start" else "completed"


class PrivateMcpDispatch:
    """Fenced journal transitions for an explicitly reviewed target and operation allowlist."""

    def __init__(
        self, authority: HostMcpConnectionApprovals, journal: McpWorkJournal, operations: dict[str, list[str]]
    ) -> None:
        self._authority = authority
        self._journal = journal
        self._operations = {target: frozenset(names) for target, names in operations.items()}

    def record(self, lease: PublishedMcpLease, request: McpWorkRequest, boundary: Boundary) -> Disposition:
        """Record a queue, pre-effect intent or observed completion without performing any effect.

        Future adapters must enforce their own provider budget and approval before reserving. They must
        check the lease at each effect boundary and observe its cancellation event during running work.
        Completion requires an independently durable observed result when the operation produces one.

        Raises:
            HeinzelError: If the operation or target is unreviewed or the current authority cannot proceed.
        """
        if boundary not in {"reserve", "start", "complete"}:
            raise HeinzelError(_REFUSED)
        with (
            step(log, "mcp.ipc.work_transition", operation="ipc", step="work_transition", boundary=boundary),
            self._authority.fenced(lease) as check,
        ):
            if request.operation not in self._operations.get(request.target, ()):
                raise HeinzelError(_REFUSED)
            result = self._journal.transition(lease, request, boundary, check)
            check()
            return result


@contextmanager
def owned_private_dispatch(
    root: Path, plan: PrivateMcpPlan, *, clock: Callable[[], float] = time.monotonic
) -> Generator[tuple[HostMcpConnectionApprovals, PrivateMcpDispatch]]:
    """Recover work only with exclusive supervisor ownership of the trusted private root.

    Raises:
        HeinzelError: If the grant changed or the process ownership and journal cannot be verified.

    Yields:
        The host-only approval authority and the journal dispatch adapter for this exact grant.
    """
    terms = json.loads(plan.terms_json)
    if arguments_hash(terms) != plan.terms_hash:
        raise HeinzelError(_REFUSED)
    operations = _OPERATIONS.validate_python(terms.get("dispatch", {}) if isinstance(terms, dict) else {}, strict=True)
    with private_lock(root / "broker.lock"):
        journal = McpWorkJournal(SessionStore(root / "work"), plan)
        revoked = journal.fence("recover")
        authority = HostMcpConnectionApprovals(
            plan,
            clock=clock,
            on_revoke=lambda: journal.fence("revoke"),
            on_lease_end=lambda lease: journal.fence("interrupt", lease),
        )
        if revoked:
            authority.revoke()
        try:
            yield authority, PrivateMcpDispatch(authority, journal, operations)
        finally:
            authority.close()
