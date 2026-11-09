"""Creation-only named Unix generations, with a private descriptor handoff from the startup child."""

import os
import secrets
import signal
import socket
import stat
import sys
import threading
import time
from collections.abc import Generator
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path

from heinzel.heads.mcp._ipc_descriptors import probe_streams
from heinzel.kernel import CurrentUser, HeinzelError
from heinzel.kernel._logging import get_logger, step
from heinzel.kernel._private_control import private_directory

_NODE = "s"
_SOCKET_MODE = 0o600
_TIMEOUT = 2.0
_PATH_BYTES = 103  # Leave room for the terminator on Darwin and Linux.
_REFUSED = "The local MCP named generation could not be verified"
log = get_logger("mcp")


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _node(directory: int) -> os.stat_result:
    node = os.stat(_NODE, dir_fd=directory, follow_symlinks=False)
    if (
        not stat.S_ISSOCK(node.st_mode)
        or node.st_uid != os.getuid()
        or stat.S_IMODE(node.st_mode) != _SOCKET_MODE
        or node.st_nlink != 1
    ):
        raise HeinzelError(_REFUSED)
    return node


def _wait(pid: int, deadline: float) -> int | None:
    while True:
        waited, status = os.waitpid(pid, os.WNOHANG)
        if waited:
            return status
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.005)


def _reap(pid: int, deadline: float) -> None:
    status = _wait(pid, deadline)
    if status is None:
        # The still-unreaped direct child is owned by this process. No discovered PID is signalled.
        with suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
        if _wait(pid, time.monotonic() + _TIMEOUT) is None:
            msg = "The owned MCP startup child did not exit"
            raise HeinzelError(msg)
        raise HeinzelError(_REFUSED)
    if status != 0:
        raise HeinzelError(_REFUSED)


def _bind_in_child(directory: int, channel: socket.socket, parent_channel: socket.socket) -> None:
    status = 1
    try:
        parent_channel.close()
        os.fchdir(directory)
        os.umask(0o177)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(_NODE)
            listener.listen()
            channel.settimeout(_TIMEOUT)
            if socket.send_fds(channel, [b"L"], [listener.fileno()]) == 1:
                status = 0
    finally:
        if status:
            with suppress(OSError):
                channel.send(b"E")
        os._exit(status)


def _start_listener(directory: int) -> socket.socket:
    control, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    with control, child:
        deadline = time.monotonic() + _TIMEOUT
        control.settimeout(_TIMEOUT)
        pid = os.fork()
        if pid == 0:
            _bind_in_child(directory, child, control)
        child.close()
        received: list[int] = []
        try:
            try:
                data, received, flags, _ = socket.recv_fds(control, 2, 2)
            finally:
                _reap(pid, deadline)
            if data != b"L" or len(received) != 1 or flags & (socket.MSG_CTRUNC | socket.MSG_TRUNC):
                raise HeinzelError(_REFUSED)
            descriptor = received[0]
            os.set_inheritable(descriptor, False)  # noqa: FBT003 - the stdlib flag is positional-only
            listener = socket.socket(fileno=descriptor)
            received.clear()
            return listener
        finally:
            for descriptor in received:
                os.close(descriptor)


@dataclass(frozen=True, slots=True)
class NamedMcpGeneration:
    """One retained listener. The pathname locates it and supplies no approval authority."""

    locator: Path
    _directory: int = field(repr=False)
    _listener: socket.socket = field(repr=False)
    _node_identity: tuple[int, int] = field(repr=False)

    def _verify(self) -> None:
        private_directory(self.locator.parent, create=False)
        if (
            self._listener.family != socket.AF_UNIX
            or self._listener.type != socket.SOCK_STREAM
            or self._listener.getsockname() != _NODE
            or _identity(os.fstat(self._directory)) != _identity(self.locator.parent.lstat())
            or _identity(_node(self._directory)) != self._node_identity
            or _identity(self.locator.lstat()) != self._node_identity
        ):
            raise HeinzelError(_REFUSED)

    def check(self) -> None:
        """Observe a real locator round-trip against the retained listener without granting any lease.

        Raises:
            HeinzelError: If the generation has changed or the bounded probe cannot reach it.
        """
        with step(log, "mcp.ipc.probe_named_transport", operation="ipc", step="probe_named_transport"):
            try:
                self._verify()
                with socket.socket(socket.AF_UNIX) as probe:
                    probe.settimeout(_TIMEOUT)
                    probe.connect(os.fspath(self.locator))
                    accepted, _ = self._listener.accept()
                    with accepted:
                        probe_streams(probe, accepted)
                self._verify()
            except OSError:
                raise HeinzelError(_REFUSED) from None

    def checked_locator(self) -> Path:
        """Return the currently observed physical locator without granting any authority.

        Raises:
            HeinzelError: If the retained generation no longer matches the path.
        """
        try:
            self._verify()
        except OSError:
            raise HeinzelError(_REFUSED) from None
        return self.locator

    def maintenance_identity(self) -> tuple[tuple[int, int], tuple[int, int]]:
        """Sync and observe the retained directory and socket for the maintenance inventory.

        Raises:
            HeinzelError: If the retained directory cannot be synced or the locator changed.
        """
        try:
            os.fsync(self._directory)
            self.checked_locator()
            return _identity(os.fstat(self._directory)), self._node_identity
        except OSError:
            raise HeinzelError(_REFUSED) from None

    def retire(self) -> None:
        """Close the retained listener without changing any filesystem node."""
        self._listener.close()


@contextmanager
def owned_named_generation(root: Path, *, root_descriptor: int | None = None) -> Generator[NamedMcpGeneration]:
    """Start from an isolated single-thread process before plugin, vendor or native worker threads.

    The caller holds the registration lock, selects the short private root and retains exclusive child
    reaping at startup. The forked helper binds relative to the inherited generation FD, with its own cwd
    and umask. All generated paths stay on disk after any outcome. Stop closes the retained listener.
    It never chmods, unlinks or renames a node.

    Raises:
        HeinzelError: If the platform, startup context or exact generation cannot be verified.

    Yields:
        A verified named listener, for transport checks only until a trusted bridge adapter is supplied.
    """
    if (
        sys.platform not in {"darwin", "linux"}
        or not sys.flags.isolated
        or threading.current_thread() is not threading.main_thread()
        or threading.active_count() != 1
        or signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL
        or not root.is_absolute()
        or ".." in root.parts
    ):
        raise HeinzelError(_REFUSED)
    locator = root / ("g-" + secrets.token_hex(6)) / _NODE
    if len(os.fsencode(locator)) > _PATH_BYTES:
        msg = "The local MCP generation path exceeds the Unix socket address limit"
        raise HeinzelError(msg)
    with ExitStack() as owned:
        try:
            private_directory(root, create=False)
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            parent = os.open(root, flags) if root_descriptor is None else os.dup(root_descriptor)
            owned.callback(os.close, parent)
            CurrentUser.verify_private(os.fstat(parent), directory=True)
            if _identity(os.fstat(parent)) != _identity(root.lstat()):
                raise HeinzelError(_REFUSED)
            os.mkdir(locator.parent.name, mode=0o700, dir_fd=parent)
            directory = os.open(locator.parent.name, flags, dir_fd=parent)
            owned.callback(os.close, directory)
            CurrentUser.verify_private(os.fstat(directory), directory=True)
            listener = owned.enter_context(_start_listener(directory))
            listener.settimeout(_TIMEOUT)
            generation = NamedMcpGeneration(locator, directory, listener, _identity(_node(directory)))
        except OSError:
            raise HeinzelError(_REFUSED) from None
        generation.check()
        yield generation
