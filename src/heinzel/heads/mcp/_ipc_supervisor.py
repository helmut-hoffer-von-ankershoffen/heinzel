"""One owned local MCP registration with separate host review and transport entry points."""

import secrets
import sys
import time
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, ExitStack, contextmanager
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
from heinzel.heads.mcp._ipc_bridge import BridgeHandshake, launch_inherited_bridge
from heinzel.heads.mcp._ipc_cli_review import review_private_mcp_from_terminal
from heinzel.heads.mcp._ipc_descriptors import local_stream_pair, probe_local_descriptors
from heinzel.heads.mcp._ipc_named_generation import NamedMcpGeneration, owned_named_generation
from heinzel.heads.mcp._ipc_runtime_root import McpRuntimeLocator, mcp_runtime_locator
from heinzel.heads.mcp._ipc_work import (
    Boundary,
    Disposition,
    McpWorkRequest,
    PrivateMcpDispatch,
    owned_private_dispatch,
)
from heinzel.kernel import HeinzelError
from heinzel.kernel._approval import arguments_hash
from heinzel.kernel._logging import get_logger, step
from heinzel.kernel._mcp_storage import InstalledMcpRuntime

type ReadLocator = Callable[[socket], McpGeneration]
log = get_logger("mcp")


@dataclass(frozen=True, slots=True)
class SupervisedMcpTransport:
    """The transport can hold leases and record fenced work without a host review entry point."""

    admit: Callable[[ReadLocator], AbstractContextManager[PublishedMcpLease]]
    record: Callable[[PublishedMcpLease, McpWorkRequest, Boundary], Disposition]


@dataclass(frozen=True, slots=True)
class InheritedMcpTransport:
    """A bridge transport with fixed bootstrap and no host review entry point."""

    admit: Callable[[], AbstractContextManager[PublishedMcpLease]]
    record: Callable[[PublishedMcpLease, McpWorkRequest, Boundary], Disposition]


@dataclass(frozen=True, slots=True)
class OwnedMcpSupervisor:
    """Host-only control of one exact reviewed registration while its private ownership lock is held."""

    _generation: McpGeneration
    _authority: HostMcpConnectionApprovals = field(repr=False)
    _dispatch: PrivateMcpDispatch = field(repr=False)
    _named: NamedMcpGeneration | None = field(default=None, repr=False)
    _runtime: McpRuntimeLocator | None = field(default=None, repr=False)
    _incarnation: bytes = field(default_factory=partial(secrets.token_bytes, 32), init=False, repr=False, compare=False)
    _connections: dict[socket, bool] = field(default_factory=dict, init=False, repr=False, compare=False)
    _released: Event = field(default_factory=Event, init=False, repr=False, compare=False)
    _lifetime: Lock = field(default_factory=Lock, init=False, repr=False, compare=False)

    def _require_owned(self) -> None:
        if self._released.is_set():
            msg = "This local MCP supervisor no longer owns the registration"
            raise HeinzelError(msg)

    def pending_review_ids(self) -> tuple[str, ...]:
        """List opaque instance IDs for the trusted CLI's review selection."""
        self._check_authority()
        return tuple(review.instance_id for review in self._authority.pending_reviews())

    def _require_available(self) -> None:
        self._require_owned()
        self._authority.require_activation()
        if self._runtime is not None:
            try:
                self._runtime.require_current()
                if self._named is not None:
                    self._named.checked_locator()
            except HeinzelError:
                self._retire()
                raise

    def _check_authority(self) -> McpGeneration:
        with self._lifetime:
            self._require_available()
            return self._generation

    def check_transport(self) -> McpGeneration:
        """Probe the retained named listener or an independent pair without granting a lease."""
        generation = self._check_authority()
        if self._named is None:
            probe_local_descriptors()
        else:
            self._named.check()
        self._check_authority()
        return generation

    def discovery_locator(self) -> Path | None:
        """Return the selected pathname as a locator only. It conveys no host or lease authority."""
        self._check_authority()
        return self._named.checked_locator() if self._named is not None else None

    def _take(self, owned: socket) -> socket:
        with self._lifetime:
            self._require_available()
            if self._connections.get(owned) is not False:
                msg = "The owned MCP descriptor was already handed off or retired"
                raise HeinzelError(msg)
            self._connections[owned] = True
            return owned

    def _record(self, lease: PublishedMcpLease, request: McpWorkRequest, boundary: Boundary) -> Disposition:
        with self._lifetime:
            self._require_available()
            return self._dispatch.record(lease, request, boundary)

    @contextmanager
    def open_transport(self) -> Generator[tuple[socket, SupervisedMcpTransport]]:
        """Issue a one-use descriptor pair to the trusted host before any client handoff.

        Yields:
            The client end and the broker's restricted transport handle for this exact connection.
        """
        with self._lifetime:
            self._require_available()
            owned, client = local_stream_pair()
            self._connections[owned] = False
        admit = partial(
            admit_owned_stream, partial(self._take, owned), self._check_authority, publish=self._authority.publish
        )
        try:
            with client:
                yield client, SupervisedMcpTransport(admit, self._record)
        finally:
            with self._lifetime:
                claimed = self._connections.pop(owned, None)
                if claimed is True:
                    _shutdown_stream(owned)
                elif claimed is False:
                    _close_stream(owned)

    @contextmanager
    def launch_bridge(self) -> Generator[tuple[socket, InheritedMcpTransport]]:
        """Launch trusted bridge code at isolated startup, with a new private channel for this lease.

        Yields:
            The bridge's front stream and a restricted broker handoff with an exact versioned handshake.
        """
        binding = self._incarnation + secrets.token_bytes(32)
        handshake = BridgeHandshake(b"HZ-MCP-BRIDGE\x00\x01" + binding, b"HZ-MCP-ADMITTED\x00\x01" + binding)

        def read_hello(channel: socket) -> McpGeneration:
            handshake.require_hello(channel)
            return self._generation

        @contextmanager
        def admit() -> Generator[PublishedMcpLease]:
            with transport.admit(read_hello) as lease:
                self._check_authority()
                lease.require_active()
                handshake.acknowledge(lease.peer.connection)
                yield lease

        # Unwind the broker channel first so its EOF releases the direct child even before approval.
        with ExitStack() as child, self.open_transport() as (channel, transport):
            with step(log, "mcp.ipc.launch_bridge", operation="ipc", step="launch_bridge"):
                front = child.enter_context(launch_inherited_bridge(channel, handshake))
            yield front, InheritedMcpTransport(admit, transport.record)

    def review(self, instance_id: str) -> None:
        """Give the pinned terminal reviewer one private control descriptor for this exact instance."""
        self._check_authority()
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

    def _retire(self) -> None:
        self._released.set()
        try:
            self._authority.close()
        finally:
            self._stop_connections()
            if self._named is not None:
                self._named.retire()

    def _release(self) -> None:
        with self._lifetime:
            self._retire()


