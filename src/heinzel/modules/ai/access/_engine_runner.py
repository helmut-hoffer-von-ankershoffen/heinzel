"""The engine runner: a coding session on hz's own Pydantic AI engine, with the selected AI access's model (ADR 0159).

An AI access option without an agent CLI of its own (a plugin's `ai-models`, or the built-in Model API option, e.g.
at a company proxy) still serves coding sessions: this `agent-runner` adapter runs the session itself. The model is
the selected option's: the plugin's chosen model with its effort, or the Model API option's configured model. The
session gets what the Claude Code runner allows read-only:

- `Read`, `Grep` and `Glob` as `read`, `grep` and `glob`, confined to the session's working directory (see
  `_file_tools`): no `..`, no symlink, regular files only, size limits;
- the profile's local MCP servers, as `mcp__<server>__<tool>`, hz's chat server above all (see `_mcp_stdio`).

Nothing else: no shell, no writes, no web. The run stops at its timeout and at `MAX_REQUESTS` model requests. The
models report tokens, not dollars, so a dollar budget is not enforced and the result says so. The caller records
the usage, as for every runner.
"""

import asyncio
import concurrent.futures
import tempfile
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from heinzel import AgentResult, Budget, Config, CurrentUser, HeinzelError, Kernel, Progress, TokenUsage, ToolProfile
from heinzel.kernel import Effort, clean_text
from heinzel.kernel._ai_models import build_model, choose, effort_settings, list_offers
from heinzel.modules.ai.access._file_tools import TOOL_NAMES, ConfinedFiles, FileToolError
from heinzel.modules.ai.access._mcp_stdio import McpError, StdioMcp, tool_name
from heinzel.modules.ai.access._models import AccessSettings, Bindings
from heinzel.modules.ai.access._pydantic import NAME as MODEL_API
from heinzel.modules.ai.access._pydantic import PydanticAiAccess

ENGINE = "engine"
MAX_REQUESTS = 50
DETAIL_LIMIT = 300
SILENT = Progress()
USE = "hz ai access use <a plugin's option or pydantic-ai>"


@dataclass(frozen=True, slots=True)
class EngineModel:
    """The model a session runs on, and how to ask it."""

    model: Any
    """A ready `pydantic_ai.models.Model`."""
    label: str
    """Its id, for the result."""
    settings: dict[str, Any] = field(default_factory=dict[str, Any])
    """Model settings for the effort, in the model's own style; empty when none applies."""
    warnings: list[str] = field(default_factory=list[str])
    """What the selected option couldn't honour, e.g. a model override."""


ModelSource = Callable[[str | None, Effort | None], EngineModel]
"""Gives the session's model for an optional model override and effort; tests pass their own."""


def _load() -> Config:
    return Config.load()


class SelectedModels:
    """The model of the selected AI access option, as hz's engine runs it."""

    def __init__(self, kernel: Kernel, current: CurrentUser, load: Callable[[], Config] = _load) -> None:
        """Resolve from `kernel` for the owner `current`, reading the selection afresh through `load`."""
        self._kernel = kernel
        self._current = current
        self._load = load

    def __call__(self, model: str | None, effort: Effort | None) -> EngineModel:
        """The session's model: a plugin's chosen one, or the Model API option's configured one.

        Raises:
            HeinzelError: If the selected option has an agent of its own, or its model can't be built.
        """
        config = self._load()
        access = config.settings(Bindings, "capabilities").ai_access
        settings = config.settings(AccessSettings, "ai.access")
        if access is not None and self._kernel.offers_ai_models(access):
            source = self._kernel.ai_models(access)
            offers, _note = list_offers(source)
            offer = choose(offers, model or settings.plugin_model)
            chosen = effort or settings.plugin_effort
            return EngineModel(build_model(source, offer), offer.id, effort_settings(offer, chosen))
        if access == MODEL_API:
            option = self._kernel.resolve_builtin_service(PydanticAiAccess, "ai.access")
            configured = option.configured_model()
            warnings = []
            if model is not None and model != configured:
                warnings.append(f"the Model API option runs its configured model {configured}, not {model}")
            if effort is not None:
                warnings.append("the Model API option takes no effort, so the run used the model's default")
            return EngineModel(option.turn_model(self._current, configured), configured, warnings=warnings)
        msg = f"The engine runner runs a plugin's models or the Model API option; the selected AI access is {access}."
        raise HeinzelError(msg)


