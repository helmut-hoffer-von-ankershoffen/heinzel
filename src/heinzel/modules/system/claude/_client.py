"""hz as an MCP client: one connection to one found server, over stdio or streamable HTTP (ADR 0162).

Modules don't import the MCP SDK (it is the MCP head's toolkit), so this speaks the protocol itself, as the engine
runner's client does: JSON-RPC 2.0 with `initialize`, `tools/list` and `tools/call`, nothing else. A request from
the server (sampling, roots, elicitation) is answered "method not found": hz grants it nothing more.

Every answer is bounded in size and time. Calls on one connection run one at a time. Nothing the server says is
logged, and a failure's message names the server and what went wrong, never its output.
"""

import contextlib
import json
import os
import queue
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from typing import IO, Any, Protocol, cast

from heinzel import HeinzelError
from heinzel.kernel import clean_text
from heinzel.modules.system.claude._launch import NOT_USABLE, Launch

PROTOCOL = "2025-06-18"
START_TIMEOUT = 30.0
REQUEST_TIMEOUT = 60.0
STOP_TIMEOUT = 2.0
MAX_MESSAGE_BYTES = 4 * 1024 * 1024
MAX_QUEUED_BYTES = 2 * MAX_MESSAGE_BYTES
"""What a server may have sent that hz has not read yet; past it, the server is stopped."""
MAX_RESULT_CHARS = 100_000
MAX_TOOLS = 200
MAX_PAGES = 10
CLIENT = {"name": "hz", "version": "1"}


class McpClientError(HeinzelError):
    """The server failed, timed out or answered something that isn't MCP; the message is safe to show."""


class Client(Protocol):
    """One connection to one server."""

    name: str

    def start(self) -> None:
        """Start or reach the server and complete the handshake."""

    def request(self, method: str, params: dict[str, Any], seconds: float) -> dict[str, Any]:
        """One JSON-RPC request and its result."""
        ...

    def alive(self) -> bool:
        """Whether the connection can still be used."""
        ...

    def stop(self) -> None:
        """End the connection; nothing is left running."""


def handshake(client: Client) -> None:
    """MCP's `initialize`, then its `initialized` notification.

    Raises:
        McpClientError: If the server does not answer as an MCP server.
    """
    client.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": CLIENT}, START_TIMEOUT)


def list_tools(client: Client) -> list[dict[str, Any]]:
    """The server's tools as it lists them, at most `MAX_TOOLS`, each with a string `name`.

    Raises:
        McpClientError: If the listing fails.
    """
    found: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(MAX_PAGES):
        result = client.request("tools/list", {"cursor": cursor} if cursor else {}, REQUEST_TIMEOUT)
        listed = result.get("tools")
        if not isinstance(listed, list):
            msg = f"The MCP server {client.name} listed no tools"
            raise McpClientError(msg)
        rows = [cast("dict[str, Any]", t) for t in cast("list[object]", listed) if isinstance(t, dict)]
        found += [t for t in rows if isinstance(t.get("name"), str)]
        next_cursor = result.get("nextCursor")
        if not isinstance(next_cursor, str) or len(found) >= MAX_TOOLS:
            break
        cursor = next_cursor
    return found[:MAX_TOOLS]


def call_tool(client: Client, tool: str, arguments: dict[str, Any]) -> str:
    """Call `tool` and return its text; an error the tool reports comes back as `Error: ...` text, for the agent.

    Raises:
        McpClientError: If the server fails or times out.
    """
    result = client.request("tools/call", {"name": tool, "arguments": arguments}, REQUEST_TIMEOUT)
    parts: list[str] = []
    for item in cast("list[object]", result.get("content") or []):
        if isinstance(item, dict):
            part = cast("dict[str, object]", item)
            kind = part.get("type")
            parts.append(str(part.get("text", "")) if kind == "text" else f"[{clean_text(str(kind))[:40]} content]")
    text = "\n".join(parts) or json.dumps(result.get("structuredContent", ""), ensure_ascii=False)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + "\n(cut short)"
    return f"Error: {text}" if result.get("isError") is True else text


