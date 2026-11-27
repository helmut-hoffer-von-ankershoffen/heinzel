"""Build the MCP application and mount its declared workspace and chat tools."""

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from heinzel import __version__
from heinzel.heads._exposure import Exposed, exposed
from heinzel.kernel.session._chat_tools import ChatTool, agent_item

if TYPE_CHECKING:
    from mcp.server.auth.provider import TokenVerifier
    from mcp.server.auth.settings import AuthSettings

    from heinzel.kernel import Kernel

type WorkspaceHandler = Callable[[Exposed, tuple[str, object] | None], Callable[..., object]]


def make_server(instructions: str, verifier: "TokenVerifier | None", *, auth: "AuthSettings | None") -> MCPServer:
    """Create the SDK server with the authentication policy selected by the transport."""
    return MCPServer(
        name="heinzel",
        title="Heinzelmännchen",
        version=__version__,
        instructions=instructions,
        token_verifier=verifier,
        auth=auth,
    )


def mount_workspace_tools(server: MCPServer, kernel: "Kernel", head: str, handler: WorkspaceHandler) -> None:
    """Register each exposed workspace operation and its non-conflicting legacy tool names."""
    items = exposed(kernel, head)
    names = {item.name for item in items}
    for item in items:
        if item.record.qualname == "system.runner.chat" and item.operation.name == "confirm-authorized-mcp":
            continue  # only a chat's own pipe may consume a person's authorization, for that chat
        annotations = ToolAnnotations(
            read_only_hint=item.operation.read_only, destructive_hint=not item.operation.read_only
        )
        tool_item = agent_item(item)
        server.add_tool(
            handler(tool_item, None),
            name=tool_item.name,
            description=item.operation.summary,
            annotations=annotations,
        )
        # A deprecated tool name must leave all current names and previously mounted aliases intact (ADR 0051).
        for old in item.operation.aliases:
            if (alias := old.replace(" ", "_").replace("-", "_")) in names:
                continue
            names.add(alias)
            server.add_tool(
                handler(tool_item, (alias, item.returns)),
                name=alias,
                description=f"Deprecated: use `{item.name}`, which this runs; goes in the next minor release. "
                f"{item.operation.summary}",
                annotations=annotations,
            )


def mount_chat_tools(
    server: MCPServer, tools: Sequence[ChatTool], handler: Callable[[ChatTool], Callable[..., object]]
) -> None:
    """Register the already selected tools of one chat with their public descriptions and annotations."""
    for chat_item in tools:
        server.add_tool(
            handler(chat_item),
            name=chat_item.name,
            description=chat_item.description,
            annotations=ToolAnnotations(read_only_hint=chat_item.read_only, destructive_hint=not chat_item.read_only),
        )
