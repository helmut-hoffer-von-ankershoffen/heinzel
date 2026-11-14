"""Connector adapters on top of an MCP server: `McpConnector` (ADR 0159).

A company often reaches its mail, calendar, tracker or documents through an MCP server, for example a company MCP
gateway with one local launcher per connector. The launcher signs in on its own, so the plugin holds no secret: the
adapter starts the launcher as a subprocess, speaks MCP over its stdin and stdout, and maps tool results onto the
capability's models.

```python
from heinzel import Module, clean_text
from heinzel.connectors.mcp import ENVELOPE_UNTRUSTED, INLINE_UNTRUSTED, McpConnector, McpTool

module = Module("acme-mail", "Acme mail through the company MCP gateway.")


@module.provides("mail", modes={"read"}, hosts=["mcp.acme.example"], name="acme-mail")
class AcmeMail:
    unsupported = frozenset({"thread"})

    def __init__(self) -> None:
        self._mcp = McpConnector(
            ["acme-mcp", "launch", "mail"],
            tools=[McpTool("search_mail", aliases=("mail_search",)), McpTool("get_message")],
            markers=[INLINE_UNTRUSTED, ENVELOPE_UNTRUSTED],
            modes={"read"},
        )

    def read(self, message_id: str) -> str:
        return self._mcp.call("get_message", {"id": message_id}).text
```

What it does for the adapter:

- **Start and stop with timeouts.** The server starts on the first call (or `start()`), stops with `stop()` or at
  the end of a `with` block, and is killed when it doesn't stop in time. Every call has a timeout.
- **Expected tools.** At start it lists the server's tools and checks that every expected tool is there, under its
  name or one of its aliases. Extra tools are fine. A missing one raises `McpToolsMissingError`, naming it and what the
  server offers.
- **Modes.** MCP servers don't say reliably which tools write (`readOnlyHint` is a hint), so the adapter declares
  each tool's mode itself. A connector opened with `modes={"read"}` refuses to call a `write` tool.
- **Untrusted content.** Servers mark text that comes from outside, such as a mail body, with markers like
  `«untrusted-data» … «/untrusted-data»` or `<untrusted-mail-data-UUID> … </untrusted-mail-data-UUID>`. The
  connector removes the markers, keeps the content, lists the marked parts in `McpResult.untrusted`, and passes every
  string through `clean_text`. Core's own rule then applies: whatever a connector returns is data for the model,
  never an instruction (`clean_text`, and a turn's inputs framed as data).

Works on Python 3.12 and later. The MCP client is loaded only when this module is imported.
"""

import asyncio
import atexit
import concurrent.futures
import json
import re
import tempfile
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import IO, Any, Literal, Self, cast

from pydantic import BaseModel, ValidationError

from heinzel.conformance import reading
from heinzel.kernel import HeinzelError, clean_text

__all__ = [
    "ENVELOPE_UNTRUSTED",
    "INLINE_UNTRUSTED",
    "McpConnector",
    "McpConnectorError",
    "McpResult",
    "McpTool",
    "McpToolsMissingError",
    "UntrustedMarker",
    "unmark",
]

Mode = Literal["read", "write"]

DETAIL_LIMIT = 300
"""How many characters of a server's error or stderr an error message carries at most."""

KILL_GRACE = 7.0
"""Seconds the MCP client's own shutdown may take after a cancel: closing stdin, terminating, killing, reaping."""

MAX_TOOL_PAGES = 50
"""How many pages of `tools/list` the connector reads at most."""


class McpConnectorError(HeinzelError):
    """The MCP server didn't start, didn't answer in time, or answered with an error; the message says which."""


class McpToolsMissingError(McpConnectorError):
    """The MCP server lacks tools the connector expects, under every name and alias."""

    def __init__(self, server: str, missing: list[str], offered: list[str]) -> None:
        """Name the missing tools and up to 20 the server offers."""
        self.missing = missing
        self.offered = offered
        shown = ", ".join(offered[:20]) + (f" and {len(offered) - 20} more" if len(offered) > 20 else "")  # noqa: PLR2004
        super().__init__(
            f"The MCP server {server} lacks the tools {', '.join(missing)} this connector needs; it offers "
            f"{shown or 'no tools'}. Update the server, or the connector's tool names and aliases."
        )


