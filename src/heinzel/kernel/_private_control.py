"""Bounded owner-only Unix control without persisting a network bearer (ADR 0117)."""

import fcntl
import hashlib
import json
import os
import secrets
import socket
import stat
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path

from pydantic import JsonValue, TypeAdapter

from heinzel.kernel._errors import HeinzelError

LIMIT = 16 * 1024


def private_directory(path: Path, *, create: bool = True) -> None:
    """Create an owner-only physical directory, refusing links and foreign or writable parents.

    Raises:
        HeinzelError: If the physical path is unsafe.
        FileNotFoundError: If a required existing directory is missing.
    """
    for parent in reversed((path, *path.parents)):
        if not parent.exists() and not parent.is_symlink():
            if not create:
                raise FileNotFoundError(parent)
            parent.mkdir(mode=0o700)
        info = parent.lstat()
        sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid not in {0, os.getuid()}
            or (info.st_mode & 0o022 and not sticky_root)
        ):
            msg = "The local service path is not physically owned and private."
            raise HeinzelError(msg)
    info = path.lstat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        msg = "The local service folder must be owned by you with mode 0700."
        raise HeinzelError(msg)


@contextmanager
def private_lock(path: Path) -> Generator[None]:
    """Hold one owned file lock while local integration changes are serialized.

    Raises:
        HeinzelError: If a foreign lock file or another writer prevents ownership.

    Yields:
        While this process exclusively owns the private file descriptor.
    """
    private_directory(path.parent)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
            msg = "The web integration lock is not owned and private."
            raise HeinzelError(msg)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            msg = "Another web integration change is in progress."
            raise HeinzelError(msg) from exc
        yield
    finally:
        os.close(descriptor)


def private_socket_directory(identity: Path) -> Path:
    """Select a short physical owner-specific socket folder, independent of long profile paths.

    This deterministic name holds no credential. Its ownership and private mode are checked before every use;
    existing foreign entries are refused rather than adopted or removed.
    """
    digest = hashlib.sha256(str(identity).encode()).hexdigest()[:16]
    return Path("/tmp").resolve() / f"hz-ui-{os.getuid()}-{digest}"  # noqa: S108 - checked owner-only child of root sticky tmp


def private_write(path: Path, text: str) -> None:
    """Atomically replace a private regular file beneath an owner-only directory.

    Raises:
        HeinzelError: If the directory or an existing entry is unsafe.
    """
    private_directory(path.parent)
    if path.exists() or path.is_symlink():
        private_read(path)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def private_read(path: Path) -> str:
    """Read one bounded owner-only regular file without following its final link.

    Raises:
        HeinzelError: If the file or its directory is unsafe.
    """
    private_directory(path.parent, create=False)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
            or info.st_size > LIMIT
        ):
            msg = "The local service file is not an owned private regular file."
            raise HeinzelError(msg)
        encoded = handle.read(LIMIT + 1)
    if len(encoded) > LIMIT:
        msg = "The local service file exceeds its size limit."
        raise HeinzelError(msg)
    return encoded.decode("utf-8")


def _receive(connection: socket.socket) -> bytes:
    data = bytearray()
    while len(data) <= LIMIT:
        chunk = connection.recv(min(4096, LIMIT + 1 - len(data)))
        if not chunk:
            break
        data.extend(chunk)
        if b"\n" in data:
            break
    if len(data) > LIMIT or not data.endswith(b"\n") or data.count(b"\n") != 1:
        msg = "Invalid local message."
        raise ValueError(msg)
    return bytes(data)


class PrivateControl:
    """An owner-only socket whose live connection, rather than a stored bearer, carries local control."""

    def __init__(self, path: Path, handler: Callable[[str], dict[str, JsonValue]]) -> None:
        """Bind a fresh socket; an existing live or stale entry is never adopted or removed.

        Raises:
            HeinzelError: If the socket's directory is unsafe.
            OSError: If another entry occupies the socket name.
        """
        private_directory(path.parent)
        self.path = path
        self.handler = handler
        self.socket = socket.socket(socket.AF_UNIX)
        self.closed = threading.Event()
        try:
            self.socket.bind(str(path))
            path.chmod(0o600)
            self.identity = path.lstat().st_ino
            self.socket.listen(4)
            self.socket.settimeout(0.2)
        except BaseException:
            self.socket.close()
            raise
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        while not self.closed.is_set():
            try:
                connection, _ = self.socket.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with connection:
                connection.settimeout(1)
                try:
                    request = TypeAdapter(dict[str, JsonValue]).validate_json(_receive(connection))
                    if set(request) != {"command"}:
                        continue
                    command = request["command"]
                    if not isinstance(command, str):
                        continue
                    response = json.dumps(self.handler(command)).encode() + b"\n"
                    if len(response) <= LIMIT:
                        connection.sendall(response)
                except (OSError, ValueError, HeinzelError):
                    continue

    def close(self) -> None:
        """Close this server and remove only its original socket entry."""
        self.closed.set()
        self.socket.close()
        self.thread.join(timeout=2)
        try:
            info = self.path.lstat()
            if stat.S_ISSOCK(info.st_mode) and info.st_ino == self.identity:
                self.path.unlink()
        except FileNotFoundError:
            pass

    @staticmethod
    def request(path: Path, command: str) -> dict[str, JsonValue]:
        """Exchange one bounded message with a private live socket.

        Raises:
            HeinzelError: If the physical socket is not private and owned.
            OSError: If no live server answers.
            ValueError: If the answer is not a bounded JSON object.
        """
        private_directory(path.parent, create=False)
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            msg = "The local service socket is not owned and private."
            raise HeinzelError(msg)
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(2)
            connection.connect(str(path))
            connection.sendall(json.dumps({"command": command}).encode() + b"\n")
            return TypeAdapter(dict[str, JsonValue]).validate_json(_receive(connection))
