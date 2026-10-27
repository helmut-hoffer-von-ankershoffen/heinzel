"""The web head's terminal: the user's login shell in a pseudo-terminal, over a WebSocket at `/api/terminal`.

A browser can't put headers on a WebSocket, so the session token arrives as the first message, `{"token": ...}`,
within a few seconds, and is compared in constant time; before that the connection must pass the same checks as
every API request (a loopback Host on our port, our own Origin, no cross-site `Sec-Fetch-Site`). A browser always
sends an Origin with a WebSocket, so one without it is refused too. After that, binary frames are keystrokes and
text frames are JSON control messages, `{"resize": [cols, rows]}`; the shell's output comes back as binary frames.
Both directions are bounded: a shell that prints faster than the browser reads is paused by the pseudo-terminal,
and a browser that types faster than the shell reads stops being read. Closing the socket hangs up the shell's
whole process group (ADR 0048). When `hz ui` stops, each terminal hangs up its shell the same way and closes its
socket with "going away" and a reason the app shows (`Stopping`).

POSIX only: on Windows the socket closes with a reason the app shows.
"""

import asyncio
import contextlib
import hmac
import json
import os
import shutil
import signal
import struct
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.websockets import WebSocketDisconnect

from heinzel.heads import api
from heinzel.heads.web._shutdown import Stopping

if os.name == "posix":
    import fcntl
    import pty
    import termios

POLICY_VIOLATION = 1008
UNSUPPORTED = 1003
TRY_AGAIN_LATER = 1013
GOING_AWAY = 1001
STOPPED = "The web app stopped. Run hz ui to start it again."
RESTARTED = "The web app is restarting. Open the terminal again in a moment."
"""Close reasons the app shows as they are, so plain text: Ctrl-C stopped `hz ui`, or it restarts (ADR 0047)."""
TOKEN_SECONDS = 5.0
READ_BYTES = 65536
OUTPUT_CHUNKS = 64
"""Reads of the shell's output queued for the browser at most; when full, the shell blocks on its next write."""
PENDING_INPUT = 1 << 20
"""Keystroke bytes waiting for the shell at most; beyond this the browser's next frames wait."""
SHELLS = 8
"""Terminals open at once at most; one more is refused until another closes."""
HANG_UP_SECONDS = 2.0
"""How long the shell gets to end after SIGHUP before its process group is killed."""
SIZE_LIMIT = 1000
"""Columns and rows larger than this are refused; no screen has them."""
COLOURS = {"TERM": "xterm-256color", "COLORTERM": "truecolor", "CLICOLOR": "1", "TERM_PROGRAM": "heinzel"}
"""What a desktop terminal tells its programs, so they colour their output (`CLICOLOR` for macOS `ls`)."""

_ACQUIRE_TERMINAL = (
    "import fcntl, os, sys, termios; fcntl.ioctl(0, termios.TIOCSCTTY, 0); os.execv(sys.argv[1], sys.argv[1:])"
)
"""Run by a fresh interpreter in the new session: take stdin as the controlling terminal, then become the shell.

The forked child itself runs no Python (`preexec_fn` is unsafe once the server has threads); `start_new_session`
makes it a session leader in C, and this exec'd interpreter acquires the terminal, which both macOS and Linux
need `TIOCSCTTY` for."""

_open: set["_Terminal"] = set()
"""The terminals open now, to cap how many run at once."""


class OriginSessions:
    """Track terminals owned by one web server and revoke those whose approved origin was removed."""

    def __init__(self) -> None:
        """Start with no connected terminals."""
        self._terminals: set[_Terminal] = set()
        self._lock = threading.Lock()

    def add(self, terminal: "_Terminal") -> None:
        """Track one authenticated terminal."""
        with self._lock:
            self._terminals.add(terminal)

    def discard(self, terminal: "_Terminal") -> None:
        """Stop tracking a disconnected terminal."""
        with self._lock:
            self._terminals.discard(terminal)

    def revoke_origins(self, removed: frozenset[str]) -> None:
        """Close only active terminals whose external origin is no longer permitted."""
        with self._lock:
            terminals = tuple(self._terminals)
        for terminal in terminals:
            if terminal.origin in removed:
                terminal.revoke()