class _Session:
    """One run's tools: the confined file tools and the MCP servers' tools, and what was called."""

    def __init__(self, tools: ToolProfile, cwd: Path) -> None:
        self.calls: Counter[str] = Counter()
        self.files = ConfinedFiles(cwd) if tools.read_files else None
        self.wanted = {TOOL_NAMES[t] for t in (tools.tools or list(TOOL_NAMES))} if tools.read_files else set()
        self.servers = [StdioMcp(name, s.command, s.env, cwd) for name, s in sorted(tools.mcp_servers.items())]

    def _file_tool(self, name: str, run: Callable[..., str]) -> Callable[..., str]:
        def call(**arguments: Any) -> str:  # noqa: ANN401 - the model's arguments, validated by the schema
            self.calls[name] += 1
            try:
                return run(**arguments)
            except FileToolError as exc:
                return f"Refused: {exc}"

        return call

    def file_tools(self) -> list[Any]:
        """The confined file tools the profile allows, as Pydantic AI tools."""
        from pydantic_ai import Tool  # noqa: PLC0415 - only during a run

        if self.files is None:
            return []
        files = self.files
        schemas: dict[str, tuple[Callable[..., str], str, dict[str, Any]]] = {
            "read": (files.read, READ_DOC, READ_SCHEMA),
            "grep": (files.grep, GREP_DOC, GREP_SCHEMA),
            "glob": (files.glob, GLOB_DOC, GLOB_SCHEMA),
        }
        return [
            Tool.from_schema(self._file_tool(name, run), name, doc, schema)
            for name, (run, doc, schema) in schemas.items()
            if name in self.wanted
        ]

    async def mcp_tools(self) -> list[Any]:
        """Start every MCP server and wrap its tools; a server that fails to start fails the run.

        Raises:
            McpError: If a server can't start or list its tools.
        """
        from pydantic_ai import Tool  # noqa: PLC0415 - only during a run

        wrapped: list[Any] = []
        for server in self.servers:
            await server.start()
            for spec in await server.tools():
                name = tool_name(server.name, spec["name"])
                given = spec.get("inputSchema")
                schema: dict[str, Any] = given if isinstance(given, dict) else {"type": "object"}
                wrapped.append(
                    Tool.from_schema(self._mcp_tool(server, spec["name"], name), name, spec.get("description"), schema)
                )
        return wrapped

    def _mcp_tool(self, server: StdioMcp, tool: str, name: str) -> Callable[..., Any]:
        async def call(**arguments: Any) -> str:  # noqa: ANN401 - the model's arguments, validated by the schema
            self.calls[name] += 1
            try:
                return await server.call(tool, arguments)
            except McpError as exc:
                return f"Error: {exc}"

        return call

    async def stop(self) -> None:
        for server in self.servers:
            await server.stop()


READ_DOC = "Read a text file in the working directory: numbered lines from `offset` (1-based), at most `limit` lines."
READ_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "The file, relative to the working directory."},
        "offset": {"type": "integer", "minimum": 1, "description": "The first line to read; 1 by default."},
        "limit": {"type": "integer", "minimum": 1, "description": "How many lines at most; 2000 by default."},
    },
    "required": ["path"],
    "additionalProperties": False,
}
GREP_DOC = "Search the text files under a folder for lines matching a regular expression; `file:line: text` each."
GREP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string", "description": "A Python regular expression."},
        "path": {"type": "string", "description": "A folder or file in the working directory; all of it by default."},
        "glob": {"type": "string", "description": "Only files whose name or path matches, e.g. `*.py`."},
        "ignore_case": {"type": "boolean", "description": "Match regardless of case."},
    },
    "required": ["pattern"],
    "additionalProperties": False,
}
GLOB_DOC = (
    "List the files under a folder whose name (or path, with `/`) matches a pattern, e.g. `*.py` or `src/**/*.ts`."
)
GLOB_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string", "description": "A glob; `*` by default."},
        "path": {"type": "string", "description": "A folder in the working directory; all of it by default."},
    },
    "additionalProperties": False,
}


