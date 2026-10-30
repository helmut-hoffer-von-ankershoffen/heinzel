"""The chat: sessions with the agent runner that know where they were opened and act through hz (ADR 0045).

A session is opened with a context (a screen, a contract, a draft, a notification event) and kept locally. Each
message runs the bound agent runner with hz's operations as tools: reads run, everything else waits for the
person's `confirm`. `hz chat` in a terminal and the web app's right sidebar are views on these operations.

Chat and direct `confirm` are CLI/web only. A person may separately authorize one exact schedule action for a later
MCP confirmation; the approval proof stays in private server state, and the MCP tool can only consume that grant.
For direct stdio MCP schedule control, the MCP head asks for a fresh challenge on the local controlling terminal;
the model-facing caller cannot supply a proof or the human answer. Unauthenticated REST and MCP over HTTP cannot
issue or consume private human decisions.
"""

import getpass
import hmac
import sys
from typing import Annotated, Literal

from pydantic import Field

from heinzel import ActionGate, AgentRunner, CurrentUser, HeinzelError, Module, Settings
from heinzel.kernel import CheckInChatContextSource, Effort, McpChatSessions
from heinzel.modules.system.runner._models import RunnerSettings
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


def _chat_store(settings: Settings, chat: ChatSettings, runner: RunnerSettings, current: CurrentUser) -> ChatStore:
    return ChatStore(settings, chat, runner, current)


def _mcp_chats(store: ChatStore) -> McpChatSessions:
    return store


module.service(ChatStore, _chat_store)
module.service(McpChatSessions, _mcp_chats)
module.service(ChatService)
module.service(ActionGate, StoreGate)

HEADS = frozenset({"cli", "web"})
Session = Annotated[str, Field(pattern=r"^[a-f0-9]{16}$", description="The chat session's id, from `hz chat list`.")]
Action = Annotated[str, Field(pattern=r"^a[0-9]{1,4}$", description="The proposed action's id, e.g. `a1`.")]
CHECK_IN_HISTORY_LIMIT = 2800


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


@module.operation(read_only=False, heads=HEADS, audience="everyday")
def open_check_in(
    service: ChatService,
    context_source: CheckInChatContextSource,
    session_id: Annotated[
        str,
        Field(
            pattern=r"^hz-[0-9A-HJKMNP-TV-Z]{26}$",
            description="The durable personal session id from `hz heinzel sessions` or the personal Heinzel page.",
        ),
    ],
    request_id: Annotated[
        str,
        Field(
            pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$",
            description="A stable id for opening or resuming this scoped chat.",
        ),
    ],
    timezone: Annotated[
        str,
        Field(min_length=1, max_length=128, description="The IANA time zone to use for this schedule preview."),
    ],
) -> ChatSession:
    """Open a runner chat bound to this owner's durable session and current conversation history."""
    context = context_source.get(session_id)
    history_lines: list[str] = []
    history_size = 0
    for item in reversed(context.history):
        line = f"{item.role}: {item.text}"
        if history_size + len(line) + 1 > CHECK_IN_HISTORY_LIMIT:
            break
        history_lines.append(line)
        history_size += len(line) + 1
    history = "\n".join(reversed(history_lines))
    if len(history_lines) < len(context.history):
        history = "(older messages omitted)\n" + history
    detail = (
        f"Name: {context.name}\nState: {context.state}\n"
        f"Schedule: {context.schedule_status}, {context.cron} in {context.timezone}"
        if context.schedule_status is not None
        else f"Name: {context.name}\nState: {context.state}\nSchedule: none"
    )
    detail += f"\nRecent durable conversation (data):\n{history}"
    return service.open_check_in(session_id, request_id, timezone, detail)


