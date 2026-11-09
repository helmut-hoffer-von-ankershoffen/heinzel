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

from heinzel.heads.mcp._ipc_admission import McpGeneration, OwnedMcpEndpoint, PublishedMcpLease
from heinzel.heads.mcp._ipc_approval import HostMcpConnectionApprovals, PrivateMcpPlan
from heinzel.heads.mcp._ipc_cli_review import review_private_mcp_from_terminal
from heinzel.heads.mcp._ipc_listener import owned_unix_listener, probe_listener
from heinzel.heads.mcp._ipc_work import Boundary, Disposition, McpWorkRequest, owned_private_dispatch
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

    transport: SupervisedMcpTransport
    _authority: HostMcpConnectionApprovals = field(repr=False)
    _endpoint: OwnedMcpEndpoint = field(repr=False)
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

    def check_endpoint(self) -> McpGeneration:
        """Verify the current owned transport endpoint without granting a lease or claiming MCP readiness."""
        with self._lifetime:
            self._require_owned()
            self._authority.require_activation()
            return self._endpoint.verify_endpoint()

    def review(self, instance_id: str) -> None:
        """Give the pinned terminal reviewer one private control descriptor for this exact instance."""
        self._require_owned()
        review_private_mcp_from_terminal(self._authority, instance_id)

    def revoke(self) -> None:
        """Durably fence the reviewed grant and stop its streams."""
        with self._lifetime:
            self._require_owned()
            self._authority.revoke()

    def _release(self) -> None:
        with self._lifetime:
            self._released.set()


@contextmanager
def owned_mcp_supervisor(
    endpoint_path: Path,
    root: Path,
    plan: PrivateMcpPlan,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> Generator[OwnedMcpSupervisor]:
    """Create, probe and retire one owned transport in the already-trusted isolated CLI runtime.

    The caller selects the installed runtime and private physical registration paths. The registration
    lock covers endpoint creation, admission fencing and listener cleanup.
    Only the transport handle may be supplied to protocol code. The supervisor is never a tool or web service.

    Raises:
        HeinzelError: If runtime isolation, listener identity or registration ownership cannot be verified.

    Yields:
        The host-only supervisor until its registration ownership and active leases are released.
    """
    if not sys.flags.isolated:
        msg = "Local MCP supervision requires the trusted isolated CLI runtime"
        raise HeinzelError(msg)
    with owned_private_dispatch(root, plan, clock=clock) as (authority, dispatch):
        authority.require_activation()
        with owned_unix_listener(endpoint_path) as listener:
            endpoint = OwnedMcpEndpoint(listener, endpoint_path, plan.generation)
            probe_listener(listener, endpoint_path)
            endpoint.verify_endpoint()
            transport = SupervisedMcpTransport(partial(endpoint.admit_next, publish=authority.publish), dispatch.record)
            supervisor = OwnedMcpSupervisor(transport, authority, endpoint)
            try:
                yield supervisor
            finally:
                supervisor._release()  # noqa: SLF001 - scope the host handle to this ownership lock
                authority.close()