class EngineRunner:
    """The `engine` agent runner: a read-only session on hz's own engine and the selected option's model."""

    def __init__(
        self, kernel: Kernel, current: CurrentUser, progress: Progress = SILENT, models: ModelSource | None = None
    ) -> None:
        """Create the runner; the kernel and the owner are injected, `models` replaces the model in tests."""
        self._models = models or SelectedModels(kernel, current)
        self._progress = progress

    def run(  # noqa: PLR0913 - a run's whole contract, by keyword
        self,
        prompt: str,
        *,
        contract: str,
        tools: ToolProfile,
        timeout: timedelta,
        budget: Budget,
        model: str | None = None,
        effort: Effort | None = None,
    ) -> AgentResult:
        """Run `prompt` under `contract` with read-only `tools`; stop at `timeout` or `MAX_REQUESTS` requests.

        Raises:
            HeinzelError: If the selected option can't serve a session, a tool isn't read-only, or the working
                directory is missing; a session that fails or times out is a result.
        """
        if unknown := [t for t in tools.tools if t not in TOOL_NAMES]:
            msg = f"The tool profile asks for {', '.join(unknown)}; the engine runner has only {', '.join(TOOL_NAMES)}."
            raise HeinzelError(msg)
        if tools.working_dir is not None and not tools.working_dir.is_dir():
            msg = f"The run's working directory {tools.working_dir} does not exist or is not a directory."
            raise HeinzelError(msg)
        chosen = self._models(model, effort)
        warnings = list(chosen.warnings)
        if budget.max_usd is not None:
            warnings.append(f"the model reports no dollar cost, so the cap of {budget.max_usd:g} USD is not enforced")
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="hz-run-") as directory:
            cwd = tools.working_dir or Path(directory)
            session = _Session(tools, cwd)
            work = self._session(session, chosen, prompt, contract, timeout)
            # Its own thread and event loop: a head may call this from a thread whose loop is already running.
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(asyncio.run, work).result()
        duration = time.monotonic() - started
        result = result.model_copy(
            update={"duration_seconds": duration, "tool_calls": dict(session.calls), "warnings": warnings}
        )
        if result.status == "completed":
            self._progress.emitter()("text", {"text": result.draft})
        return result

    async def _session(
        self, session: _Session, chosen: EngineModel, prompt: str, contract: str, allowed: timedelta
    ) -> AgentResult:
        import pydantic_ai  # noqa: PLC0415 - a heavy import, only during a run
        from pydantic_ai.usage import UsageLimits  # noqa: PLC0415

        pydantic_ai.BANNER_ENABLED = False  # hz owns its output: stdout may be the MCP head's protocol
        failed = AgentResult(runner=ENGINE, model=chosen.label, status="failed", duration_seconds=0, billing="api")
        try:
            tools = session.file_tools() + await asyncio.wait_for(session.mcp_tools(), allowed.total_seconds())
            agent = pydantic_ai.Agent(chosen.model, instructions=contract or None, tools=tools)
            run = agent.run(
                prompt,
                model_settings=chosen.settings or None,  # type: ignore[arg-type]
                usage_limits=UsageLimits(request_limit=MAX_REQUESTS),
            )
            done = await asyncio.wait_for(run, allowed.total_seconds())
        except TimeoutError:
            return failed.model_copy(
                update={"status": "timeout", "detail": f"stopped after {allowed}, the run's timeout"}
            )
        except Exception as exc:  # noqa: BLE001 - the model's, the plugin's or a server's error is a failed run
            return failed.model_copy(update={"detail": clean_text(f"{type(exc).__name__}: {exc}")[:DETAIL_LIMIT]})
        finally:
            await session.stop()
        used = done.usage
        return failed.model_copy(
            update={
                "status": "completed",
                "draft": str(done.output),
                "turns": used.requests,
                "usage": TokenUsage(
                    input_tokens=used.input_tokens,
                    output_tokens=used.output_tokens,
                    cache_read_tokens=used.cache_read_tokens,
                    cache_write_tokens=used.cache_write_tokens,
                ),
            }
        )