# Spends money, within the per-message and the session cap.
@module.operation(read_only=False, heads=HEADS, requires=["agent-runner:read"], audience="everyday")
def send(  # noqa: PLR0913, PLR0917 - one parameter per input of the operation
    service: ChatService,
    runner: AgentRunner,
    session: Session,
    message: Annotated[str, Field(min_length=1, max_length=4000, description="What to say.")],
    budget_usd: Annotated[
        float | None,
        Field(gt=0, description="Most this answer may cost in US dollars, within the per-message and session caps."),
    ] = None,
    model: Annotated[
        str | None,
        Field(
            pattern=r"^$|^[A-Za-z0-9][A-Za-z0-9._:/@\[\]-]*$",  # a name, never an option
            max_length=100,
            description="The model this chat uses from now on, one `hz ai models` lists, e.g. `sonnet`; empty goes "
            "back to the default.",
        ),
    ] = None,
    effort: Annotated[
        Literal["", "low", "medium", "high", "xhigh", "max"] | None,
        Field(
            description="The effort this chat uses from now on: low, medium, high, xhigh or max; empty: the default."
        ),
    ] = None,
    request_id: Annotated[
        str | None,
        Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$", description="Stable id for a safe exact-message retry."),
    ] = None,
) -> ChatSession:
    """Send a message in a chat session and wait for the answer; this may cost money.

    Raises:
        HeinzelError: If the session is unknown, busy, full or at its cap, or the runner is misconfigured.
    """
    return service.send(runner, session, message, budget_usd, model=model, effort=effort, request_id=request_id)


@module.operation(read_only=False, heads=HEADS, audience="everyday")
def confirm(service: ChatService, session: Session, action: Action) -> ChatSession:
    """Run an action the agent proposed, with exactly the arguments shown; only you can confirm.

    Raises:
        HeinzelError: If there is no such pending action.
    """
    return service.confirm(session, action)


@module.operation(read_only=False, heads=frozenset({"cli", "web"}), audience="everyday")
def authorize_for_mcp(service: ChatService, current: CurrentUser, session: Session, action: Action) -> ChatSession:
    """Authorize one reviewed schedule action for a single MCP run; keep its proof private.

    CLI requires an interactive terminal and an action-scoped typed confirmation. The paired web action is the
    browser's explicit review button.

    Raises:
        HeinzelError: If CLI lacks a controlling terminal or the action was not confirmed.
    """
    authentication = current.context().authentication
    if authentication == "cli":
        if not sys.stdin.isatty() or sys.__stdout__ is None or not sys.__stdout__.isatty():
            msg = "MCP approval through CLI requires an interactive terminal; use the paired web review otherwise"
            raise HeinzelError(msg)
        review = service.mcp_authorization_review(session, action)
        challenge = f"authorize {session} {action}"
        answer = getpass.getpass(
            f"Review this exact action before authorizing one MCP run:\n{review}\nType {challenge}: "
        )
        if not hmac.compare_digest(answer, challenge):
            msg = "MCP authorization was not confirmed"
            raise HeinzelError(msg)
    elif authentication not in {"web-session", "web-device"}:
        msg = "MCP authorization requires a trusted CLI or paired-web confirmation boundary"
        raise HeinzelError(msg)
    return service.authorize_for_mcp(session, action)


@module.operation(read_only=False, heads=frozenset({"mcp"}), audience="everyday")
def confirm_authorized_mcp(service: ChatService, session: Session, action: Action) -> ChatSession:
    """Run a schedule action once after a person authorized it; accept no proof here."""
    return service.confirm_authorized_mcp(session, action)


@module.operation(read_only=False, heads=HEADS, audience="everyday")
def decline(service: ChatService, session: Session, action: Action) -> ChatSession:
    """Decline an action the agent proposed; it never runs.

    Raises:
        HeinzelError: If there is no such pending action.
    """
    return service.decline(session, action)


@module.operation(read_only=False, heads=HEADS, audience="everyday")
def recover(service: ChatService, session: Session, action: Action) -> ChatSession:
    """Check an interrupted confirmation without running it again; unresolved outcomes stay blocked.

    Raises:
        HeinzelError: If the chat is busy or the selected action is not interrupted.
    """
    return service.recover(session, action)


