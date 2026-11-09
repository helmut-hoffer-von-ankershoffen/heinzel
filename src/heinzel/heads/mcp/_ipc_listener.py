"""Physical Unix listener ownership and a startup transport probe."""

import os
import secrets
import socket
import stat
import time
from collections.abc import Generator
from contextlib import contextmanager, suppress
from pathlib import Path

from heinzel.heads.mcp._ipc_peer import require_owner_peer
from heinzel.kernel import CurrentUser, HeinzelError
from heinzel.kernel._logging import get_logger, step
from heinzel.kernel._private_control import private_directory

_MODE = 0o600
_TIMEOUT = 2.0
_REFUSED = "The local MCP transport could not establish ownership"
log = get_logger("mcp")


def _socket_node(directory: int, name: str) -> os.stat_result:
    node = os.stat(name, dir_fd=directory, follow_symlinks=False)
    if not stat.S_ISSOCK(node.st_mode) or node.st_uid != os.getuid() or node.st_nlink != 1:
        raise HeinzelError(_REFUSED)
    return node


def _remove_node(directory: int, name: str, identity: tuple[int, int]) -> None:
    with suppress(FileNotFoundError):
        node = _socket_node(directory, name)
        if (node.st_dev, node.st_ino) != identity:
            msg = "The local MCP socket changed before cleanup"
            raise HeinzelError(msg)
        os.unlink(name, dir_fd=directory)
        os.fsync(directory)


def _receive(connection: socket.socket, size: int, deadline: float) -> bytes:
    data = bytearray()
    while len(data) < size:
        connection.settimeout(max(0, deadline - time.monotonic()))
        chunk = connection.recv(size - len(data))
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)


def probe_listener(listener: socket.socket, path: Path) -> None:
    """Verify this exact listener can exchange a bounded challenge with its same-owner startup probe.

    Run before handing the listener to vendor admission workers. The probe grants no MCP lease.

    Raises:
        HeinzelError: If the real Unix round-trip or either kernel peer check fails.
    """
    with step(log, "mcp.ipc.probe_transport", operation="ipc", step="probe_transport"):
        try:
            with socket.socket(socket.AF_UNIX) as probe:
                probe.settimeout(_TIMEOUT)
                probe.connect(os.fspath(path))
                require_owner_peer(probe, owner_uid=os.getuid())
                challenge = secrets.token_bytes(32)
                probe.sendall(challenge)
                deadline = time.monotonic() + _TIMEOUT
                accepted, _ = listener.accept()
                with accepted:
                    require_owner_peer(accepted, owner_uid=os.getuid())
                    if _receive(accepted, len(challenge), deadline) != challenge:
                        raise HeinzelError(_REFUSED)
                    accepted.sendall(challenge)
                    if _receive(probe, len(challenge), deadline) != challenge:
                        raise HeinzelError(_REFUSED)
        except OSError:
            raise HeinzelError(_REFUSED) from None


@contextmanager
def owned_unix_listener(path: Path) -> Generator[socket.socket]:
    """Bind a fresh socket inside the supervisor's already-private physical directory.

    Hold the registration lock across this context. A pre-existing entry is never adopted or removed.

    Raises:
        HeinzelError: If the socket cannot be bound, verified or safely retired.

    Yields:
        The listening socket until the owning context closes it and removes its exact node.
    """
    if not path.is_absolute() or ".." in path.parts:
        raise HeinzelError(_REFUSED)
    try:
        private_directory(path.parent, create=False)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        raise HeinzelError(_REFUSED) from None
    identity: tuple[int, int] | None = None
    try:
        CurrentUser.verify_private(os.fstat(directory), directory=True)
        with socket.socket(socket.AF_UNIX) as listener:
            try:
                listener.bind(os.fspath(path))
                node = _socket_node(directory, path.name)
                identity = (node.st_dev, node.st_ino)
                os.chmod(path.name, _MODE, dir_fd=directory, follow_symlinks=False)
                listener.listen()
                listener.settimeout(_TIMEOUT)
            except (NotImplementedError, OSError, ValueError):
                raise HeinzelError(_REFUSED) from None
            yield listener
    finally:
        try:
            if identity is not None:
                _remove_node(directory, path.name, identity)
        except OSError:
            raise HeinzelError(_REFUSED) from None
        finally:
            os.close(directory)