def shell() -> str:
    """The user's login shell: `$SHELL` when it is an absolute, executable path, else `/bin/sh`."""
    candidate = os.environ.get("SHELL", "")
    if Path(candidate).is_absolute() and os.access(candidate, os.X_OK):
        return candidate
    return shutil.which("sh") or "/bin/sh"


def environment() -> dict[str, str]:
    """The shell's environment: the user's, without hz's virtual environment and with a desktop terminal's colours.

    `uv run hz ui` puts hz's own environment first on `PATH` and sets `VIRTUAL_ENV` and `UV_*`; left in, the user's
    `python` and `pip` would be hz's. `NO_COLOR` is dropped because a terminal colours by default.
    """
    dropped = {"VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT", "NO_COLOR"}
    env = {key: value for key, value in os.environ.items() if key not in dropped and not key.startswith("UV_")}
    if sys.prefix != sys.base_prefix:
        ours = os.path.normpath(Path(sys.prefix) / "bin")
        kept = [entry for entry in env.get("PATH", "").split(os.pathsep) if entry and os.path.normpath(entry) != ours]
        env["PATH"] = os.pathsep.join(kept)
    return env | COLOURS


def add_route(  # noqa: PLR0913 - the app plus explicit terminal authentication, origin and shutdown policy
    app: FastAPI,
    *,
    token: str,
    port: int | None,
    allowed_origins: frozenset[str] | Callable[[], frozenset[str]] = frozenset(),
    accept: Callable[[str], object],
    stopping: Stopping | None = None,
    sessions: OriginSessions | None = None,
) -> None:
    """Serve the terminal at `/api/terminal` on `app`; register it before mounting the API there."""

    @app.websocket("/api/terminal")
    async def terminal(websocket: WebSocket) -> None:
        await session(
            websocket,
            token=token,
            port=port,
            allowed_origins=allowed_origins,
            accept=accept,
            stopping=stopping,
            sessions=sessions,
        )


async def session(  # noqa: PLR0913 - the socket plus explicit terminal authentication, origin and shutdown policy
    websocket: WebSocket,
    *,
    token: str,
    port: int | None,
    allowed_origins: frozenset[str] | Callable[[], frozenset[str]] = frozenset(),
    accept: Callable[[str], object] | None = None,
    stopping: Stopping | None = None,
    sessions: OriginSessions | None = None,
) -> None:
    """Serve one terminal on `websocket` until either side closes it, or the server stops.

    `accept` admits another token besides the session's: the web head's paired devices (ADR 0046), so the installed
    app's terminal works after it reopens without a new link. `stopping` closes the terminal when the server stops.
    """
    headers = websocket.headers
    origin = headers.get("origin")
    request_origins = allowed_origins() if callable(allowed_origins) else allowed_origins
    problem = api.request_refusal(
        headers,
        api.LOOPBACK,
        port,
        allowed_origins=request_origins,
        host_help=api.WEB_HOST_HELP,
    )
    if problem or origin is None:
        await websocket.close(code=POLICY_VIOLATION, reason="Refused: open the terminal from the hz ui app.")
        return
    await websocket.accept()
    if not await _authenticated(websocket, token, accept):
        await websocket.close(code=POLICY_VIOLATION, reason="Refused: the session token is missing or wrong.")
        return
    if os.name != "posix":
        await websocket.close(code=UNSUPPORTED, reason="The terminal needs macOS or Linux.")
        return
    if len(_open) >= SHELLS:
        await websocket.close(code=TRY_AGAIN_LATER, reason=f"{SHELLS} terminals are open; close one and try again.")
        return
    active_origins = allowed_origins() if callable(allowed_origins) else allowed_origins
    origin = _external_origin(headers.get("host", ""), origin, active_origins)
    terminal = _Terminal(websocket, origin=origin)
    _open.add(terminal)
    if sessions is not None:
        sessions.add(terminal)
    try:
        stopping = stopping or Stopping()
        with stopping.watch() as stopped:
            await terminal.run(stopped, stopping)
    finally:
        _open.discard(terminal)
        if sessions is not None:
            sessions.discard(terminal)


