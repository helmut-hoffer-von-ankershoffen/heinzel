"""The tools the stdio MCP server lists, for modules that document them without importing this head."""

import asyncio

from heinzel.kernel import Kernel, ServedTool, ServedTools


class McpServedTools(ServedTools):
    """What `build(kernel, "stdio")` registers, read once and kept."""

    def __init__(self, kernel: Kernel) -> None:
        self._kernel = kernel
        self._tools: dict[str, ServedTool] | None = None

    def tools(self) -> dict[str, ServedTool]:
        """The served tools by name, in the server's order."""
        if self._tools is None:
            from heinzel.heads.mcp import build  # noqa: PLC0415 - the server is built only when a reference asks

            listed = asyncio.run(build(self._kernel, "stdio").list_tools())
            self._tools = {
                tool.name: ServedTool(
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=dict(tool.input_schema),
                    read_only=bool(tool.annotations and tool.annotations.read_only_hint),
                )
                for tool in listed
            }
        return self._tools


def bind_served_tools(kernel: Kernel) -> None:
    """Let modules read the served tools through the kernel; every head that builds an app calls it."""
    if not kernel.provides(ServedTools):
        kernel.bind(ServedTools, McpServedTools(kernel))