def _refused(name: str, answer: dict[str, Any]) -> str:
    """Why a request failed, by the error's code if it is a number: nothing else of the server's reaches the text."""
    error = answer.get("error")
    code = cast("dict[str, Any]", error).get("code") if isinstance(error, dict) else None
    shown = str(code) if isinstance(code, int) and not isinstance(code, bool) else "?"
    return f"The MCP server {name} refused the request (code {shown})"


_LATE = b"\x00late"
"""What `StdioClient._take` returns when the deadline passed; no line read from a server is ever this object."""


class StdioClient:
    """A local server process hz started, in a process group of its own; `stop` ends the whole group."""

    def __init__(self, name: str, launch: Launch) -> None:
        """Describe the server `name` and how to start it."""
        self.name = name
        self._launch = launch
        self._process: subprocess.Popen[bytes] | None = None
        self._lines: queue.Queue[bytes | None] = queue.Queue()
        self._queued = 0
        self._overflow = False
        self._count = threading.Lock()
        self._next = 0
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start the process and complete the handshake.

        Raises:
            McpClientError: If it can't start or doesn't answer the handshake.
        """
        try:
            self._process = subprocess.Popen(  # noqa: S603 - the person's own configured server, no shell
                self._launch.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=self._launch.cwd,
                env=self._launch.env,
                start_new_session=True,  # its own process group: `stop` ends what it starts too (npx, then node)
            )
        except OSError as exc:
            msg = f"The MCP server {self.name} couldn't start: {clean_text(exc.strerror or type(exc).__name__)}"
            raise McpClientError(msg) from None
        stdout = self._process.stdout
        if stdout is None:  # pragma: no cover - PIPE always gives one
            msg = f"The MCP server {self.name} has no output"
            raise McpClientError(msg)
        threading.Thread(target=self._read, args=(stdout,), name=f"mcp-{self.name}", daemon=True).start()
        try:
            handshake(self)
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except McpClientError:
            self.stop()
            raise

    def _read(self, stdout: IO[bytes]) -> None:
        """Queue each line for the waiting request, up to `MAX_QUEUED_BYTES`; past that the server is stopped."""
        with contextlib.suppress(OSError, ValueError):
            while line := stdout.readline(MAX_MESSAGE_BYTES + 1):
                if len(line) > MAX_MESSAGE_BYTES:
                    self._overflow = True
                    break
                with self._count:
                    if self._queued + len(line) > MAX_QUEUED_BYTES:
                        self._overflow = True
                        break
                    self._queued += len(line)
                self._lines.put(line)
        if self._overflow:
            self._kill_group()
        self._lines.put(None)

    def _take(self, seconds: float) -> bytes | None:
        """The next line, none when the server stopped, `_LATE` when `seconds` pass first."""
        if seconds <= 0:
            return _LATE
        try:
            line = self._lines.get(timeout=seconds)
        except queue.Empty:
            return _LATE
        if line is not None:
            with self._count:
                self._queued -= len(line)
        return line

    def _drain(self) -> None:
        """Drop what was queued since the last request: no answer to a new request can be among it."""
        while True:
            try:
                line = self._lines.get_nowait()
            except queue.Empty:
                return
            if line is None:
                self._lines.put(None)
                return
            with self._count:
                self._queued -= len(line)

    def alive(self) -> bool:
        """Whether the process still runs."""
        return self._process is not None and self._process.poll() is None

    def request(self, method: str, params: dict[str, Any], seconds: float) -> dict[str, Any]:
        """One request within one deadline; requests from the server get "method not found", notifications go.

        Raises:
            McpClientError: If the server stops, sends too much, misses the deadline or answers an error.
        """
        with self._lock:
            self._drain()
            self._next += 1
            ident = self._next
            deadline = time.monotonic() + seconds
            self._send({"jsonrpc": "2.0", "id": ident, "method": method, "params": params})
            while True:
                line = self._take(deadline - time.monotonic())
                if line is _LATE:
                    self.stop()
                    msg = f"The MCP server {self.name} did not answer within {seconds:.0f} seconds; it was stopped"
                    raise McpClientError(msg) from None
                if line is None:
                    self._lines.put(None)
                    self.stop()
                    msg = (
                        f"The MCP server {self.name} sent more than hz reads at once; it was stopped"
                        if self._overflow
                        else f"The MCP server {self.name} stopped"
                    )
                    raise McpClientError(msg)
                answer = _message(line)
                if answer is None:
                    continue
                if "method" in answer:
                    if "id" in answer:
                        error = {"code": -32601, "message": "hz grants this server nothing beyond its tools"}
                        self._send({"jsonrpc": "2.0", "id": answer["id"], "error": error})
                    continue
                if answer.get("id") != ident:
                    continue
                if "error" in answer:
                    raise McpClientError(_refused(self.name, answer))
                result = answer.get("result")
                return cast("dict[str, Any]", result) if isinstance(result, dict) else {}

    def _send(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            msg = f"The MCP server {self.name} is not running"
            raise McpClientError(msg)
        try:
            process.stdin.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
            process.stdin.flush()
        except (OSError, ValueError):
            msg = f"The MCP server {self.name} stopped"
            raise McpClientError(msg) from None

    def _kill_group(self, sig: int = signal.SIGKILL) -> None:
        """Signal the server's process group, which only hz's own server leads (`start_new_session`)."""
        process = self._process
        if process is not None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, sig)

    def stop(self) -> None:
        """Close its input, then end its whole process group: terminate, then kill."""
        process = self._process
        if process is None:
            return
        with contextlib.suppress(OSError, ValueError):
            if process.stdin is not None:
                process.stdin.close()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(STOP_TIMEOUT)
        # Its children outlive it unless the group is ended (npx then node, uvx then python).
        self._kill_group(signal.SIGTERM)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(STOP_TIMEOUT)
        time.sleep(0.05)
        self._kill_group(signal.SIGKILL)
        self._process = None
        process.wait()
        if process.stdout is not None:
            with contextlib.suppress(OSError, ValueError):
                process.stdout.close()


def _message(line: bytes) -> dict[str, Any] | None:
    """One JSON-RPC message, or none for a line that isn't one (a server's stray output)."""
    try:
        value = json.loads(line)
    except ValueError:
        return None
    return cast("dict[str, Any]", value) if isinstance(value, dict) else None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is refused: the server's headers are for its own address only."""

    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        """Never follow."""
        return


