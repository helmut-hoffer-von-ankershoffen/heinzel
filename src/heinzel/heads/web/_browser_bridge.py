"""The web head's end of the browser bridge: the desktop app's socket at `/api/browser-bridge` (ADR 0162).

The desktop app's main process dials this socket once it started the local service. hz then sends it the commands of
the browser operations and waits for its answers; the web page is not on this path. While a desktop is connected, it
is the link of the kernel's `BrowserBridge`; without one every browser operation says it needs the desktop app.

Who may connect: only the desktop app that started this service. The handshake passes the same checks as every API
request (a loopback Host on our port, our own Origin, no cross-site `Sec-Fetch-Site`), and an origin shared for the
web app (ADR 0084) is not enough here. The first frame must be exactly
`{"hello": "hz-browser-bridge", "version": 1, "token": <the bridge token>}`.

The bridge token is not the session token. The desktop app's main process makes it and hands it to the service it
starts in the variable `HEINZEL_UI_BROWSER_BRIDGE`, which `hz ui serve` reads and removes at start (and passes on
when it restarts itself). It is in no page, no URL, no file and no API answer, so a script in the web app, which
holds the session token, cannot pose as the browser; neither can a paired device (ADR 0046). A service that was not
started by the desktop app has no bridge token, and this route then refuses everyone. hz answers
`{"ready": true, "version": 1}`. A newer desktop replaces an older one, whose socket is closed.

Frames after that, bridge protocol v1 (`desktop/src/browser-bridge.js`), exact keys both ways:

- hz sends `{"id", "command", "agent": {"id", "name", "kind"}, "args"}`.
- The desktop answers `{"id", "ok": true, "result": {...}}` or `{"id", "ok": false, "error": {"code", "message"}}`.
- The desktop reports `{"event", "agent", "tab"}` when the person takes over, hands back, stops an agent or a tab
  closes. Nothing consumes them yet; they are checked and dropped.

Any other frame ends the connection. An operation waits for its answer in its own thread; the socket lives on the
server's event loop.
"""

import asyncio
import concurrent.futures
import contextlib
import hashlib
import hmac
import json
import re
import secrets
import threading
from collections.abc import Callable
from typing import Final, cast

from fastapi import FastAPI, WebSocket
from fastapi.websockets import WebSocketDisconnect
from pydantic import JsonValue

from heinzel.heads import api
from heinzel.heads.web._shutdown import Stopping
from heinzel.kernel import HeinzelError, Kernel
from heinzel.kernel._browser import NEEDS_DESKTOP, BrowserBridge, BrowserCommand, BrowserError

PATH: Final = "/api/browser-bridge"
VERSION: Final = 1
HELLO: Final = "hz-browser-bridge"
POLICY_VIOLATION: Final = 1008
PROTOCOL_ERROR: Final = 1002
GOING_AWAY: Final = 1001
HELLO_SECONDS: Final = 5.0
ANSWER_SECONDS: Final = 60.0
"""How long one command may take in the desktop; a navigation waits for its page for 15 seconds at most."""
CONFIRM_SLACK_SECONDS: Final = 10.0
"""Added to the seconds a commit card may wait for the person, so the desktop's own answer arrives first."""
PENDING: Final = 64
"""Commands waiting for the desktop at once at most, as many as the desktop runs."""
EVENTS: Final = frozenset({"take-over", "hand-back", "stop", "closed"})
CODES: Final = frozenset(
    {
        "invalid",
        "unknown_tab",
        "limit",
        "refused_url",
        "paused",
        "stopped",
        "stale_ref",
        "changed",
        "never",
        "rate",
        "timeout",
        "failed",
    }
)
_AGENT_ID: Final = re.compile(r"[A-Za-z0-9_.:-]{1,80}")
_TOKEN: Final = re.compile(r"[A-Za-z0-9_-]{32,256}")
_CONTROL: Final = re.compile(r"[\x00-\x1f\x7f]")
_NAME_LENGTH: Final = 60
_MESSAGE_LENGTH: Final = 500


def kernel_bridge(kernel: Kernel) -> BrowserBridge | None:
    """The bridge the browser operations use, or None while their module is not loaded."""
    try:
        return kernel.resolve(BrowserBridge)
    except HeinzelError:
        return None


def bridge_token(given: str | None, session_token: str) -> str | None:
    """The bridge token the desktop app handed over, or None: absent, malformed, or the session token itself."""
    if given is None or not _TOKEN.fullmatch(given) or hmac.compare_digest(given.encode(), session_token.encode()):
        return None
    return given


