"""Public service contracts for heads that bridge runner chats to other Heinzel modules."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CheckInHistoryMessage:
    """One bounded, already-authorized message from a durable personal conversation."""

    role: str
    text: str


@dataclass(frozen=True)
class CheckInChatContext:
    """The durable-session projection a runner chat may use to describe its check-in context."""

    name: str
    state: str
    schedule_status: str | None
    cron: str | None
    timezone: str | None
    history: tuple[CheckInHistoryMessage, ...]


class CheckInChatContextSource:
    """Provides only the owning user's current personal-session context and bounded conversation history."""

    def get(self, session_id: str) -> CheckInChatContext:
        """Read one durable session using the current authenticated owner."""
        raise NotImplementedError


class McpChatSessions:
    """The narrow chat-session checks needed by the MCP head; no conversation contents are exposed."""

    def require(self, session_id: str) -> None:
        """Raise when the local chat session is missing or invalid."""
        raise NotImplementedError

    def is_check_in(self, session_id: str) -> bool:
        """Whether the existing session is scoped to a personal check-in."""
        raise NotImplementedError

    def turn_running(self, session_id: str) -> bool:
        """Whether some process holds the existing session right now: a turn is being answered, or an action run.

        The process with the desktop's browser asks this before it runs another process's call as that session
        (ADR 0162): a session nobody is working in has no agent that could be calling.
        """
        raise NotImplementedError


FOUND_MODULE = "mcp:"
"""How a proposed call of a found MCP server's tool names its source in `ProposedCall.module`: `mcp:<server id>`.

No module's qualified name holds a colon, so a confirmation can never mistake it for one of hz's operations.
"""


@dataclass(frozen=True)
class FoundMcpTool:
    """One tool of a found MCP server that is switched on (ADR 0146, 0162), as its server lists it.

    `description` and `schema` are the server's own words: untrusted, like a web page.
    """

    server: str
    """The server's id, as `hz system claude list --json` shows it; what a confirmation calls it by."""
    namespace: str
    """The server's name in tool names, `mcp__<namespace>__<tool>`; unique among the turn's sources."""
    name: str
    """The tool's name on its server."""
    title: str
    """The tool's name as the agent calls it: `mcp__<namespace>__<tool>`."""
    description: str
    schema: dict[str, Any]
    read_only: bool
    """Whether the server marks the tool read-only (`readOnlyHint`); any other or unknown tool is proposed."""


class FoundMcpServers:
    """hz as the MCP client of every found MCP server that is switched on; a module provides it (ADR 0162).

    A base class rather than a protocol, so the kernel injects it by type like any other service.
    """

    def tools(self) -> list[FoundMcpTool]:
        """The tools of every usable switched-on server; a server is started the first time its tools are needed."""
        raise NotImplementedError

    def call(self, server: str, tool: str, arguments: dict[str, Any]) -> str:
        """Call `tool` of the server `server` and return its text, which is untrusted data.

        Raises:
            HeinzelError: If the server is off, not usable, fails, times out or the tool reports an error.
        """
        raise NotImplementedError
