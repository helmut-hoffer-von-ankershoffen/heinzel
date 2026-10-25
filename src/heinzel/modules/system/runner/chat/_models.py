"""What a chat session is made of: its context, its messages, the actions the agent proposed, and its costs."""

from datetime import datetime
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, Field, JsonValue, StringConstraints

from heinzel import ModuleSettings
from heinzel.kernel import Billing, Effort

ContextKind = Literal["route", "contract", "draft", "event", "text"]
SessionId = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{16}$")]
"""16 hex characters: a session's directory name, so never a path."""
ActionId = Annotated[str, StringConstraints(pattern=r"^a[0-9]{1,4}$")]


class ChatSettings(ModuleSettings):
    """Settings of chat sessions: the `[system.runner.chat]` table or `HZ_SYSTEM_RUNNER_CHAT__*` variables.

    Each message may cost at most `[system.runner] ask_max_usd`; a session at most `session_max_usd` in all.
    """

    section_title: ClassVar[str | None] = "Chat"  # the heading in Settings

    session_max_usd: float = Field(
        default=2.0,
        gt=0,
        allow_inf_nan=False,
        title="Most per chat",
        description="The most one chat may cost in US dollars, all its messages together. One "
        "message never costs more than the most per answer.",
        json_schema_extra={"x-unit": "usd"},
    )
    keep_days: int = Field(
        default=30,
        ge=1,
        le=3650,
        title="Keep chats for",
        description="How long a chat is kept after its last message, then removed.",
        json_schema_extra={"x-unit": "days"},
    )
    keep_sessions: int = Field(
        default=50,
        ge=1,
        le=1000,
        title="Chats kept",
        description="How many chats are kept at most; the oldest beyond that are removed.",
    )

    tools: Literal["everyday", "all"] = Field(
        default="everyday",
        title="What the chat can use",
        description="Which operations the chat's agent can use: the everyday ones, or all of them. Every tool is sent "
        "with every message, so fewer make each message cheaper.",
        json_schema_extra={"x-expert": True},
    )

    ignored_in_project: ClassVar[frozenset[str]] = frozenset({"session_max_usd", "tools"})


class ChatContext(BaseModel):
    """What a chat was opened about: the screen, a contract, a draft or a notification event; it seeds the chat."""

    kind: ContextKind = Field(description="`route`, `contract`, `draft`, `event` or `text`.")
    ref: str = Field(
        default="",
        max_length=300,
        description="Which one: a route such as `/contracts/weekly`, a contract name, an id.",
    )
    detail: str = Field(
        default="",
        max_length=4000,
        description="What the opener knows about it, in words (a notification's title and body); email addresses are "
        "never sent to the agent.",
    )


class ChatAction(BaseModel):
    """An operation the agent proposed; it runs only when the person confirms it, with exactly these arguments."""

    id: ActionId = Field(description="The action's id in its session, e.g. `a1`.")
    tool: str = Field(description="The tool the agent called, e.g. `system_config_set`.")
    module: str = Field(description="The operation's module, e.g. `system.config`.")
    operation: str = Field(description="The operation's name, e.g. `set`.")
    command: str = Field(description="The same as a command line: what the person confirms.")
    arguments: dict[str, JsonValue] = Field(default_factory=dict, description="The arguments it runs with.")
    status: Literal["pending", "running", "done", "failed", "declined"] = Field(
        default="pending",
        description="`pending` until the person confirms it (`running`, then `done` or `failed`) or declines it.",
    )
    result: str = Field(default="", description="What it returned, or why it failed; empty while pending.")
    proposed: datetime = Field(description="When the agent proposed it.")
    decided: datetime | None = Field(default=None, description="When the person confirmed or declined it.")


class ChatMessage(BaseModel):
    """One message of a chat."""

    role: Literal["user", "assistant", "action"] = Field(
        description="`user`, `assistant`, or `action`: what a confirmed or declined action did."
    )
    text: str = Field(description="The message; for an assistant, empty when the run did not complete.")
    at: datetime = Field(description="When it was written.")
    status: str = Field(default="completed", description="For an assistant message: how the run ended.")
    detail: str = Field(default="", description="Why the run did not complete; empty when it did.")
    fix: str = Field(default="", description="A command that fixes why it did not complete, if there is one.")
    cost_usd: float | None = Field(default=None, description="What the answer cost in US dollars, if reported.")
    budget_usd: float | None = Field(
        default=None,
        description="The cap the answer ran with; what it counts as against the session's cap when no cost is "
        "reported.",
    )
    runner: str = Field(default="", description="The runner that answered, e.g. `claude-code`.")
    tools: dict[str, int] = Field(default_factory=dict, description="Tools the agent called, by name.")
    actions: list[str] = Field(default_factory=list, description="Ids of the actions the agent proposed in it.")
    warnings: list[str] = Field(default_factory=list, description="Limits the runner could not enforce.")
    access: str | None = Field(default=None, description="The AI access in use when it answered, e.g. `claude-code`.")
    model: str | None = Field(default=None, description="The model that answered, as the runner reported it.")
    effort: Effort | None = Field(default=None, description="The effort it was asked for; none: the default.")
    billing: Billing | None = Field(
        default=None,
        description="`plan`: on a subscription, so `cost_usd` is what it would cost at API prices, covered by the "
        "plan; `api`: billed per token; none when the runner can't tell.",
    )
    duration_seconds: float | None = Field(default=None, description="How long the answer took.")
    input_tokens: int | None = Field(default=None, description="Tokens sent, without cache reads, if reported.")
    output_tokens: int | None = Field(default=None, description="Tokens produced, if reported.")
    cache_read_tokens: int | None = Field(default=None, description="Input tokens read from the prompt cache.")
    cache_write_tokens: int | None = Field(default=None, description="Input tokens written to the prompt cache.")


class ChatCosts(BaseModel):
    """What a session has cost and may still cost: shown before every message."""

    spent_usd: float = Field(description="What its answers cost so far, as reported.")
    session_max_usd: float = Field(description="Most the whole session may cost.")
    message_max_usd: float = Field(description="Most the next message may cost: the per-message cap or what is left.")


class ChatSession(BaseModel):
    """A chat: its context, messages and actions, kept locally in the state directory."""

    id: SessionId = Field(description="The session's id, for `hz chat --session` and the web app.")
    title: str = Field(description="Its first message, shortened, or its context.")
    created: datetime = Field(description="When it was opened.")
    updated: datetime = Field(description="When it last changed.")
    context: ChatContext | None = Field(default=None, description="What it was opened about, if anything.")
    messages: list[ChatMessage] = Field(
        default_factory=list[ChatMessage], description="The conversation, oldest first."
    )
    actions: list[ChatAction] = Field(
        default_factory=list[ChatAction], description="What the agent proposed, oldest first."
    )
    costs: ChatCosts = Field(description="What it cost and may still cost.")
    model: str | None = Field(default=None, description="The model this chat asks for; none: the runner's default.")
    effort: Effort | None = Field(default=None, description="The effort this chat asks for; none: the default.")

    @property
    def pending(self) -> list[ChatAction]:
        """Actions waiting for the person."""
        return [action for action in self.actions if action.status == "pending"]


class ChatSummary(BaseModel):
    """A session in a list."""

    id: str = Field(description="The session's id.")
    title: str = Field(description="Its first message, shortened, or its context.")
    updated: datetime = Field(description="When it last changed.")
    messages: int = Field(description="How many messages it has.")
    pending: int = Field(description="Actions waiting for confirmation.")
    spent_usd: float = Field(description="What it cost so far.")