class HttpClient:
    """A streamable HTTP server at a URL, with the fixed headers its entry names; no sign-in of its own."""

    def __init__(self, name: str, launch: Launch) -> None:
        """Describe the server `name` and where it is."""
        self.name = name
        self._launch = launch
        self._session: str | None = None
        self._next = 0
        self._started = False
        self._lock = threading.Lock()
        self._opener = urllib.request.build_opener(_NoRedirect())

    def start(self) -> None:
        """Complete the handshake.

        Raises:
            McpClientError: If the server can't be reached, wants a sign-in, or doesn't answer as MCP.
        """
        handshake(self)
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, START_TIMEOUT)
        self._started = True

    def alive(self) -> bool:
        """Whether the handshake was completed and the connection not stopped."""
        return self._started

    def request(self, method: str, params: dict[str, Any], seconds: float) -> dict[str, Any]:
        """One request, answered as JSON or as an event stream.

        Raises:
            McpClientError: If the server fails, times out or answers an error.
        """
        with self._lock:
            self._next += 1
            ident = self._next
            answer = self._post({"jsonrpc": "2.0", "id": ident, "method": method, "params": params}, seconds, ident)
            if answer is None:
                msg = f"The MCP server {self.name} gave no answer"
                raise McpClientError(msg)
            if "error" in answer:
                raise McpClientError(_refused(self.name, answer))
            result = answer.get("result")
            return cast("dict[str, Any]", result) if isinstance(result, dict) else {}

    def _post(self, message: dict[str, Any], seconds: float, ident: int | None = None) -> dict[str, Any] | None:
        headers = {
            **self._launch.headers,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL,
        }
        if self._session:
            headers["Mcp-Session-Id"] = self._session
        body = json.dumps(message).encode()
        request = urllib.request.Request(self._launch.url, data=body, headers=headers, method="POST")  # noqa: S310 - http(s) only, checked in `launch`
        expired = threading.Event()
        try:
            return self._exchange(request, seconds, ident, expired)
        except urllib.error.HTTPError as exc:
            with exc:  # the error is also the open response (a refused redirect too): closed before anything else
                code = exc.code
            if code in {401, 403}:
                msg = f"The MCP server {self.name} is {NOT_USABLE}: it needs a sign-in held by another app"
                raise McpClientError(msg) from None
            msg = f"The MCP server {self.name} answered HTTP {code}"
            raise McpClientError(msg) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, AttributeError) as exc:
            if expired.is_set():  # closed under the reader at the deadline
                msg = f"The MCP server {self.name} did not answer within {seconds:.0f} seconds"
                raise McpClientError(msg) from None
            if isinstance(exc, ValueError | AttributeError):
                raise
            msg = f"The MCP server {self.name} could not be reached ({type(exc).__name__})"
            raise McpClientError(msg) from None

    def _exchange(
        self, request: urllib.request.Request, seconds: float, ident: int | None, expired: threading.Event
    ) -> dict[str, Any] | None:
        """Send `request` and read its answer within one deadline; `expired` is set if the deadline closed it."""
        deadline = time.monotonic() + seconds
        with self._opener.open(request, timeout=seconds) as response:
            # One deadline for the whole answer: a stream that trickles is closed under the reader when it passes.
            def close() -> None:
                expired.set()
                response.close()

            timer = threading.Timer(max(deadline - time.monotonic(), 0.0), close)
            timer.daemon = True
            timer.start()
            try:
                if session := response.headers.get("Mcp-Session-Id"):
                    self._session = str(session)[:256]
                if ident is None:
                    return None
                kind = str(response.headers.get("Content-Type", ""))
                raw = cast("IO[bytes]", response)
                if kind.startswith("text/event-stream"):
                    return _event_answer(raw, ident, deadline)
                return _json_answer(raw)
            finally:
                timer.cancel()

    def stop(self) -> None:
        """Forget the session; the server is told, when it gave one, and nothing waits for it."""
        session, self._session, self._started = self._session, None, False
        if session is None:
            return
        headers = {**self._launch.headers, "Mcp-Session-Id": session, "MCP-Protocol-Version": PROTOCOL}
        request = urllib.request.Request(self._launch.url, headers=headers, method="DELETE")  # noqa: S310 - http(s) only
        try:
            self._opener.open(request, timeout=STOP_TIMEOUT).close()
        except urllib.error.HTTPError as exc:
            exc.close()  # the server may refuse to end the session; its answer is closed all the same
        except (urllib.error.URLError, TimeoutError, OSError):
            return