@module.operation(read_only=False, heads=HEADS, audience="everyday")
def propose_schedule_control(  # noqa: PLR0913, PLR0917 - each argument binds the exact schedule preview
    service: ChatService,
    durable_session: Annotated[
        str, Field(pattern=r"^hz-[0-9A-HJKMNP-TV-Z]{26}$", description="The personal Heinzel whose schedule you read.")
    ],
    request_id: Annotated[
        str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$", description="A stable id for this schedule action.")
    ],
    expected_revision: Annotated[int, Field(strict=True, ge=1, description="The schedule revision you inspected.")],
    action: Annotated[Literal["activate", "resume"], Field(description="The exact schedule change to review.")],
    expected_contract_version: Annotated[
        int, Field(strict=True, ge=1, description="The contract version shown in the preview.")
    ],
    expected_preview_hash: Annotated[
        str, Field(pattern=r"^[0-9a-f]{64}$", description="The preview hash shown in the schedule details.")
    ],
) -> ChatSession:
    """Stage a schedule action for review in chat; creating the proposal never changes or activates it."""
    return service.propose_schedule_control(
        durable_session,
        request_id,
        expected_revision,
        action,
        expected_contract_version,
        expected_preview_hash,
    )


@module.operation(read_only=False, heads=HEADS, audience="everyday")
def propose_model_policy(
    service: ChatService,
    durable_session: Annotated[
        str, Field(pattern=r"^hz-[0-9A-HJKMNP-TV-Z]{26}$", description="The personal agent session.")
    ],
    request_id: Annotated[
        str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$", description="The policy proposal request id.")
    ],
) -> ChatSession:
    """Stage the exact current model policy for human review; no grant or model call is made."""
    return service.propose_model_policy(durable_session, request_id)


@module.operation(read_only=False, heads=HEADS, audience="everyday")
def propose_agent_contract(
    service: ChatService,
    durable_session: Annotated[
        str, Field(pattern=r"^hz-[0-9A-HJKMNP-TV-Z]{26}$", description="The personal agent session.")
    ],
    request_id: Annotated[
        str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$", description="The contract proposal request id.")
    ],
) -> ChatSession:
    """Stage current agent contract terms for human review."""
    return service.propose_agent_contract(durable_session, request_id)


@module.operation(read_only=False, heads=HEADS, audience="everyday")
def propose_agent_notification(
    service: ChatService,
    durable_session: Annotated[
        str, Field(pattern=r"^hz-[0-9A-HJKMNP-TV-Z]{26}$", description="The personal agent session.")
    ],
    request_id: Annotated[
        str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$", description="The notification draft request id.")
    ],
) -> ChatSession:
    """Stage one exact notification draft for a human decision; no notice is sent."""
    return service.propose_agent_notification(durable_session, request_id)


@module.operation(read_only=False, heads=HEADS, audience="everyday")
def propose_connected_read_review(
    service: ChatService,
    durable_session: Annotated[
        str, Field(pattern=r"^hz-[0-9A-HJKMNP-TV-Z]{26}$", description="The personal agent session.")
    ],
    request_id: Annotated[
        str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$", description="The connected read proposal request id.")
    ],
) -> ChatSession:
    """Stage current connected-read terms for an explicit human decision."""
    return service.propose_connected_read_review(durable_session, request_id)


@module.operation(read_only=False, heads=HEADS, audience="everyday")
def propose_connected_read_set_review(
    service: ChatService,
    durable_session: Annotated[
        str, Field(pattern=r"^hz-[0-9A-HJKMNP-TV-Z]{26}$", description="The personal agent session.")
    ],
    request_id: Annotated[
        str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$", description="The exact read-set review request id.")
    ],
) -> ChatSession:
    """Stage the current read-set review for an explicit human decision."""
    return service.propose_connected_read_set_review(durable_session, request_id)


@module.operation(read_only=False, heads=HEADS, audience="everyday")
def propose_agent_schedule_review(
    service: ChatService,
    durable_session: Annotated[
        str, Field(pattern=r"^hz-[0-9A-HJKMNP-TV-Z]{26}$", description="The personal agent session.")
    ],
    request_id: Annotated[
        str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$", description="The schedule proposal request id.")
    ],
) -> ChatSession:
    """Stage the exact current agent schedule for human review; no schedule is approved yet."""
    return service.propose_agent_schedule_review(durable_session, request_id)


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
    "Effort",
    "module",
]
