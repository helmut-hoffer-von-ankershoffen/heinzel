"""How a process without the desktop's bridge reaches the one that has it (ADR 0162, with ADR 0117's rules).

The desktop app connects its browser to one process: the web server. A chat turn's private stdio pipe (the Claude
Code runner) and `hz system serve mcp` over stdio are other processes of the same person. Their browser calls are
not run there: they are sent, as the session or `local`, the tool's name and its arguments, to the process that
holds the bridge, and that process runs its own tool by that name. So the gate, the proposal, the limits and the
audit log happen once, in one place, and the sender cannot widen the tool set, name another agent or approve.

The channel is an owner-only Unix socket as in ADR 0117: no port and no stored bearer; the live connection in a
directory only the owner can enter carries the trust, the same trust that lets that person start `hz` at all. The
socket exists only while a desktop is connected. Without it there is nothing to send to, and the caller answers
"needs the desktop app" itself.

One request and one answer per connection, each a line of JSON. A request is small; an answer may hold a picture.
"""

import json
import os
import secrets
import socket
import stat
import struct
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from pathlib import Path
from typing import Final

from pydantic import JsonValue, TypeAdapter, ValidationError

from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._private_control import private_directory, private_read, private_socket_directory, private_write

REQUEST_LIMIT: Final = 64 * 1024
ANSWER_LIMIT: Final = 16 * 1024 * 1024
"""A picture of a tab is a few megabytes of base64 at most."""
RECORD: Final = "browser-relay.json"
CONNECTIONS: Final = 16
"""Calls served at once; a commit of an outside agent holds one while it waits for the person."""
CONNECT_SECONDS: Final = 2.0
REQUEST_SECONDS: Final = 2.0
"""The whole request must be here by then, however it is sent: a slow sender holds no place."""
ANSWER_SECONDS: Final = 180.0
"""Longer than the 120 seconds an outside agent's commit waits for the person's click."""
_SOL_LOCAL: Final = 0
_LOCAL_PEERCRED: Final = 0x001
_XUCRED_SIZE: Final = 76
_REQUEST_KEYS: Final = frozenset({"kind", "session", "tool", "arguments"})
_OBJECT = TypeAdapter(dict[str, JsonValue])

RelayHandler = Callable[[str, str, str, dict[str, JsonValue]], dict[str, JsonValue]]
"""`(kind, session, tool, arguments)` to the answer; the holder of the bridge runs its own tool by that name."""

_serving: ContextVar[bool] = ContextVar("hz_browser_relay_serving", default=False)


def serving() -> bool:
    """Whether this call already is a relayed one: it is run here and never sent on."""
    return _serving.get()


@contextmanager
def _served() -> Generator[None]:
    token = _serving.set(True)
    try:
        yield
    finally:
        _serving.reset(token)


def _record_directory(state_dir: Path) -> Path:
    return state_dir / "ui"


def _receive(connection: socket.socket, limit: int, seconds: float) -> bytes:
    """One line of at most `limit` bytes, all of it within `seconds`: a sender that trickles bytes is cut off.

    Raises:
        ValueError: If it is too long, not one line, or not here in time.
        OSError: If the connection fails.
    """
    deadline = time.monotonic() + seconds
    data = bytearray()
    while len(data) <= limit:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            msg = "The local message took too long."
            raise ValueError(msg)
        connection.settimeout(remaining)
        try:
            chunk = connection.recv(min(65536, limit + 1 - len(data)))
        except TimeoutError:
            msg = "The local message took too long."
            raise ValueError(msg) from None
        if not chunk:
            break
        data.extend(chunk)
        if chunk.endswith(b"\n"):
            break
    if len(data) > limit or not data.endswith(b"\n") or data.count(b"\n") != 1:
        msg = "Invalid local message."
        raise ValueError(msg)
    return bytes(data)


def peer_uid(connection: socket.socket) -> int | None:
    """The user id of the process at the other end of a Unix socket, as the kernel reports it; None if unknown."""
    if connection.family != socket.AF_UNIX:
        return None  # Linux answers SO_PEERCRED on a TCP socket with uid -1: there is no process to name
    try:
        if hasattr(socket, "SO_PEERCRED"):  # Linux: struct ucred {pid, uid, gid}
            size = struct.calcsize("3i")
            peercred: int = getattr(socket, "SO_PEERCRED")  # noqa: B009  (absent from the stubs on macOS)
            return int(struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, peercred, size))[1])
        # macOS and the BSDs: LOCAL_PEERCRED at level SOL_LOCAL gives struct xucred {version, uid, ...}
        credentials = connection.getsockopt(_SOL_LOCAL, _LOCAL_PEERCRED, _XUCRED_SIZE)
        return int(struct.unpack_from("2I", credentials)[1])
    except (OSError, struct.error):
        return None


