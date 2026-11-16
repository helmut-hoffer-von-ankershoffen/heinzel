"""Private, ephemeral measurements of one interactive chat turn.

Only counts, tool names and descriptions already offered to the model are sent to the authenticated stream.
Prompts, message contents, arguments, credentials and server configuration are never included.
"""

import asyncio
import contextlib
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic_ai import RunContext
from pydantic_ai._instrumentation import get_instructions
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.capabilities.abstract import WrapModelRequestHandler
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import ModelRequestContext

from heinzel.kernel import Emit, FoundMcpServers, Kernel
from heinzel.modules.heinzel._engine import TurnDeps

if TYPE_CHECKING:
    from heinzel.modules.heinzel._direct_tools import TurnToolbox

_NAME_PARTS = 3
_DESCRIPTION_LIMIT = 256
_DETAIL_LIMIT = 100
_TOTAL_DETAIL_LIMIT = 250


def instruction_length(request: ModelRequestContext) -> int | None:
    """Count request instructions without retaining or emitting their contents."""
    try:
        value = get_instructions(request.messages, request.model_request_parameters)
        return len(value) if value is not None else 0
    except Exception:  # noqa: BLE001 - diagnostics must not change provider behavior
        return None


@dataclass
class TurnDiagnostics(AbstractCapability[TurnDeps]):
    """Time the actual provider requests and report the tools in the latest request, without a second discovery."""

    emit: Emit
    started_at: float = 0
    preparation_ms: float = 0
    discovery_ms: float = 0
    provider_ms: float = 0
    internal_tool_ms: float = 0
    mcp_discovery_ms: float = 0
    mcp_tool_ms: float = 0
    _timing_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    cached_counts: dict[str, int | None] = field(default_factory=dict[str, int | None])
    requests: int = 0
    context: dict[str, Any] = field(default_factory=dict[str, Any])
    servers: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    status: dict[str, tuple[str, bool]] = field(default_factory=dict[str, tuple[str, bool]])
    tool_sources: dict[str, tuple[str, str]] = field(default_factory=dict[str, tuple[str, str]])

    def observe_connections(self, kernel: Kernel | None) -> None:
        """Read passive connection status only; never start a server for the sidebar."""
        try:
            if kernel is None or not kernel.provides(FoundMcpServers):
                return
            service = kernel.resolve(FoundMcpServers)
            rows = service.diagnostics()
            names: dict[str, list[tuple[str, bool, int | None]]] = {}
            for row in rows or []:
                names.setdefault(row.namespace.lower(), []).append((row.scope, row.running, row.known_tool_count))
            unique = {key: values[0] for key, values in names.items() if len(values) == 1}
            self.status = {key: (value[0], value[1]) for key, value in unique.items()}
            self.cached_counts = {key: value[2] for key, value in unique.items()}
            self.servers = [
                {
                    "name": name,
                    "internal": False,
                    "offered": False,
                    "tools": [],
                    "tool_count": None,
                    "scope": scope,
                    "running": running,
                    "cached_tool_count": self.cached_counts.get(name),
                }
                for name, (scope, running) in self.status.items()
            ]
        except Exception:  # noqa: BLE001 - optional passive diagnostics must not affect a turn
            self.status = {}
            self.cached_counts = {}
            self.servers = []

    def observe_tool(self, name: str, elapsed_ms: float) -> None:
        """Measure direct tool wall time without retaining arguments, results or error text; calls may overlap."""
        with self._timing_lock:
            if name in {"mcp__external__servers", "mcp__external__tools", "mcp__external__schema"}:
                self.mcp_discovery_ms += elapsed_ms
            elif name.startswith("mcp__") and not name.startswith("mcp__hz__"):
                self.mcp_tool_ms += elapsed_ms
            else:
                self.internal_tool_ms += elapsed_ms
        self.publish()

    def publish(self) -> None:
        """Send a snapshot; failure to relay diagnostics must never fail a chat turn."""
        with self._timing_lock:
            timings = {
                "internal_tool_ms": self.internal_tool_ms,
                "mcp_discovery_ms": self.mcp_discovery_ms,
                "mcp_tool_ms": self.mcp_tool_ms,
            }
        with contextlib.suppress(Exception):
            self.emit(
                "diagnostics",
                {
                    **timings,
                    "preparation_ms": self.preparation_ms,
                    "discovery_ms": self.discovery_ms,
                    "provider_ms": self.provider_ms,
                    "requests": self.requests,
                    "context": self.context,
                    "servers": self.servers,
                },
            )

    def capture(self, request_context: ModelRequestContext) -> None:
        """Capture counts only; a provider is never asked to do additional work."""
        params = request_context.model_request_parameters
        grouped: dict[tuple[bool, str], list[dict[str, str]]] = {}
        totals: Counter[tuple[bool, str]] = Counter()
        details_left = _TOTAL_DETAIL_LIMIT
        for tool in params.function_tools:
            parts = tool.name.split("__", 2)
            source = self.tool_sources.get(tool.name)
            gateway = source is None and tool.name.startswith("mcp__external__")
            internal = source is None and (len(parts) != _NAME_PARTS or parts[1] == "hz" or gateway)
            server = source[0] if source else "Internal" if internal else parts[1]
            key = (internal, server)
            name = source[1] if source else parts[2] if len(parts) == _NAME_PARTS else tool.name
            if gateway:
                name = f"external.{name}"
            totals[key] += 1
            entries = grouped.setdefault(key, [])
            if len(entries) < _DETAIL_LIMIT and details_left:
                entries.append({"name": name, "description": (tool.description or "")[:_DESCRIPTION_LIMIT]})
                details_left -= 1
        self.servers = [
            {
                "name": name,
                "internal": internal,
                "offered": True,
                "tools": tools,
                "tool_count": totals[(internal, name)],
                "scope": None if internal else self.status.get(name.lower(), (None, None))[0],
                "running": None if internal else self.status.get(name.lower(), (None, None))[1],
                "cached_tool_count": None if internal else self.cached_counts.get(name.lower()),
            }
            for (internal, name), tools in grouped.items()
        ]
        offered = {name.lower() for internal, name in grouped if not internal}
        self.servers.extend(
            {
                "name": name,
                "internal": False,
                "offered": False,
                "tools": [],
                "tool_count": None,
                "scope": scope,
                "running": running,
                "cached_tool_count": self.cached_counts.get(name),
            }
            for name, (scope, running) in self.status.items()
            if name not in offered
        )
        counts = Counter(
            part.part_kind for message in request_context.messages for part in getattr(message, "parts", ())
        )
        self.context = {
            "messages": len(request_context.messages),
            "input_tokens": None,
            "window_tokens": request_context.model.context_window,
            "instruction_chars": instruction_length(request_context),
            "user_parts": counts["user-prompt"],
            "system_parts": counts["system-prompt"],
            "tool_results": counts["tool-return"],
            "responses": counts["text"] + counts["tool-call"],
        }

    async def wrap_model_request(
        self, ctx: RunContext[TurnDeps], *, request_context: ModelRequestContext, handler: WrapModelRequestHandler
    ) -> ModelResponse:
        """Time each model call, including streamed responses, and keep only its latest context counts."""
        del ctx
        with contextlib.suppress(Exception):  # diagnostics are observational, never an admission check
            self.capture(request_context)
        if self.requests == 0 and self.started_at:
            self.preparation_ms = (time.monotonic() - self.started_at) * 1000
        self.publish()
        started = time.monotonic()
        try:
            response = await handler(request_context)
        finally:
            self.provider_ms += (time.monotonic() - started) * 1000
            self.requests += 1
            self.publish()
        with contextlib.suppress(Exception):
            if response.usage.input_tokens > 0:
                self.context["input_tokens"] = response.usage.input_tokens
                self.publish()
        return response


