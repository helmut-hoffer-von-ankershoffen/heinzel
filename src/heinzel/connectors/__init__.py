"""Public helpers for connector adapters (ADR 0159).

- `claude_mcp_servers()`: the MCP servers Claude Code and Claude Desktop have configured on this computer, by name and
  shape, never a secret (ADR 0062), and whether the person left them on for Heinzels (ADR 0146).
- `heinzel.connectors.mcp`: `McpConnector`, an adapter's client for one MCP server over stdio. Imported on its own,
  so `import heinzel.connectors` doesn't load the MCP client.

Works on Python 3.15 and later.
"""

from collections.abc import Iterable
from pathlib import Path

from heinzel.modules.system.claude._models import FoundServer, ServerSource
from heinzel.modules.system.claude._service import ClaudeService
from heinzel.modules.system.claude._switch import SwitchStore, switch_file

ClaudeMcpServer = FoundServer
"""An MCP server found in Claude's configuration: id, name, source, scope, transport, program name, host, the names
of its environment variables and headers, and whether it is on. No field holds a secret, an argument or a URL path."""

__all__ = ["ClaudeMcpServer", "ServerSource", "claude_mcp_servers"]


def claude_mcp_servers(
    home: Path | None = None,
    *,
    sources: Iterable[ServerSource] | None = None,
    include_disabled: bool = False,
) -> list[ClaudeMcpServer]:
    """The MCP servers Claude has configured, as `hz system claude list` shows them; reads files only.

    Nothing is started, connected to or imported, and no secret value is read: environment variables and headers come
    by name. hz's own server is never listed. A plugin uses it to see whether a server it builds on, such as a company
    MCP gateway's launcher, is set up, before it tells the person how to set it up; it starts its own launcher.

    Which servers are off comes from hz's own switch file in your state folder, whatever `home` is;
    `HZ_CLAUDE_SWITCH_DIR` moves it, as the `heinzel.testing` fixtures do for every test.

    Args:
        home: The home directory to read; yours when None. Tests pass a fake one.
        sources: Only servers from these sources, such as `code-user` and `code-project` for Claude Code's own; all
            when None.
        include_disabled: Also the servers the person switched off for Heinzels with `hz system claude disable`.

    Returns:
        The servers, sorted by source, origin and name.
    """
    service = ClaudeService(Path.home() if home is None else home, SwitchStore(switch_file()))
    wanted = None if sources is None else frozenset(sources)
    return [
        server
        for server in service.scan().servers
        if server.status != "self"
        and (include_disabled or server.enabled)
        and (wanted is None or server.source in wanted)
    ]