@contextmanager
def owned_mcp_supervisor(
    root: Path,
    plan: PrivateMcpPlan,
    *,
    endpoint_path: Path | None = None,
    named_root: Path | McpRuntimeLocator | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Generator[OwnedMcpSupervisor]:
    """Create, probe and retire one owned transport in the already-trusted isolated CLI runtime.

    The caller selects the installed runtime and private registration root. A short, private named root is
    optional and can be used only at single-thread startup. Fixed caller-selected socket paths still refuse.
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
    runtime = named_root if isinstance(named_root, McpRuntimeLocator) else None
    if runtime is not None:
        runtime.require_current()
    with owned_private_dispatch(root, plan, clock=clock) as (authority, dispatch), ExitStack() as transport:
        authority.require_activation()
        named = None
        if runtime is not None:
            named = transport.enter_context(owned_named_generation(runtime.path, root_descriptor=runtime.descriptor))
            directory, node = named.maintenance_identity()
            runtime.observe_generation(
                named.locator, directory, node, plan.generation.registration_id, plan.generation.number
            )
        elif isinstance(named_root, Path):
            named = transport.enter_context(owned_named_generation(named_root))
        else:
            probe_local_descriptors()
        supervisor = OwnedMcpSupervisor(plan.generation, authority, dispatch, named, runtime)
        try:
            yield supervisor
        finally:
            supervisor._release()  # noqa: SLF001 - scope the host handle to this ownership lock


@contextmanager
def owned_installed_mcp_supervisor(
    installation: InstalledMcpRuntime, plan: PrivateMcpPlan, *, clock: Callable[[], float] = time.monotonic
) -> Generator[OwnedMcpSupervisor]:
    """Enter from the trusted installed activation with a reviewed plan and OS-owned runtime location.

    Yields:
        The owned foreground supervisor under the SDK maintenance and registration locks.
    """
    with ExitStack() as ownership:
        with step(log, "mcp.ipc.activate_installed", operation="ipc", step="activate_installed"):
            runtime = ownership.enter_context(mcp_runtime_locator(installation))
            registration = arguments_hash(plan.generation.registration_id)[:24]
            host = ownership.enter_context(
                owned_mcp_supervisor(installation.state / "mcp" / registration, plan, named_root=runtime, clock=clock)
            )
            runtime.require_current()
        yield host
