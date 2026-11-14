"""A minimal MCP client over stdio for the engine runner: list a local server's tools and call them (ADR 0159).

The engine runner hands a coding session the same local MCP servers the Claude Code runner does (hz's own chat
server, `hz system serve mcp --chat <session>`, above all). Modules don't import the MCP SDK (it is the MCP head's
toolkit) and Pydantic AI's MCP client needs another dependency, so this speaks the protocol's stdio transport
itself: newline-delimited JSON-RPC 2.0, `initialize`, `tools/list` and `tools/call`, nothing else. A request from
the server (sampling, roots, elicitation) is answered "method not found": the session grants it nothing more.

Each server is a process of its own with the run's working directory, the run's environment without hz's
credentials plus the server's own variables, and a bound on every answer's size and time.
"""

import asyncio
import contextlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from heinzel.kernel import clean_text, subprocess_environment

PROTOCOL = "2025-06-18"
REQUEST_TIMEOUT = 60.0
START_TIMEOUT = 30.0
MAX_MESSAGE_BYTES = 4 * 1024 * 1024
MAX_RESULT_CHARS = 100_000
MAX_TOOLS = 200
MAX_PAGES = 10
_NAME = re.compile(r"[^A-Za-z0-9_-]")


class McpError(Exception):
    """The server failed, timed out or answered something that isn't MCP; the message is safe to show."""


def tool_name(server: str, tool: str) -> str:
    """What the model calls a server's tool: `mcp__<server>__<tool>` as Claude Code names it, within API limits."""
    return _NAME.sub("_", f"mcp__{server}__{tool}")[:128]


class StdioMcp:
    """One local MCP server process, started for one run and stopped after it."""

    def __init__(self, name: str, command: list[str], env: Mapping[str, str], cwd: Path) -> None:
        """Describe the server `name`: its command line, its own variables and the run's working directory."""
        self.name = name
        self._command = command
        self._env = {**subprocess_environment(), **env}
        self._cwd = cwd
        self._process: asyncio.subprocess.Process | None = None
        self._next = 0
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Start the process and complete MCP's handshake.

        Raises:
            McpError: If it can't start or doesn't answer the handshake.
        """
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self._command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=self._cwd,
                env=self._env,
                limit=MAX_MESSAGE_BYTES,
            )
        except OSError as exc:
            msg = f"the MCP server {self.name} couldn't start: {clean_text(exc.strerror or type(exc).__name__)}"
            raise McpError(msg) from None
        info = {"name": "hz-engine", "version": "1"}
        await self._call(
            "initialize", {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": info}, START_TIMEOUT
        )
        await self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    async def tools(self) -> list[dict[str, Any]]:
        """The server's tools, as it lists them: `name`, `description`, `inputSchema`; at most `MAX_TOOLS`.

        Raises:
            McpError: If the listing fails.
        """
        found: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(MAX_PAGES):
            result = await self._call("tools/list", {"cursor": cursor} if cursor else {}, REQUEST_TIMEOUT)
            listed = result.get("tools")
            if not isinstance(listed, list):
                msg = f"the MCP server {self.name} listed no tools"
                raise McpError(msg)
            found += [t for t in cast("list[object]", listed) if isinstance(t, dict) and isinstance(t.get("name"), str)]
            next_cursor = result.get("nextCursor")
            if not isinstance(next_cursor, str) or len(found) >= MAX_TOOLS:
                break
            cursor = next_cursor
        return found[:MAX_TOOLS]

    async def call(self, tool: str, arguments: dict[str, Any]) -> str:
        """Call `tool` and return its text content; an error the tool reports is returned as text, for the model.

        Raises:
            McpError: If the server fails or times out.
        """
        result = await self._call("tools/call", {"name": tool, "arguments": arguments}, REQUEST_TIMEOUT)
        parts: list[str] = []
        for item in cast("list[object]", result.get("content") or []):
            if isinstance(item, dict):
                part = cast("dict[str, object]", item)
                parts.append(str(part["text"]) if part.get("type") == "text" else f"[{part.get('type')} content]")
        text = "\n".join(parts) or json.dumps(result.get("structuredContent", ""))
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + "\n(cut short)"
        return f"Error: {text}" if result.get("isError") is True else text

    async def stop(self) -> None:
        """End the process: close its input, then terminate it, then kill it."""
        process, self._process = self._process, None
        if process is None or process.returncode is not None:
            return
        with contextlib.suppress(Exception):
            if process.stdin is not None:
                process.stdin.close()
            await asyncio.wait_for(process.wait(), 2)
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 2)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()

    async def _send(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            msg = f"the MCP server {self.name} is not running"
            raise McpError(msg)
        try:
            process.stdin.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
            await process.stdin.drain()
        except (ConnectionError, RuntimeError) as exc:
            msg = f"the MCP server {self.name} stopped ({type(exc).__name__})"
            raise McpError(msg) from None

    async def _call(self, method: str, params: dict[str, Any], seconds: float) -> dict[str, Any]:
        async with self._lock:  # one request at a time: answers come back in the order they are read
            self._next += 1
            ident = self._next
            await self._send({"jsonrpc": "2.0", "id": ident, "method": method, "params": params})
            try:
                return await asyncio.wait_for(self._answer(ident), seconds)
            except TimeoutError:
                msg = f"the MCP server {self.name} didn't answer {method} within {seconds:g}s"
                raise McpError(msg) from None

    async def _answer(self, ident: int) -> dict[str, Any]:
        process = self._process
        if process is None or process.stdout is None:
            msg = f"the MCP server {self.name} is not running"
            raise McpError(msg)
        while True:
            try:
                line = await process.stdout.readline()
            except (ValueError, asyncio.LimitOverrunError):
                msg = f"the MCP server {self.name} sent a message over {MAX_MESSAGE_BYTES} bytes"
                raise McpError(msg) from None
            if not line:
                msg = f"the MCP server {self.name} stopped"
                raise McpError(msg)
            answer = await self._take(line, ident)
            if answer is not None:
                return answer

    async def _take(self, line: bytes, ident: int) -> dict[str, Any] | None:
        """The result in `line` if it answers request `ident`; None for anything else, after answering a request.

        Raises:
            McpError: If it answers `ident` with an error.
        """
        try:
            message: object = json.loads(line)
        except json.JSONDecodeError:
            return None  # a log line on stdout: not ours to read
        if not isinstance(message, dict):
            return None
        data = cast("dict[str, Any]", message)
        if "method" in data:
            if "id" in data:  # the server asks something of the client: the session grants nothing more
                error = {"code": -32601, "message": "not supported by this client"}
                await self._send({"jsonrpc": "2.0", "id": data["id"], "error": error})
            return None
        if data.get("id") != ident:
            return None
        if isinstance(data.get("error"), dict):
            said = clean_text(str(data["error"].get("message", "")))[:300]
            msg = f"the MCP server {self.name} refused: {said}"
            raise McpError(msg)
        result = data.get("result")
        return cast("dict[str, Any]", result) if isinstance(result, dict) else {}
