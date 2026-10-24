"""The MCP head: every operation becomes a tool for coding agents (Claude Code, Codex, Cursor, …).

The tool's input schema comes from the operation's parameters (`Annotated[T, Field(description=...)]`), its
description from the docstring, its output schema from the return model, and `read_only` from the operation's flag.
"""

import inspect
from collections.abc import Callable
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from heinzel import __version__
from heinzel.heads._exposure import Exposed, exposed
from heinzel.kernel import HeinzelError, Kernel, boot

HEAD = "mcp"
INSTRUCTIONS = (
    "Heinzelmännchen helps product development teams with the busywork of running a team. Tools are named after "
    "their command path: `system_doctor` is `hz system doctor`. Start with `system_info` or `system_doctor`; tools "
    "marked as not read-only change state, so ask the user before calling them unless they asked for exactly that."
)

Transport = Literal["stdio", "streamable-http"]


def build(kernel: Kernel) -> MCPServer:
    """An MCP server with one tool per exposed operation."""
    server = MCPServer(name="heinzel", title="🧝 Heinzelmännchen", version=__version__, instructions=INSTRUCTIONS)
    for item in exposed(kernel, HEAD):
        server.add_tool(
            tool(kernel, item),
            name=item.name,
            description=item.operation.summary,
            annotations=ToolAnnotations(
                read_only_hint=item.operation.read_only, destructive_hint=not item.operation.read_only
            ),
        )
    return server


def tool(kernel: Kernel, item: Exposed) -> Callable[..., Any]:
    """A function with the operation's public signature that runs it through the kernel."""

    def run(**kwargs: Any) -> Any:  # noqa: ANN401 - whatever the operation returns
        try:
            return kernel.invoke(item.operation, **kwargs)
        except HeinzelError as exc:
            raise ToolError(str(exc)) from exc

    run.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        [p.replace(kind=inspect.Parameter.KEYWORD_ONLY) for p in item.parameters], return_annotation=item.returns
    )
    run.__doc__ = inspect.getdoc(item.operation.func)
    run.__name__ = item.name
    return run


def serve(transport: Transport = "stdio", host: str = "127.0.0.1", port: int = 8765) -> None:
    """Boot the kernel and serve its operations over `transport` until interrupted."""
    server = build(boot())
    if transport == "stdio":
        server.run("stdio")
    else:
        server.run("streamable-http", host=host, port=port)
