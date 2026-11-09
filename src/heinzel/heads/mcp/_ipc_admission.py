"""Handoff from a supervisor-owned Unix listener to an admitted MCP stream."""

import os
import select
import socket
import stat
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, ExitStack, contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event

from heinzel.heads.mcp._ipc_peer import require_owner_peer
from heinzel.kernel import CurrentUser, HeinzelError
from heinzel.kernel._logging import get_logger, step
from heinzel.kernel._private_control import private_directory

log = get_logger("mcp")
_SOCKET_MODE = 0o600


@dataclass(frozen=True, slots=True)
class McpGeneration:
    """The registration locator and generation. These values confer no authority."""

    registration_id: str
    number: int

    def __post_init__(self) -> None:
        """Refuse invalid generation values before comparing a locator with trusted state.

        Raises:
            ValueError: If the generation is not a positive integer.
        """
        if type(self.number) is not int or self.number < 1:
            msg = "Local MCP generation must be a positive integer"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True, eq=False)
class PendingMcpPeer:
    """One verified stream, still pending authorization. Compare instances by identity."""

    generation: McpGeneration
    connection: socket.socket = field(repr=False)


@dataclass(frozen=True, slots=True)
class PublishedMcpLease:
    """The authority's published lease for this exact pending peer and current generation."""

    id: str
    current_generation: McpGeneration
    peer: PendingMcpPeer
    check_active: Callable[["PublishedMcpLease"], None] = field(repr=False, compare=False)
    cancelled: Event = field(default_factory=Event, init=False, repr=False, compare=False)

    def require_active(self) -> None:
        """Check current authority synchronously before handoff and each subsequent dispatch."""
        self.check_active(self)


@dataclass(frozen=True, slots=True)
class _EndpointIdentity:
    directory: tuple[int, int]
    node: tuple[int, int]


def _endpoint_identity(path: Path) -> _EndpointIdentity:
    try:
        private_directory(path.parent, create=False)
        directory = path.parent.lstat()
        CurrentUser.verify_private(directory, directory=True)
        node = path.lstat()
        if (
            stat.S_ISSOCK(node.st_mode)
            and node.st_uid == os.getuid()
            and stat.S_IMODE(node.st_mode) == _SOCKET_MODE
            and node.st_nlink == 1
        ):
            return _EndpointIdentity((directory.st_dev, directory.st_ino), (node.st_dev, node.st_ino))
    except (HeinzelError, OSError):
        pass  # The caller does not need private paths or platform errors.
    msg = "Local MCP endpoint must be an owned private socket in its physical directory"
    raise HeinzelError(msg)


def _require_waiting_peer(connection: socket.socket) -> None:
    try:
        readiness = select.poll()
        readiness.register(connection, select.POLLIN)
        if not readiness.poll(0):
            return  # The bridge is still waiting for admission and has not sent MCP bytes.
    except (OSError, ValueError):
        pass
    msg = "Local MCP peer disconnected or sent data before admission"
    raise HeinzelError(msg)


def _shutdown_stream(connection: socket.socket) -> None:
    # Wake both ends without invalidating a descriptor the owning thread may still be polling.
    with suppress(OSError):
        connection.shutdown(socket.SHUT_RDWR)


def _close_stream(connection: socket.socket) -> None:
    _shutdown_stream(connection)  # Buffered wrappers can defer descriptor close but cannot keep the stream alive.
    connection.close()


