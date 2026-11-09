"""MCP protocol handlers for an already admitted private connection."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from mcp import types
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server
from pydantic import ValidationError

from heinzel import __version__
from heinzel.heads._heinzel_tools import HeinzelTools
from heinzel.kernel import HeinzelError
from heinzel.kernel._heinzel_invocation import HeinzelInput, HeinzelTarget

_REFUSED = "The reviewed Heinzel request is invalid or unavailable"


def _schema(target: HeinzelTarget) -> dict[str, object]:
    identity: dict[str, object] = {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"}
    properties = {"request_id": identity}
    required = ["request_id"]
    if target.kind == "personal":
        properties["message"] = {"type": "string", "minLength": 1, "maxLength": 32000}
        required.append("message")
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def build_admitted_heinzel_server(tools: HeinzelTools) -> Server[None]:
    """Build protocol handlers only; the private transport owner must supply authenticated streams."""

    @asynccontextmanager
    async def lifespan(_server: Server[None]) -> AsyncGenerator[None]:
        yield

    async def listing(
        _context: ServerRequestContext[None], _params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        try:
            targets = await asyncio.to_thread(tools.targets)
        except (HeinzelError, OSError):
            targets = ()
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=target.tool_name,
                    title=f"{'Message' if target.kind == 'personal' else 'Draft with'} {target.identity}",
                    description=(
                        "Queue one message for this Heinzel; acceptance is not a model answer."
                        if target.kind == "personal"
                        else "Run this reviewed Heinzel and save its draft. It does not publish."
                    ),
                    input_schema=_schema(target),
                    annotations=types.ToolAnnotations(read_only_hint=False, destructive_hint=False),
                )
                for target in targets
            ]
        )

    async def call(_context: ServerRequestContext[None], params: types.CallToolRequestParams) -> types.CallToolResult:
        try:
            request = HeinzelInput.model_validate(params.arguments or {})
            value = await asyncio.to_thread(tools.call, params.name, request)
        except (HeinzelError, OSError, ValidationError):
            return types.CallToolResult(is_error=True, content=[types.TextContent(type="text", text=_REFUSED)])
        return types.CallToolResult(content=[types.TextContent(type="text", text=value.decode())])

    return Server[None](
        "heinzel-private", version=__version__, lifespan=lifespan, on_list_tools=listing, on_call_tool=call
    )