class BridgeSlot:
    """The one bridge token a server accepts: the starting desktop app's, or the one `hz ui attach` registered last."""

    def __init__(self, session_token: str, given: str | None) -> None:
        """Start with the token handed over in the environment, if valid."""
        self._session = session_token
        self._token = bridge_token(given, session_token)

    def current(self) -> str | None:
        """The token a bridge socket must present now; None refuses every one."""
        return self._token

    def register(self, given: str) -> bool:
        """Replace the token with a valid one from an attaching desktop app; refuse malformed ones."""
        valid = bridge_token(given, self._session)
        if valid is not None:
            self._token = valid
        return valid is not None


def wire_agent(actor: str, kind: str) -> dict[str, JsonValue]:
    """The agent as the desktop takes it: an id in its alphabet and the name its tabs show.

    The id starts with the kind, so a Heinzel's session and an outside agent never share tabs whatever they are
    called. An actor name outside the id's alphabet or too long for it gets a stable id made from it, so no two
    actors share tabs and none is refused for its name.
    """
    name = _CONTROL.sub(" ", actor).strip()[:_NAME_LENGTH] or kind
    identifier = f"{kind}:{actor}"
    if not _AGENT_ID.fullmatch(identifier):
        identifier = f"{kind}:x-{hashlib.sha256(actor.encode()).hexdigest()[:32]}"
    return {"id": identifier, "name": name, "kind": kind}


def wire_command(identifier: str, command: BrowserCommand) -> dict[str, JsonValue]:
    """One `BrowserCommand` as a frame of bridge protocol v1."""
    args: dict[str, JsonValue] = dict(command.arguments)
    if command.tab is not None:
        args["tab"] = command.tab
    return {
        "id": identifier,
        "command": command.action,
        "agent": wire_agent(command.actor, command.kind),
        "args": args,
    }


class _Desktop:
    """One connected desktop app: the `BrowserLink` the operations call, and the answers its socket brings."""

    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket
        self.loop = asyncio.get_running_loop()
        self.replaced = asyncio.Event()
        self._pending: dict[str, concurrent.futures.Future[dict[str, JsonValue]]] = {}
        self._lock = threading.Lock()
        self._closed = False

    def __call__(self, command: BrowserCommand) -> dict[str, JsonValue]:
        """Send one command and wait for its answer.

        Raises:
            BrowserError: If the desktop refuses, is gone, is busy, or does not answer in time.
        """
        with contextlib.suppress(RuntimeError):
            if asyncio.get_running_loop() is self.loop:
                # Waiting here would stop the very loop that brings the answer.
                msg = "The browser cannot be used from this place in hz."
                raise BrowserError(msg, code="failed")
        identifier = secrets.token_hex(8)
        answer: concurrent.futures.Future[dict[str, JsonValue]] = concurrent.futures.Future()
        with self._lock:
            if self._closed:
                raise BrowserError(NEEDS_DESKTOP, code="unavailable")
            if len(self._pending) >= PENDING:
                msg = "Too many browser calls at once. Try again in a moment."
                raise BrowserError(msg, code="rate")
            self._pending[identifier] = answer
        seconds = command.arguments.get("seconds") if command.action == "confirm" else None
        wait = ANSWER_SECONDS + (float(seconds) + CONFIRM_SLACK_SECONDS if isinstance(seconds, int | float) else 0.0)
        try:
            frame = json.dumps(wire_command(identifier, command))
            asyncio.run_coroutine_threadsafe(self.websocket.send_text(frame), self.loop)
            return answer.result(wait)
        except concurrent.futures.TimeoutError:
            msg = "The desktop app did not answer in time."
            raise BrowserError(msg, code="timeout") from None
        except RuntimeError:  # the loop is closed: the server stopped
            raise BrowserError(NEEDS_DESKTOP, code="unavailable") from None
        finally:
            with self._lock:
                self._pending.pop(identifier, None)

    def close(self) -> None:
        """Fail every command still waiting; later ones are refused at once."""
        with self._lock:
            self._closed = True
            waiting = list(self._pending.values())
            self._pending.clear()
        for answer in waiting:
            if not answer.done():
                answer.set_exception(BrowserError(NEEDS_DESKTOP, code="unavailable"))

    def take(self, frame: object) -> bool:
        """Handle one frame of the desktop; False when it is none the protocol knows."""
        if not isinstance(frame, dict):
            return False
        message = cast("dict[str, object]", frame)
        if set(message) == {"event", "agent", "tab"}:
            tab = message["tab"]
            return message["event"] in EVENTS and isinstance(message["agent"], str) and isinstance(tab, str | None)
        identifier = message.get("id")
        if not isinstance(identifier, str):
            return False
        outcome = _outcome(message)
        if outcome is None:
            return False
        with self._lock:
            answer = self._pending.get(identifier)
        # An answer nobody waits for any more (its call timed out) is dropped.
        if answer is not None and not answer.done():
            if isinstance(outcome, BrowserError):
                answer.set_exception(outcome)
            else:
                answer.set_result(outcome)
        return True


