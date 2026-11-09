"""One-use private MCP connection approvals delivered directly to the broker."""

import json
import math
import os
import secrets
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Condition, Timer

from pydantic import JsonValue

from heinzel.heads.mcp._ipc_admission import (
    McpGeneration,
    PendingMcpPeer,
    PublishedMcpLease,
    _require_waiting_peer,
    _shutdown_stream,
)
from heinzel.heads.mcp._ipc_peer import require_owner_peer
from heinzel.kernel import HeinzelError
from heinzel.kernel._approval import arguments_hash
from heinzel.kernel._logging import get_logger, step

log = get_logger("mcp")
_REVIEW_SECONDS = 60.0
_PROOF_SECONDS = 60.0
_LEASE_SECONDS = 15 * 60.0
_PEER_POLL_SECONDS = 0.05
_UNAVAILABLE = "Local MCP connection approval is invalid or unavailable"


@dataclass(frozen=True, slots=True)
class PrivateMcpPlan:
    """Immutable snapshot of one authoritative grant and the exact terms for host review."""

    generation: McpGeneration
    grant_id: str
    grant_revision: int
    valid_until: float  # Monotonic deadline derived by the supervisor from the authoritative grant expiry.
    terms_json: str = field(repr=False)
    terms_hash: str = field(repr=False)

    @classmethod
    def capture(
        cls, generation: McpGeneration, grant_id: str, grant_revision: int, valid_until: float, terms: JsonValue
    ) -> "PrivateMcpPlan":
        """Snapshot the grant and exact terms supplied by the trusted registration authority.

        Raises:
            ValueError: If the grant identity, deadline or reviewed terms are invalid.
        """
        if not grant_id or type(grant_revision) is not int or grant_revision < 0 or not math.isfinite(valid_until):
            msg = "Invalid local MCP grant terms"
            raise ValueError(msg)
        digest = arguments_hash(terms)
        encoded = json.dumps(terms, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return cls(generation, grant_id, grant_revision, valid_until, encoded, digest)


@dataclass(frozen=True, slots=True)
class McpConnectionReview:
    """Host-only review of one retained connection and its full grant terms."""

    instance_id: str
    challenge: str = field(repr=False)
    peer_uid: int
    plan: PrivateMcpPlan = field(repr=False)
    expires_at: float


@dataclass(frozen=True, slots=True)
class _HostProof:
    review: McpConnectionReview
    expires_at: float


@dataclass(slots=True)
class _PendingReview:
    peer: PendingMcpPeer
    review: McpConnectionReview
    proof: _HostProof | None = None


@dataclass(frozen=True, slots=True)
class _ActiveLease:
    lease: PublishedMcpLease
    expires_at: float


class HostMcpConnectionApprovals:
    """Process-local host control and broker for a single immutable private grant.

    The supervisor keeps this object on the trusted management side. Only the bound `publish` callback
    is supplied to the socket handoff. Call `approve` only after the native or explicit local CLI reviewer
    accepted the exact snapshot. Nothing here takes web/CLI identity, a caller-supplied grant or a wire proof.
    Revoke this authority before replacing its registration generation, grant revision or reviewed terms.
    """

    def __init__(
        self,
        plan: PrivateMcpPlan,
        *,
        clock: Callable[[], float] = time.monotonic,
        on_revoke: Callable[[], object] | None = None,
        on_lease_end: Callable[[str], object] | None = None,
    ) -> None:
        self._plan = plan
        self._clock = clock
        self._on_revoke = on_revoke
        self._on_lease_end = on_lease_end
        self._changed = Condition()
        self._pending: dict[str, _PendingReview] = {}
        self._active: dict[str, _ActiveLease] = {}
        self._revoked = False

    def require_activation(self) -> None:
        """Refuse startup and endpoint checks for a revoked or expired grant.

        Raises:
            HeinzelError: If this registration cannot accept work.
        """
        with self._changed:
            if self._revoked or self._clock() >= self._plan.valid_until:
                raise HeinzelError(_UNAVAILABLE)

    def pending_reviews(self) -> tuple[McpConnectionReview, ...]:
        """Return host-only snapshots of pending peers that have no issued decision."""
        with self._changed:
            now = self._clock()
            return tuple(
                pending.review
                for pending in self._pending.values()
                if pending.proof is None and now < pending.review.expires_at
            )

    def approve(self, review: McpConnectionReview) -> None:
        """Deliver a one-use proof directly to the broker after trusted review of this exact snapshot.

        Raises:
            HeinzelError: If the reviewed instance, peer, plan or generation is stale or already decided.
        """
        with step(log, "mcp.ipc.approve_connection", operation="ipc", step="approve_connection"), self._changed:
            pending = self._pending.get(review.instance_id)
            now = self._clock()
            if (
                self._revoked
                or pending is None
                or pending.review != review
                or pending.proof is not None
                or now >= review.expires_at
            ):
                raise HeinzelError(_UNAVAILABLE)
            _require_waiting_peer(pending.peer.connection)
            pending.proof = _HostProof(pending.review, min(now + _PROOF_SECONDS, review.expires_at))
            self._changed.notify_all()

    def revoke(self) -> None:
        """Fence this generation and grant, invalidate pending proofs and shut down its exact streams."""
        with step(log, "mcp.ipc.revoke_connections", operation="ipc", step="revoke_connections"), self._changed:
            try:
                if self._on_revoke is not None:
                    self._on_revoke()
            finally:
                self.close()

    def close(self) -> None:
        """Retire this broker's leases without revoking the persisted grant."""
        with self._changed:
            self._revoked = True
            try:
                if self._on_lease_end is not None:
                    for lease_id in self._active:
                        self._on_lease_end(lease_id)
            finally:
                for pending in self._pending.values():
                    _shutdown_stream(pending.peer.connection)
                self._pending.clear()
                for active in self._active.values():
                    active.lease.cancelled.set()
                    _shutdown_stream(active.lease.peer.connection)
                self._active.clear()
                self._changed.notify_all()

    @contextmanager
    def fenced(self, lease: PublishedMcpLease) -> Generator[Callable[[], None]]:
        """Serialize a short journal transition with lease retirement and grant revocation.

        Acquire the invocation writer inside this registration fence. Never run external work under it.

        Yields:
            A deadline check safe to call while the invocation writer is held.
        """
        with self._changed:
            self._require_active(lease)
            yield lambda: self._require_active(lease, retire_expired=False)

    def _expire(self, lease_id: str) -> None:
        with self._changed:
            active = self._active.get(lease_id)
            if active is not None:
                try:
                    if self._on_lease_end is not None:
                        self._on_lease_end(lease_id)
                finally:
                    active.lease.cancelled.set()
                    _shutdown_stream(active.lease.peer.connection)
                    del self._active[lease_id]

    def _require_active(self, lease: PublishedMcpLease, *, retire_expired: bool = True) -> None:
        with self._changed:
            active = self._active.get(lease.id)
            if self._revoked or active is None or active.lease is not lease:
                raise HeinzelError(_UNAVAILABLE)
            if self._clock() >= active.expires_at:
                if retire_expired:
                    self._expire(lease.id)
                raise HeinzelError(_UNAVAILABLE)

    def _admit(self, pending: _PendingReview) -> _ActiveLease:
        """Wait for a proof and publish exactly one lease while holding the broker condition.

        Raises:
            HeinzelError: If the proof or the retained connection loses authority.
        """
        review = pending.review
        while True:
            now = self._clock()
            if self._revoked or now >= review.expires_at:
                raise HeinzelError(_UNAVAILABLE)
            _require_waiting_peer(pending.peer.connection)
            proof = pending.proof
            if proof is not None:
                require_owner_peer(pending.peer.connection, owner_uid=review.peer_uid)
                now = self._clock()
                if proof.review != review or now >= proof.expires_at:
                    raise HeinzelError(_UNAVAILABLE)
                pending.proof = None
                lease = PublishedMcpLease(
                    secrets.token_urlsafe(32), review.plan.generation, pending.peer, self._require_active
                )
                active = _ActiveLease(lease, min(now + _LEASE_SECONDS, review.plan.valid_until))
                self._active[lease.id] = active
                return active
            self._changed.wait(min(review.expires_at - now, _PEER_POLL_SECONDS))

    @contextmanager
    def publish(self, peer: PendingMcpPeer) -> Generator[PublishedMcpLease]:
        """Offer the retained verified stream for host review, then consume its proof before handoff.

        Raises:
            HeinzelError: If the generation, live stream or explicit review does not authorize admission.

        Yields:
            The lease for this exact connection, with expiry and revocation managed by this broker.
        """
        with self._changed:
            now = self._clock()
            deadline = min(now + _REVIEW_SECONDS, self._plan.valid_until)
            if self._revoked or peer.generation != self._plan.generation or now >= deadline:
                raise HeinzelError(_UNAVAILABLE)
            owner_uid = os.getuid()
            require_owner_peer(peer.connection, owner_uid=owner_uid)
            review = McpConnectionReview(
                secrets.token_urlsafe(24), secrets.token_urlsafe(32), owner_uid, self._plan, deadline
            )
            pending = _PendingReview(peer, review)
            self._pending[review.instance_id] = pending
            self._changed.notify_all()
            try:
                active = self._admit(pending)
            finally:
                self._pending.pop(review.instance_id, None)
                self._changed.notify_all()
        lease = active.lease
        expiry = Timer(max(0, active.expires_at - self._clock()), self._expire, args=(lease.id,))
        expiry.daemon = True
        try:
            expiry.start()
            lease.require_active()
            yield lease
        finally:
            expiry.cancel()
            self._expire(lease.id)
