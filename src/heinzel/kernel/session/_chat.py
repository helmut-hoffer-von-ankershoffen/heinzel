"""The seam between an agent's tool calls in a chat and the person who confirms them (ADR 0045).

A chat runs the configured agent runner with Heinzelmännchen's operations as tools, served by the MCP head for that
one chat session (`hz system serve mcp --chat <session>`). Read operations run when the agent calls them. Every
other operation (it writes, publishes or spends) is not run: the head hands it to the `ActionGate` as a proposal, and
only the person's confirmation in the chat runs it, with exactly the arguments shown. The rule lives in code, in the
head and in the module that confirms, never in the agent's prompt.

The kernel knows neither chats nor MCP: one module provides the gate (`system.runner.chat` in the core), and the MCP
head asks for it by type.
"""

from dataclasses import dataclass, field

from pydantic import JsonValue


@dataclass(frozen=True, kw_only=True, slots=True)
class ProposedCall:
    """An operation an agent asked to run in a chat session, held until the person confirms it."""

    session: str
    """The chat session the agent runs in."""
    module: str
    """The qualified name of the operation's module, e.g. `system.config`."""
    operation: str
    """The operation's name in its module, e.g. `set`."""
    tool: str
    """The tool name the agent called, e.g. `system_config_set`."""
    arguments: dict[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    """The arguments the agent passed, as JSON; confirming runs exactly these."""
    command: str
    """The call as a person reads it: `hz system config set --key ... --value ...`."""


class ActionGate:
    """Holds what an agent in a chat asked to change until the person decides; a module provides a subclass.

    A base class rather than a protocol, so the kernel injects it by type like any other service.
    """

    def propose(self, call: ProposedCall) -> str:
        """Record `call` as waiting for the person's confirmation and return its id; nothing runs.

        Raises:
            NotImplementedError: Always; the providing module stores proposals with its sessions.
        """
        raise NotImplementedError