def _external_origin(host: str, origin: str | None, allowed_origins: frozenset[str]) -> str | None:
    """Mark only a configured non-loopback authority for later revocation; local browser sessions stay local."""
    if origin not in allowed_origins:
        return None
    name = host.lower().split("]", maxsplit=1)[0] + "]" if host.startswith("[") else host.partition(":")[0].lower()
    return origin if name not in api.LOOPBACK else None


async def _authenticated(websocket: WebSocket, token: str, accept: Callable[[str], object] | None) -> bool:
    try:
        first = await asyncio.wait_for(websocket.receive_text(), TOKEN_SECONDS)
        given = json.loads(first).get("token", "")
    except (TimeoutError, WebSocketDisconnect, ValueError, AttributeError, KeyError, RuntimeError):
        return False
    if not isinstance(given, str):
        return False
    return hmac.compare_digest(given.encode(), token.encode()) or (accept is not None and bool(accept(given)))


class _Terminal:
    """One shell on one pseudo-terminal, joined to one WebSocket with bounded buffers both ways."""

    def __init__(self, websocket: WebSocket, *, origin: str | None = None) -> None:
        self.websocket = websocket
        self.origin = origin
        self.loop = asyncio.get_running_loop()
        self.output: asyncio.Queue[bytes] = asyncio.Queue(maxsize=OUTPUT_CHUNKS)
        self.paused = False
        """Whether reading the shell's output stopped because the queue is full."""
        self.pending = bytearray()
        """Keystrokes the pseudo-terminal didn't take yet."""
        self.drained = asyncio.Event()
        self.drained.set()
        self.main = -1
        self.revoked = asyncio.Event()
        self._revoked = False
        self._revocation_task: asyncio.Task[None] | None = None

    def revoke(self) -> None:
        """Close this socket on its own event loop so the receiver hangs up the shell process group."""
        with contextlib.suppress(RuntimeError):
            self.loop.call_soon_threadsafe(self._schedule_revocation)

    def _schedule_revocation(self) -> None:
        if not self._revoked:
            self._revoked = True
            self.revoked.set()
            self._revocation_task = self.loop.create_task(self._close_for_revoked_origin())

    async def _close_for_revoked_origin(self) -> None:
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await self.websocket.close(code=POLICY_VIOLATION, reason="This origin is no longer allowed.")

    async def run(self, stopped: asyncio.Event, stopping: Stopping) -> None:
        self.main, process = _spawn()
        self.loop.add_reader(self.main, self._readable)
        sender = asyncio.create_task(self._send())
        closer = asyncio.create_task(self._close_when(stopped, stopping))
        receiver = asyncio.create_task(self._receive())
        revoked = asyncio.create_task(self.revoked.wait())
        try:
            done, _ = await asyncio.wait((receiver, revoked), return_when=asyncio.FIRST_COMPLETED)
            if revoked in done and not receiver.done():
                receiver.cancel()
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError, OSError):
                await receiver
        finally:
            self.loop.remove_reader(self.main)
            self.loop.remove_writer(self.main)
            sender.cancel()
            closer.cancel()
            revoked.cancel()
            try:
                with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError, OSError):
                    await sender
                with contextlib.suppress(asyncio.CancelledError, RuntimeError, OSError):
                    await closer
                with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError, OSError):
                    await revoked
                if self._revocation_task is not None:
                    with contextlib.suppress(WebSocketDisconnect, RuntimeError, OSError):
                        await self._revocation_task
            finally:
                await _hang_up(process, self.main)

    async def _close_when(self, stopped: asyncio.Event, stopping: Stopping) -> None:
        """Close the socket as the server stops or restarts; the browser's answer to the close ends `_receive`."""
        await stopped.wait()
        await self.websocket.close(code=GOING_AWAY, reason=STOPPED if stopping.interrupted else RESTARTED)

    def _readable(self) -> None:
        try:
            data = os.read(self.main, READ_BYTES)
        except BlockingIOError:
            return
        except OSError:  # EIO on Linux once the shell's side is closed: the same as end of file
            data = b""
        self.output.put_nowait(data)
        if not data or self.output.full():
            self.loop.remove_reader(self.main)
            self.paused = bool(data)

    async def _send(self) -> None:
        """The shell's output to the browser; an empty read means the shell ended."""
        while data := await self.output.get():
            if self.paused:
                self.paused = False
                self.loop.add_reader(self.main, self._readable)
            await self.websocket.send_bytes(data)
        self.drained.set()  # nothing reads keystrokes any more; let the receiver see the socket close
        await self.websocket.close(reason="The shell ended.")

    async def _receive(self) -> None:
        """Keystrokes and control messages from the browser, until it goes away."""
        with contextlib.suppress(WebSocketDisconnect, RuntimeError):
            while True:
                await self.drained.wait()
                message = await self.websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                if (data := message.get("bytes")) is not None:
                    self._write(data)
                elif (text := message.get("text")) is not None:
                    _control(self.main, text)

    def _write(self, data: bytes) -> None:
        """Queue keystrokes for the shell; what the pseudo-terminal can't take now is written when it can."""
        self.pending += data
        if len(self.pending) == len(data):
            self._writable()

    def _writable(self) -> None:
        try:
            written = os.write(self.main, self.pending)
        except BlockingIOError:
            written = 0
        except OSError:  # the shell's side is closed; its output reader ends the session
            written = len(self.pending)
        del self.pending[:written]
        if self.pending:
            self.loop.add_writer(self.main, self._writable)
            if len(self.pending) >= PENDING_INPUT:
                self.drained.clear()
        else:
            self.loop.remove_writer(self.main)
            self.drained.set()