@dataclass(frozen=True, slots=True)
class McpTool:
    """A tool the connector expects: its name, the mode it acts in, and other names the server may give it."""

    name: str
    mode: Mode = "read"
    """`read` for a tool that only reads, `write` for one that changes anything; the adapter decides, not the server."""
    aliases: tuple[str, ...] = ()
    """Other names the same tool goes by on some servers or versions; the first name found is used."""

    def __post_init__(self) -> None:
        """Refuse a mode other than read or write.

        Raises:
            ValueError: If `mode` is neither.
        """
        if self.mode not in {"read", "write"}:
            msg = f"McpTool {self.name!r}: mode must be 'read' or 'write', not {self.mode!r}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class UntrustedMarker:
    """How a server marks untrusted content: a start and an end, both regular expressions.

    `end` may refer to groups of `start`, so an envelope's closing tag must carry the same id as its opening tag, and
    content can't close it early. `stray` matches a leftover start or end that didn't pair, which is removed.
    """

    start: str
    end: str
    stray: str = ""

    @classmethod
    def literal(cls, start: str, end: str) -> "UntrustedMarker":
        """A marker of two fixed strings, such as `«untrusted-data»` and `«/untrusted-data»`."""
        return cls(re.escape(start), re.escape(end), f"{re.escape(start)}|{re.escape(end)}")


INLINE_UNTRUSTED = UntrustedMarker.literal("«untrusted-data»", "«/untrusted-data»")
"""Inline markers: `«untrusted-data» … «/untrusted-data»`."""

ENVELOPE_UNTRUSTED = UntrustedMarker(
    r"<untrusted-(?P<kind>[a-z][a-z0-9-]{0,40})-data-(?P<id>[0-9A-Fa-f-]{8,64})>",
    r"</untrusted-(?P=kind)-data-(?P=id)>",
    r"</?untrusted-[a-z][a-z0-9-]{0,40}-data-[0-9A-Fa-f-]{8,64}>",
)
"""An envelope with a random id: `<untrusted-mail-data-UUID> … </untrusted-mail-data-UUID>`, any kind of data."""


@dataclass(frozen=True, slots=True)
class McpResult:
    """A tool's answer: its text and data with markers removed and every string cleaned."""

    text: str
    """All text blocks joined by newlines, markers removed, through `clean_text`."""
    data: Any = None
    """The structured content, or the text parsed as JSON when it is JSON; None when neither."""
    untrusted: tuple[str, ...] = ()
    """The parts the server marked as untrusted, cleaned, in order."""

    def parse[M: BaseModel](self, model: type[M]) -> M:
        """`data` validated as `model`.

        Raises:
            McpConnectorError: If there is no data, or it doesn't match `model`.
        """
        if self.data is None:
            msg = f"The MCP tool answered no JSON data to read as {model.__name__}."
            raise McpConnectorError(msg)
        try:
            return model.model_validate(self.data)
        except ValidationError as exc:
            msg = f"The MCP tool's answer doesn't match {model.__name__}: {exc.error_count()} problems, first: "
            msg += clean_text(str(exc.errors()[0].get("msg", "")))[:DETAIL_LIMIT]
            raise McpConnectorError(msg) from exc


@dataclass
class _Compiled:
    start: re.Pattern[str]
    pair: re.Pattern[str]
    stray: re.Pattern[str] | None


