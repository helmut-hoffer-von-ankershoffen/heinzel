"""Public service contracts for heads that bridge runner chats to other Heinzel modules."""

from dataclasses import dataclass


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