def _json_answer(response: IO[bytes]) -> dict[str, Any]:
    """The one JSON-RPC answer of a JSON response.

    Raises:
        McpClientError: If it is too large or not JSON-RPC.
    """
    body = response.read(MAX_MESSAGE_BYTES + 1)
    if len(body) > MAX_MESSAGE_BYTES:
        msg = "The MCP server's answer is too large"
        raise McpClientError(msg)
    answer = _message(body)
    if answer is None:
        msg = "The MCP server's answer is not JSON-RPC"
        raise McpClientError(msg)
    return answer


def _event_answer(response: IO[bytes], ident: int, deadline: float) -> dict[str, Any] | None:
    """The answer to `ident` from an event stream: the `data:` lines of each event, up to the size limit.

    Raises:
        McpClientError: If the stream is longer than the limit or outlasts the deadline.
    """
    read, data = 0, b""
    while line := response.readline(MAX_MESSAGE_BYTES + 1):
        read += len(line)
        if time.monotonic() > deadline:
            msg = "The MCP server did not answer in time"
            raise McpClientError(msg)
        if read > MAX_MESSAGE_BYTES:
            msg = "The MCP server's answer is too large"
            raise McpClientError(msg)
        if line.startswith(b"data:"):
            data += line[5:].strip() + b"\n"
        elif not line.strip() and data:
            answer = _message(data)
            data = b""
            if answer is not None and answer.get("id") == ident and "method" not in answer:
                return answer
    answer = _message(data) if data else None
    return answer if answer is not None and answer.get("id") == ident else None