@contextmanager
def admit_owned_stream(
    take: Callable[[], socket.socket],
    verify: Callable[[], McpGeneration],
    read_locator: Callable[[socket.socket], McpGeneration],
    publish: Callable[[PendingMcpPeer], AbstractContextManager[PublishedMcpLease]],
) -> Generator[PublishedMcpLease]:
    """Verify a supervisor-owned connection and publish its lease before handing off the stream.

    Raises:
        HeinzelError: If the ownership, peer, locator or published lease fails verification.

    Yields:
        The exact active lease while the stream is owned by this handoff.
    """
    # Unwind the stream before the lease, even when an admission check fails.
    with ExitStack() as leases, ExitStack() as streams:
        with step(log, "mcp.ipc.admit_peer", operation="ipc", step="admit_peer"):
            generation = verify()
            connection = take()
            streams.callback(_close_stream, connection)
            require_owner_peer(connection, owner_uid=os.getuid())
            verify()
            try:
                requested = read_locator(connection)
            except (HeinzelError, OSError, ValueError):
                msg = "Local MCP could not read the bootstrap locator"
                raise HeinzelError(msg) from None
            if requested != generation:
                msg = "Local MCP locator does not match the supervised generation"
                raise HeinzelError(msg)
            _require_waiting_peer(connection)
            pending = PendingMcpPeer(generation, connection)
            try:
                lease = leases.enter_context(publish(pending))
            except (HeinzelError, OSError, ValueError):
                msg = "Local MCP admission lease was not published"
                raise HeinzelError(msg) from None
            if not lease.id or lease.peer is not pending or lease.current_generation != generation:
                msg = "Local MCP lease does not match the current generation and peer"
                raise HeinzelError(msg)
            verify()
            _require_waiting_peer(connection)
            lease.require_active()
        yield lease


class OwnedMcpEndpoint:
    """Keep the supervisor's original listener and its physical endpoint together."""

    def __init__(self, listener: socket.socket, path: Path, generation: McpGeneration) -> None:
        """Capture the listener just bound by the supervisor under its registration lifecycle lock.

        The supervisor creates the private socket and retains the listener for this generation. It does not
        construct this binding by reopening a pathname or adopting an existing socket. It owns final cleanup.

        Raises:
            HeinzelError: If the endpoint is not a bound private Unix socket at the specified absolute path.
        """
        try:
            if (
                path.is_absolute()
                and ".." not in path.parts
                and listener.family == socket.AF_UNIX
                and listener.type == socket.SOCK_STREAM
                and listener.getsockname() == os.fspath(path)
            ):
                self._identity = _endpoint_identity(path)
                self._listener = listener
                self._path = path
                self._generation = generation
                return
        except OSError:
            pass
        msg = "Local MCP requires its original owned Unix listener"
        raise HeinzelError(msg)

    def verify_endpoint(self) -> McpGeneration:
        """Check the retained listener and exact filesystem identity.

        Raises:
            HeinzelError: If the owned endpoint is no longer available or has changed.
        """
        try:
            bound = self._listener.getsockname() == os.fspath(self._path)
        except OSError:
            bound = False
        if not bound:
            msg = "Local MCP requires its original owned Unix listener"
            raise HeinzelError(msg)
        if _endpoint_identity(self._path) != self._identity:
            msg = "Local MCP endpoint identity changed"
            raise HeinzelError(msg)
        return self._generation

    @contextmanager
    def admit_next(
        self,
        read_locator: Callable[[socket.socket], McpGeneration],
        publish: Callable[[PendingMcpPeer], AbstractContextManager[PublishedMcpLease]],
    ) -> Generator[PublishedMcpLease]:
        """Give protocol code the same stream only while its trusted admission lease is held.

        The bounded bootstrap reader reads only the locator. The trusted publisher checks the current
        generation, review/grant and lifecycle fences, and publishes and tracks the lease before yielding it.
        Its lock order is SDK maintenance, profile authority, then registration lifecycle. It releases those
        locks before protocol handoff. Revocation must shut down the tracked stream; this handoff owns the
        descriptor and closes it on exit. The publisher's context releases the lease on refusal or protocol exit.

        Raises:
            HeinzelError: If the physical endpoint, peer, locator or published lease fails verification.

        Yields:
            The published lease for the original stream, now ready for protocol handoff.
        """

        def take() -> socket.socket:
            return self._listener.accept()[0]

        with admit_owned_stream(take, self.verify_endpoint, read_locator, publish) as lease:
            yield lease