def _outcome(message: dict[str, object]) -> dict[str, JsonValue] | BrowserError | None:
    """The result or the refusal an answer frame carries; None when it is no answer frame."""
    if set(message) == {"id", "ok", "result"} and message["ok"] is True and isinstance(message["result"], dict):
        return cast("dict[str, JsonValue]", message["result"])
    error = message.get("error")
    if set(message) != {"id", "ok", "error"} or message["ok"] is not False or not isinstance(error, dict):
        return None
    detail = cast("dict[str, object]", error)
    code, text = detail.get("code"), detail.get("message")
    if set(detail) != {"code", "message"} or not isinstance(code, str) or not isinstance(text, str):
        return None
    # The desktop wrote the message for the agent; a code it does not have is its plain failure.
    return BrowserError(_CONTROL.sub(" ", text)[:_MESSAGE_LENGTH], code=code if code in CODES else "failed")


def add_route(
    app: FastAPI,
    *,
    token: str | Callable[[], str | None] | None,
    port: int | None,
    bridge: Callable[[], BrowserBridge | None],
    stopping: Stopping | None = None,
) -> None:
    """Add `/api/browser-bridge` to `app`.

    `token` is the bridge token, or None when no desktop app started or attached to this service; a callable reads
    the token a desktop app registered later through `hz ui attach`. `bridge()` is the kernel's bridge,
    or None while the browser module is not loaded.
    """
    current: list[_Desktop] = []

    @app.websocket(PATH)
    async def browser_bridge(websocket: WebSocket) -> None:
        given = token() if callable(token) else token
        await session(websocket, token=given, port=port, bridge=bridge(), stopping=stopping, current=current)


async def session(  # noqa: PLR0913 - the socket plus explicit authentication, the bridge and shutdown policy
    websocket: WebSocket,
    *,
    token: str | None,
    port: int | None,
    bridge: BrowserBridge | None,
    stopping: Stopping | None = None,
    current: list["_Desktop"] | None = None,
) -> None:
    """Serve one desktop app on `websocket` until either side closes it, a newer one connects or the server stops.

    `current` holds the desktop connected through this route, so a newer one can end it.
    """
    headers = websocket.headers
    problem = api.request_refusal(headers, api.LOOPBACK, port, host_help=api.WEB_HOST_HELP)
    if token is None or bridge is None or problem or headers.get("origin") is None:
        await websocket.close(code=POLICY_VIOLATION, reason="Refused: only the desktop app on this computer connects.")
        return
    await websocket.accept()
    if not await _hello(websocket, token):
        await websocket.close(code=POLICY_VIOLATION, reason="Refused: the hello frame or the bridge token is wrong.")
        return
    desktop = _Desktop(websocket)
    holder = current if current is not None else []
    for older in holder:
        older.replaced.set()
    holder[:] = [desktop]
    await websocket.send_text(json.dumps({"ready": True, "version": VERSION}))
    bridge.connect(desktop)
    try:
        with (stopping or Stopping()).watch() as stopped:
            await _serve(desktop, stopped)
    finally:
        bridge.disconnect(desktop)
        desktop.close()
        if desktop in holder:
            holder.remove(desktop)


async def _hello(websocket: WebSocket, token: str) -> bool:
    try:
        first: object = json.loads(await asyncio.wait_for(websocket.receive_text(), HELLO_SECONDS))
    except (TimeoutError, WebSocketDisconnect, ValueError, KeyError, RuntimeError):
        return False
    if not isinstance(first, dict):
        return False
    hello = cast("dict[str, object]", first)
    given = hello.get("token")
    return (
        set(hello) == {"hello", "version", "token"}
        and hello["hello"] == HELLO
        and hello["version"] == VERSION
        and not isinstance(hello["version"], bool)
        and isinstance(given, str)
        and hmac.compare_digest(given.encode(), token.encode())
    )


async def _serve(desktop: _Desktop, stopped: asyncio.Event) -> None:
    """Read the desktop's frames until it leaves, is replaced, breaks the protocol or the server stops."""
    websocket = desktop.websocket
    ends = [asyncio.ensure_future(stopped.wait()), asyncio.ensure_future(desktop.replaced.wait())]
    try:
        while True:
            reading = asyncio.ensure_future(websocket.receive_text())
            await asyncio.wait([reading, *ends], return_when=asyncio.FIRST_COMPLETED)
            if not reading.done():
                reading.cancel()
                with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                    await reading
                reason = "A newer desktop app connected." if desktop.replaced.is_set() else "The web app stopped."
                await websocket.close(code=GOING_AWAY, reason=reason)
                return
            try:
                frame: object = json.loads(reading.result())
            except (WebSocketDisconnect, RuntimeError, KeyError):
                return
            except ValueError:
                frame = None
            if not desktop.take(frame):
                await websocket.close(code=PROTOCOL_ERROR, reason="Refused: not a frame of the browser bridge.")
                return
    finally:
        for end in ends:
            end.cancel()