@dataclass
class _Unmarker:
    """Removes markers from text and collects what they marked."""

    markers: Sequence[UntrustedMarker]
    found: list[str] = field(default_factory=list[str])
    _compiled: list[_Compiled] = field(default_factory=list[_Compiled])

    def __post_init__(self) -> None:
        self._compiled = [
            _Compiled(
                re.compile(m.start),
                re.compile(f"(?:{m.start})(?P<hz_body>.*?)(?:{m.end})", re.DOTALL),
                re.compile(m.stray) if m.stray else None,
            )
            for m in self.markers
        ]

    def text(self, text: str) -> str:
        """`text` with paired markers removed, an unpaired start marking the rest, stray markers dropped, cleaned."""
        out: list[str] = []
        position = 0
        while self._compiled:
            starts = [(m.start.search(text, position), m) for m in self._compiled]
            hits = [(hit, m) for hit, m in starts if hit is not None]
            if not hits:
                break
            hit, marker = min(hits, key=lambda pair: pair[0].start())
            out.append(text[position : hit.start()])
            paired = marker.pair.match(text, hit.start())
            body = paired.group("hz_body") if paired else text[hit.end() :]
            body = self._strip(body)
            self.found.append(clean_text(body))
            out.append(body)
            if not paired:
                position = len(text)
                break
            position = paired.end()
        out.append(text[position:])
        return clean_text(self._strip("".join(out)))

    def _strip(self, text: str) -> str:
        for marker in self._compiled:
            if marker.stray is not None:
                text = marker.stray.sub("", text)
        return text

    def value(self, value: object) -> object:
        """`value` with every string in it unmarked and cleaned, keys too."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.value(item) for item in cast("list[object]", value)]
        if isinstance(value, dict):
            return {self.text(str(k)): self.value(v) for k, v in cast("dict[object, object]", value).items()}
        return value


def unmark(text: str, markers: Sequence[UntrustedMarker]) -> tuple[str, tuple[str, ...]]:
    """`text` without `markers`, cleaned, and the parts they marked: what `McpConnector` does to every answer."""
    unmarker = _Unmarker(markers)
    return unmarker.text(text), tuple(unmarker.found)


class McpConnector:
    """One MCP server over stdio for one connector adapter: started once, called by tool name, stopped at the end.

    Thread-safe: the server runs on an event loop of its own thread, so a synchronous adapter method just calls
    `call`. Construct it in the adapter's `__init__`; nothing starts until the first call or `start()`.
    """

    def __init__(  # noqa: PLR0913 - every knob a connector needs, all keyword-only
        self,
        command: Sequence[str],
        *,
        tools: Iterable[McpTool | str],
        markers: Sequence[UntrustedMarker] = (),
        modes: Iterable[Mode] = ("read", "write"),
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
        timeout: float = 30.0,
        start_timeout: float = 30.0,
        stop_timeout: float = 5.0,
    ) -> None:
        """Describe the server; nothing starts yet.

        Args:
            command: The launcher and its arguments, an argument list run without a shell.
            tools: The tools the connector calls: `McpTool`s, or names of read tools.
            markers: How the server marks untrusted content.
            modes: The modes the adapter serves; a `write` tool is refused unless `write` is among them.
            env: Extra environment variables for the launcher, on top of a minimal environment (`HOME`, `PATH` and
                the like). No secrets: the launcher signs in on its own.
            cwd: The launcher's working directory.
            timeout: Seconds one tool call may take.
            start_timeout: Seconds starting the server and listing its tools may take.
            stop_timeout: Seconds the server gets to stop before it is killed.

        Raises:
            ValueError: If `command` is a string or empty, a tool is declared twice, or a mode is unknown.
        """
        if isinstance(command, str) or not command:
            msg = "McpConnector: command must be a non-empty argument list, such as ['acme-mcp', 'launch', 'mail']"
            raise ValueError(msg)
        self._command = [str(part) for part in command]
        self._label = self._command[0].rsplit("/", 1)[-1]
        self._transport: Any = None
        self._tools = _tools(tools)
        self._markers = tuple(markers)
        self._modes = frozenset(modes)
        if not self._modes <= {"read", "write"}:
            msg = f"McpConnector: modes must be read and/or write, not {sorted(self._modes)}"
            raise ValueError(msg)
        self._env = dict(env or {})
        self._cwd = cwd
        self.timeout = timeout
        self.start_timeout = start_timeout
        self.stop_timeout = stop_timeout
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._runner: concurrent.futures.Future[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._client: Any = None
        self._resolved: dict[str, str] = {}
        self._stderr: IO[str] | None = None
        self._finished = threading.Event()

    @classmethod
    def in_process(
        cls,
        server: object,
        *,
        tools: Iterable[McpTool | str],
        markers: Sequence[UntrustedMarker] = (),
        **kwargs: Any,  # noqa: ANN401 - the constructor's keyword arguments
    ) -> Self:
        """A connector to an MCP server object in this process (`mcp.server.MCPServer`), for an adapter's tests."""
        connector = cls(["in-process"], tools=tools, markers=markers, **kwargs)
        connector._transport = server
        connector._label = "in-process"
        return connector

    @property
    def tools(self) -> dict[str, str]:
        """Each expected tool's name and the name the server gave it; empty until started."""
        return dict(self._resolved)

    @property
    def started(self) -> bool:
        """Whether the server runs and its tools were checked."""
        return self._client is not None

    def __enter__(self) -> Self:
        """Start the server."""
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        """Stop the server."""
        self.stop()

    def start(self) -> None:
        """Start the server and check its tools; nothing happens when it runs already.

        Raises:
            McpConnectorError: If it doesn't start within `start_timeout`, or fails to.
            McpToolsMissingError: If an expected tool is missing under every name.
        """
        with self._lock:
            if self._client is not None:
                return
            loop = asyncio.new_event_loop()
            thread = threading.Thread(target=loop.run_forever, name=f"mcp-{self._label}", daemon=True)
            thread.start()
            self._loop, self._thread = loop, thread
            self._finished.clear()
            ready: concurrent.futures.Future[list[str]] = concurrent.futures.Future()
            self._runner = asyncio.run_coroutine_threadsafe(self._serve(ready), loop)
            try:
                offered = ready.result(timeout=self.start_timeout)
            except concurrent.futures.TimeoutError:
                hint = self._hint()
                self._shutdown(graceful=False)
                msg = f"The MCP server {self._label} didn't start within {self.start_timeout:g}s.{hint}"
                raise McpConnectorError(msg) from None
            except Exception as exc:
                hint = self._hint()
                self._shutdown(graceful=False)
                detail = clean_text(f"{type(exc).__name__}: {exc}")[:DETAIL_LIMIT]
                msg = f"The MCP server {self._label} didn't start ({detail}).{hint}"
                raise McpConnectorError(msg) from exc
            resolved: dict[str, str] = {}
            missing: list[str] = []
            for tool in self._tools.values():
                found = next((name for name in (tool.name, *tool.aliases) if name in offered), None)
                if found is None:
                    missing.append(tool.name)
                else:
                    resolved[tool.name] = found
            if missing:
                self._shutdown()
                raise McpToolsMissingError(self._label, missing, sorted(offered))
            self._resolved = resolved
            atexit.register(self.stop)

    def stop(self) -> None:
        """Stop the server; it is killed when it doesn't stop within `stop_timeout`. Safe to call twice."""
        with self._lock:
            atexit.unregister(self.stop)
            self._shutdown()

    def call(self, tool: str, arguments: Mapping[str, Any] | None = None) -> McpResult:
        """Call the expected tool `tool` (its own name, not an alias) and return its cleaned answer.

        Starts the server on the first call.

        Raises:
            McpConnectorError: If `tool` isn't an expected tool, is a write tool on a read-only connector or during a
                read method, takes longer than `timeout`, or the server answers with an error.
        """
        spec = self._tools.get(tool)
        if spec is None:
            msg = f"The tool {tool!r} isn't one this connector declared; declare it in `tools`."
            raise McpConnectorError(msg)
        if spec.mode == "write" and "write" not in self._modes:
            msg = f"The tool {tool!r} writes, and this connector serves only {', '.join(sorted(self._modes))}."
            raise McpConnectorError(msg)
        if spec.mode == "write" and (method := reading.get()) is not None:
            msg = f"The read method {method} called the write tool {tool!r}: a read method must not write."
            raise McpConnectorError(msg)
        self.start()
        loop, client = self._loop, self._client
        if loop is None or client is None:  # stopped by another thread in between
            msg = f"The MCP server {self._label} was stopped."
            raise McpConnectorError(msg)
        future = asyncio.run_coroutine_threadsafe(
            client.call_tool(self._resolved[tool], dict(arguments or {}), read_timeout_seconds=self.timeout), loop
        )
        try:
            result = future.result(timeout=self.timeout + 1)
        except concurrent.futures.TimeoutError:
            future.cancel()
            msg = f"The MCP tool {tool} didn't answer within {self.timeout:g}s."
            raise McpConnectorError(msg) from None
        except Exception as exc:
            detail = clean_text(f"{type(exc).__name__}: {exc}")[:DETAIL_LIMIT]
            msg = f"The MCP tool {tool} failed: {detail}"
            raise McpConnectorError(msg) from exc
        return self._result(tool, result)

    def _result(self, tool: str, result: Any) -> McpResult:  # noqa: ANN401 - mcp.types.CallToolResult
        unmarker = _Unmarker(self._markers)
        raw = "\n".join(getattr(block, "text", "") for block in result.content if getattr(block, "type", "") == "text")
        if result.is_error:
            msg = f"The MCP tool {tool} answered an error: {unmarker.text(raw)[:DETAIL_LIMIT] or 'no details'}"
            raise McpConnectorError(msg)
        text = unmarker.text(raw)
        data: object = None
        if result.structured_content is not None:
            data = unmarker.value(result.structured_content)
        else:
            try:
                data = unmarker.value(json.loads(raw))
            except ValueError:
                data = None
        # Every string was unmarked once for `text` and again inside `data`: keep each marked part once, in order.
        return McpResult(text=text, data=data, untrusted=tuple(dict.fromkeys(unmarker.found)))

    async def _serve(self, ready: "concurrent.futures.Future[list[str]]") -> None:
        from mcp import Client  # noqa: PLC0415 - loaded only when a connector starts
        from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: PLC0415

        self._stop_event = asyncio.Event()
        server: Any = self._transport
        if server is None:
            self._stderr = tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace")  # noqa: SIM115 - closed in _shutdown
            parameters = StdioServerParameters(
                command=self._command[0], args=self._command[1:], env=self._env, cwd=self._cwd
            )
            server = stdio_client(parameters, errlog=self._stderr)
        try:
            async with Client(server, read_timeout_seconds=self.timeout) as client:
                offered = await _tool_names(client)
                self._client = client
                ready.set_result(offered)
                await self._stop_event.wait()
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc if isinstance(exc, Exception) else RuntimeError(str(exc)))
            raise
        finally:
            self._client = None
            self._finished.set()

    def _hint(self) -> str:
        """The last line the launcher wrote to stderr, cleaned and cut, as a sentence; empty when none."""
        if self._stderr is None:
            return ""
        try:
            self._stderr.flush()
            self._stderr.seek(0)
            lines = [line.strip() for line in self._stderr.read()[-4096:].splitlines() if line.strip()]
        except (OSError, ValueError):
            return ""
        return f" It said: {clean_text(lines[-1])[:DETAIL_LIMIT]}" if lines else ""

    def _shutdown(self, *, graceful: bool = True) -> None:
        """Stop the server: ask it to end, or right away cancel it, whose cleanup kills the launcher's process tree."""
        loop, thread, runner, event = self._loop, self._thread, self._runner, self._stop_event
        self._client = None
        self._resolved = {}
        if loop is not None and runner is not None:
            if graceful and event is not None:
                loop.call_soon_threadsafe(event.set)
            if not (graceful and self._finished.wait(self.stop_timeout)):
                # Cancelling runs the MCP client's own shutdown: close stdin, then terminate and kill the process tree.
                runner.cancel()
                self._finished.wait(self.stop_timeout + KILL_GRACE)
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)
            if thread is not None:
                thread.join(timeout=self.stop_timeout)
            if thread is None or not thread.is_alive():
                loop.close()
        if self._stderr is not None:
            self._stderr.close()
        self._loop = self._thread = self._runner = self._stop_event = self._stderr = None


def _tools(tools: Iterable[McpTool | str]) -> dict[str, McpTool]:
    declared: dict[str, McpTool] = {}
    for item in tools:
        tool = McpTool(item) if isinstance(item, str) else item
        if tool.name in declared:
            msg = f"McpConnector: the tool {tool.name!r} is declared twice"
            raise ValueError(msg)
        declared[tool.name] = tool
    return declared


async def _tool_names(client: Any) -> list[str]:  # noqa: ANN401 - mcp.Client
    names: list[str] = []
    cursor: str | None = None
    for _ in range(MAX_TOOL_PAGES):
        # Serial on purpose: each page is asked for with the cursor the previous page returned.
        page = await client.list_tools(cursor=cursor)
        names.extend(tool.name for tool in page.tools)
        cursor = page.next_cursor
        if not cursor:
            break
    return names
