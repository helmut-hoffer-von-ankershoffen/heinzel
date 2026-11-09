"""One owned local MCP registration with separate host review and transport entry points."""

import sys
import time
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from socket import socket
from threading import Event, Lock

from heinzel.heads.mcp._ipc_admission import (
    McpGeneration,
    PublishedMcpLease,
    _close_stream,
    _shutdown_stream,
    admit_owned_stream,
)
from heinzel.heads.mcp._ipc_approval import HostMcpConnectionApprovals, PrivateMcpPlan
from heinzel.heads.mcp._ipc_cli_review import review_private_mcp_from_terminal
from heinzel.heads.mcp._ipc_descriptors import local_stream_pair, probe_local_descriptors
from heinzel.heads.mcp._ipc_work import (
    Boundary,
    Disposition,
    McpWorkRequest,
    PrivateMcpDispatch,
    owned_private_dispatch,
)
from heinzel.kernel import HeinzelError

type ReadLocator = Callable[[socket], McpGeneration]


@dataclass(frozen=True, slots=True)
class SupervisedMcpTransport:
    """The transport can hold leases and record fenced work without a host review entry point."""

    admit: Callable[[ReadLocator], AbstractContextManager[PublishedMcpLease]]
    record: Callable[[PublishedMcpLease, McpWorkRequest, Boundary], Disposition]


@dataclass(frozen=True, slots=True)
class OwnedMcpSupervisor:
    """Host-only control of one exact reviewed registration while its private ownership lock is held."""

    _generation: McpGeneration
    _authority: HostMcpConnectionApprovals = field(repr=False)
    _dispatch: PrivateMcpDispatch = field(repr=False)
    _connections: dict[socket, bool] = field(default_factory=dict, init=False, repr=False, compare=False)
    _released: Event = field(default_factory=Event, init=False, repr=False, compare=False)
    _lifetime: Lock = field(default_factory=Lock, init=False, repr=False, compare=False)

    def _require_owned(self) -> None:
        if self._released.is_set():
            msg = "This local MCP supervisor no longer owns the registration"
            raise HeinzelError(msg)

    def pending_review_ids(self) -> tuple[str, ...]:
        """List opaque instance IDs for the trusted CLI's review selection."""
        self._require_owned()
        return tuple(review.instance_id for review in self._authority.pending_reviews())

    def _check_authority(self) -> McpGeneration:
        with self._lifetime:
            self._require_owned()
            self._authority.require_activation()
            return self._generation

    def check_transport(self) -> McpGeneration:
        """Probe an independent owned pair without granting a lease or claiming MCP readiness."""
        generation = self._check_authority()
        probe_local_descriptors()
        self._check_authority()
        return generation

    def _take(self, owned: socket) -> socket:
        with self._lifetime:
            self._require_owned()
            self._authority.require_activation()
            if self._connections.get(owned) is not False:
                msg = "The owned MCP descriptor was already handed off or retired"
                raise HeinzelError(msg)
            self._connections[owned] = True
            return owned

    @contextmanager
    def open_transport(self) -> Generator[tuple[socket, SupervisedMcpTransport]]:
        """Issue a one-use descriptor pair to the trusted host before any client handoff.

        Yields:
            The client end and the broker's restricted transport handle for this exact connection.
        """
        with self._lifetime:
            self._require_owned()
            self._authority.require_activation()
            owned, client = local_stream_pair()
            self._connections[owned] = False
        admit = partial(
            admit_owned_stream, partial(self._take, owned), self._check_authority, publish=self._authority.publish
        )
        try:
            with client:
                yield client, SupervisedMcpTransport(admit, self._dispatch.record)
        finally:
            with self._lifetime:
                claimed = self._connections.pop(owned, None)
                if claimed is True:
                    _shutdown_stream(owned)
                elif claimed is False:
                    _close_stream(owned)

    def review(self, instance_id: str) -> None:
        """Give the pinned terminal reviewer one private control descriptor for this exact instance."""
        self._require_owned()
        review_private_mcp_from_terminal(self._authority, instance_id)

    def revoke(self) -> None:
        """Durably fence the reviewed grant and stop its streams."""
        with self._lifetime:
            self._require_owned()
            try:
                self._authority.revoke()
            finally:
                self._stop_connections()

    def _stop_connections(self) -> None:
        for owned, claimed in self._connections.items():
            if claimed:
                _shutdown_stream(owned)  # The owning worker closes a descriptor it may still be polling.
            else:
                _close_stream(owned)
        self._connections.clear()

    def _release(self) -> None:
        with self._lifetime:
            self._released.set()
            try:
                self._authority.close()
            finally:
                self._stop_connections()


@contextmanager
def owned_mcp_supervisor(
    root: Path,
    plan: PrivateMcpPlan,
    *,
    endpoint_path: Path | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Generator[OwnedMcpSupervisor]:
    """Create, probe and retire one owned transport in the already-trusted isolated CLI runtime.

    The caller selects the installed runtime and private registration root. Filesystem-named endpoints fail
    closed until a platform adapter can bind and retire them without mutating substitute filesystem nodes.
    Only the transport handle may be supplied to protocol code. The supervisor is never a tool or web service.

    Raises:
        HeinzelError: If isolation, descriptor transport or registration ownership cannot be verified.

    Yields:
        The host-only supervisor until its registration ownership and active leases are released.
    """
    if endpoint_path is not None:
        msg = "Named local MCP activation is unavailable; use an owned descriptor transport"
        raise HeinzelError(msg)
    if not sys.flags.isolated:
        msg = "Local MCP supervision requires the trusted isolated CLI runtime"
        raise HeinzelError(msg)
    with owned_private_dispatch(root, plan, clock=clock) as (authority, dispatch):
        authority.require_activation()
        probe_local_descriptors()
        supervisor = OwnedMcpSupervisor(plan.generation, authority, dispatch)
        try:
            yield supervisor
        finally:
            supervisor._release()  # noqa: SLF001 - scope the host handle to this ownership lock