class BrowserRelay:
    """The owner-only socket of the process that holds the desktop's bridge; open while a desktop is connected."""

    def __init__(self, state_dir: Path, handler: RelayHandler) -> None:
        """Bind a fresh socket and publish its name; a stale entry is never adopted or removed.

        Raises:
            HeinzelError: If the socket's or the record's directory is unsafe.
            OSError: If the socket cannot be bound.
        """
        self._directory = _record_directory(state_dir)
        name = f"brw-{secrets.token_hex(6)}.sock"
        self.path = private_socket_directory(self._directory) / name
        private_directory(self.path.parent)
        self._handler = handler
        self._slots = threading.BoundedSemaphore(CONNECTIONS)
        self._closed = threading.Event()
        self._socket = socket.socket(socket.AF_UNIX)
        try:
            self._socket.bind(str(self.path))
            self.path.chmod(0o600)
            self._identity = self.path.lstat().st_ino
            self._socket.listen(CONNECTIONS)
            self._socket.settimeout(0.2)
            self._record = json.dumps({"socket": name, "pid": os.getpid()})
            private_directory(self._directory)
            private_write(self._directory / RECORD, self._record)
        except BaseException:
            self._socket.close()
            self._unlink()
            raise
        self._thread = threading.Thread(target=self._run, name="hz-browser-relay", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._closed.is_set():
            try:
                connection, _ = self._socket.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            if not self._slots.acquire(blocking=False):
                connection.close()  # busy: the caller reads no answer and says so
                continue
            # A fresh context per call: nothing of one caller (its identity, its agent) reaches the next.
            worker = threading.Thread(target=copy_context().run, args=(self._serve, connection), daemon=True)
            worker.start()

    def _serve(self, connection: socket.socket) -> None:
        try:
            with connection:
                if peer_uid(connection) != os.getuid():
                    return  # the folder already keeps others out; this holds if a descriptor was passed along
                request = _OBJECT.validate_json(_receive(connection, REQUEST_LIMIT, REQUEST_SECONDS))
                kind, session, tool, arguments = (request.get(key) for key in ("kind", "session", "tool", "arguments"))
                if (
                    frozenset(request) != _REQUEST_KEYS
                    or not isinstance(kind, str)
                    or not isinstance(session, str)
                    or not isinstance(tool, str)
                    or not isinstance(arguments, dict)
                ):
                    return
                with _served():
                    answer = json.dumps(self._handler(kind, session, tool, arguments)).encode() + b"\n"
                if len(answer) <= ANSWER_LIMIT:
                    connection.settimeout(ANSWER_SECONDS)
                    connection.sendall(answer)
        except (OSError, ValueError, HeinzelError):
            return
        finally:
            self._slots.release()

    def _unlink(self) -> None:
        try:
            info = self.path.lstat()
            if stat.S_ISSOCK(info.st_mode) and info.st_ino == getattr(self, "_identity", info.st_ino):
                self.path.unlink()
        except FileNotFoundError:
            pass

    def close(self) -> None:
        """Stop serving and remove only this relay's own socket and record."""
        self._closed.set()
        self._socket.close()
        self._thread.join(timeout=2)
        self._unlink()
        try:
            path = self._directory / RECORD
            if private_read(path) == self._record:
                path.unlink()
        except (FileNotFoundError, HeinzelError, OSError):
            pass


def relay(  # noqa: PLR0911 - every way there is nobody to ask ends the same
    state_dir: Path, kind: str, session: str, tool: str, arguments: dict[str, JsonValue]
) -> dict[str, JsonValue] | None:
    """Ask the process that holds the desktop's bridge to run one browser tool; None if there is none to ask.

    None means no desktop is connected anywhere (or this call is itself a relayed one): the caller then answers as
    a process without a desktop does. An answer is `{"error": text}`, `{"text": text}`, `{"image": {...}}` or
    `{"result": {...}}`.
    """
    if serving():
        return None
    directory = _record_directory(state_dir)
    try:
        record = _OBJECT.validate_json(private_read(directory / RECORD))
        name = record.get("socket")
        if not isinstance(name, str) or Path(name).name != name or not name.startswith("brw-"):
            return None
        path = private_socket_directory(directory) / name
        private_directory(path.parent, create=False)
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            return None
        request = json.dumps({"kind": kind, "session": session, "tool": tool, "arguments": arguments}).encode()
        if len(request) >= REQUEST_LIMIT:
            return {"error": "This browser call is too large to pass on."}
        connection = socket.socket(socket.AF_UNIX)
        connection.settimeout(CONNECT_SECONDS)
        connection.connect(str(path))
    except (OSError, ValueError, ValidationError, HeinzelError):
        return None  # no record, a stale one, or nobody listening: no desktop is connected
    # Somebody took the call: from here on a failure is a failure, never "there is no desktop".
    try:
        with connection:
            connection.sendall(request + b"\n")
            return _OBJECT.validate_json(_receive(connection, ANSWER_LIMIT, ANSWER_SECONDS))
    except (OSError, ValueError, ValidationError):
        return {"error": "The browser call got no answer from hz; the person can see the log. Try again."}
