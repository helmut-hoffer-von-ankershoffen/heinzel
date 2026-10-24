"""The chat: sessions with the agent runner that know where they were opened and act through hz (ADR 0045).

A session is opened with a context (a screen, a contract, a draft, a notification event) and kept locally. Each
message runs the bound agent runner with hz's operations as tools: reads run, everything else waits for the
person's `confirm`. `hz chat` in a terminal and the web app's right sidebar are views on these operations.

Every operation is on the CLI and the web head only: a session holds what the person wrote, `send` spends money,
and `confirm` must never be reachable by an agent, so neither the MCP head (the agent's own tools) nor the
unauthenticated REST head exposes them.
"""

from typing import Annotated

from pydantic import Field

from heinzel import ActionGate, AgentRunner, Module
from heinzel.modules.system.runner.chat._models import (
    ChatAction,
    ChatContext,
    ChatCosts,
    ChatMessage,
    ChatSession,
    ChatSettings,
    ChatSummary,
)
from heinzel.modules.system.runner.chat._service import ChatService, parse_context
from heinzel.modules.system.runner.chat._store import ChatStore, StoreGate

module = Module("chat", "Chat with the agent runner about where you are; it acts through hz once you confirm.")
module.settings(ChatSettings)
module.service(ChatStore)
module.service(ChatService)
module.service(ActionGate, StoreGate)

HEADS = frozenset({"cli", "web"})
Session = Annotated[str, Field(pattern=r"^[a-f0-9]{16}$", description="The chat session's id, from `hz chat list`.")]
Action = Annotated[str, Field(pattern=r"^a[0-9]{1,4}$", description="The proposed action's id, e.g. `a1`.")]


@module.operation(read_only=False, heads=HEADS, audience="everyday")
def open(  # noqa: A001 - the command is `hz chat open`
    service: ChatService,
    context: Annotated[
        str | None,
        Field(
            max_length=320,
            description="What the chat is about, as `kind:ref`: `route:/contracts`, `contract:weekly`, "
            "`draft:<id>`, `event:<id>`; a bare id is a notification event.",
        ),
    ] = None,
    detail: Annotated[
        str | None,
        Field(max_length=4000, description="What you know about it, in words, e.g. a notification's text."),
    ] = None,
) -> ChatSession:
    """Open a chat session, optionally about a screen, a contract, a draft or a notification event.

    Raises:
        HeinzelError: If the context's kind is unknown.
    """
    return service.open(parse_context(context, detail))


# Spends money, within the per-message and the session cap.
@module.operation(read_only=False, heads=HEADS, requires=["agent-runner:read"], audience="everyday")
def send(
    service: ChatService,
    runner: AgentRunner,
    session: Session,
    message: Annotated[str, Field(min_length=1, max_length=4000, description="What to say.")],
    budget_usd: Annotated[
        float | None,
        Field(gt=0, description="Most this answer may cost in US dollars, within the per-message and session caps."),
    ] = None,
) -> ChatSession:
    """Send a message in a chat session and wait for the answer; this may cost money.

    Raises:
        HeinzelError: If the session is unknown, busy, full or at its cap, or the runner is misconfigured.
    """
    return service.send(runner, session, message, budget_usd)


@module.operation(read_only=False, heads=HEADS, audience="everyday")
def confirm(service: ChatService, session: Session, action: Action) -> ChatSession:
    """Run an action the agent proposed, with exactly the arguments shown; only you can confirm.

    Raises:
        HeinzelError: If there is no such pending action.
    """
    return service.confirm(session, action)


@module.operation(read_only=False, heads=HEADS, audience="everyday")
def decline(service: ChatService, session: Session, action: Action) -> ChatSession:
    """Decline an action the agent proposed; it never runs.

    Raises:
        HeinzelError: If there is no such pending action.
    """
    return service.decline(session, action)


@module.operation(heads=HEADS)
def show(store: ChatStore, session: Session) -> ChatSession:
    """Show a chat session: its context, messages, actions and costs.

    Raises:
        HeinzelError: If there is no such session.
    """
    return store.load(session)


@module.operation(name="list", heads=HEADS, audience="everyday")
def sessions(store: ChatStore) -> list[ChatSummary]:
    """List chat sessions, most recent first; they are kept for system.runner.chat.keep_days."""
    return store.sessions()


@module.operation(read_only=False, heads=HEADS)
def remove(store: ChatStore, session: Session) -> list[ChatSummary]:
    """Delete a chat session and everything it holds; returns the sessions left.

    Raises:
        HeinzelError: If there is no such session.
    """
    store.remove(session)
    return store.sessions()


__all__ = [
    "ChatAction",
    "ChatContext",
    "ChatCosts",
    "ChatMessage",
    "ChatService",
    "ChatSession",
    "ChatSettings",
    "ChatStore",
    "ChatSummary",
    "module",
]