async def prepare_toolbox(  # noqa: PLR0913 - explicit authority, stream and timing inputs
    kernel: Kernel | None, session: str, owner: str, *, attended: bool, emit: Emit | None, started: float
) -> tuple["TurnToolbox | None", TurnDiagnostics | None]:
    """Prepare the existing toolbox and, for a watching person, observe its timing without starting extra servers."""
    from heinzel.modules.heinzel._direct_tools import turn_toolbox  # noqa: PLC0415 - only interactive turns need it

    diagnostic = TurnDiagnostics(emit, started_at=started) if attended and emit is not None else None
    tools_started = time.monotonic()
    toolbox = (
        await asyncio.to_thread(turn_toolbox, kernel, session, owner, attended=attended) if kernel is not None else None
    )
    if diagnostic is not None:
        diagnostic.discovery_ms = (time.monotonic() - tools_started) * 1000
        if toolbox is not None:
            toolbox.measure = diagnostic.observe_tool
            for tool in toolbox.tools:
                found = getattr(tool, "found", None)
                if found is not None:
                    diagnostic.tool_sources[found.title] = (found.namespace, found.name)
            await asyncio.to_thread(diagnostic.observe_connections, kernel)
        diagnostic.preparation_ms = (time.monotonic() - started) * 1000
        diagnostic.publish()
    return toolbox, diagnostic