def _spawn() -> tuple[int, "subprocess.Popen[bytes]"]:
    """The login shell on a new pseudo-terminal, in a session of its own with that terminal as its controlling one.

    A plain `Popen`, not asyncio's: reaping it must also work while the session is being cancelled (see `_hang_up`).
    """
    main, child = pty.openpty()
    try:
        process = subprocess.Popen(  # noqa: S603 - our own interpreter and the user's own shell, arguments only
            [sys.executable, "-I", "-S", "-c", _ACQUIRE_TERMINAL, shell(), "-l"],
            stdin=child,
            stdout=child,
            stderr=child,
            cwd=Path.home(),
            env=environment(),
            start_new_session=True,
        )
    finally:
        os.close(child)
    os.set_blocking(main, False)
    return main, process


async def _hang_up(process: "subprocess.Popen[bytes]", main: int) -> None:
    """SIGHUP to the shell's process group, as closing a terminal window does; SIGKILL if it lingers.

    Cancelled while waiting (the server shutting down), the group is killed and reaped at once, so no shell outlives
    the terminal.
    """
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGHUP)
    os.close(main)
    try:
        await asyncio.to_thread(_reap, process, HANG_UP_SECONDS)
    finally:
        _reap(process, 0)


def _reap(process: "subprocess.Popen[bytes]", seconds: float) -> None:
    """Wait up to `seconds` for the shell to end, then SIGKILL its process group and wait for that."""
    try:
        process.wait(seconds)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def _control(main: int, text: str) -> None:
    """Apply a control message; anything malformed is ignored."""
    try:
        cols, rows = json.loads(text)["resize"]
    except (ValueError, KeyError, TypeError):
        return
    if not (isinstance(cols, int) and isinstance(rows, int) and 0 < cols <= SIZE_LIMIT and 0 < rows <= SIZE_LIMIT):
        return
    fcntl.ioctl(main, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
