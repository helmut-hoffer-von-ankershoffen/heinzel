"""The transport-neutral projection of one durable personal conversation (ADR 0049).

These are private application models, not kernel primitives. Heads render the same snapshot; providers never own
the public session identity, pending decisions or contract versions.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from heinzel.kernel import Billing, Effort

SessionId = Annotated[str, Field(pattern=r"^hz-[0-9A-HJKMNP-TV-Z]{26}$")]
RequestId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
BackendMode = Literal["api", "cli-model", "native-cli"]


class BackendBinding(BaseModel):
    """The effective choice pinned to a turn; changing it takes effect between turns."""

    backend: str
    mode: BackendMode
    model: str | None = None
    effort: Effort | None = None
    revision: int = Field(default=1, ge=1)
    capabilities: list[str] = Field(default_factory=list)


class AttachmentRef(BaseModel):
    """One session's immutable private upload; no server path or arbitrary URL is exposed."""

    id: RequestId
    session_id: SessionId
    name: str = Field(max_length=200)
    mime: str = Field(max_length=100)
    bytes: int = Field(ge=0)
    sha256: Digest
    state: Literal["retained", "expired"] = "retained"


class TextBlock(BaseModel):
    """A portable text answer, also the fallback for heads without rich rendering."""

    kind: Literal["text"] = "text"
    id: str
    text: str


class RendererBlock(BaseModel):
    """Validated data for an installed renderer; executable markup is never a message block."""

    kind: Literal["renderer"] = "renderer"
    id: str
    renderer: str
    manifest_sha256: Digest
    data: dict[str, JsonValue]
    fallback: str = Field(min_length=1)


class ReferenceBlock(BaseModel):
    """A question, proposal or attachment whose state is held by the application service."""

    kind: Literal["question", "proposal", "attachment"]
    id: str
    ref: str
    fallback: str = Field(min_length=1)


ResultBlock = Annotated[TextBlock | RendererBlock | ReferenceBlock, Field(discriminator="kind")]


class SessionMessage(BaseModel):
    """A visible message or an explicit gap where private body content expired."""

    id: RequestId
    seq: int = Field(ge=1)
    turn_id: str | None = None
    role: Literal["user", "assistant", "tool", "system"]
    text: str = ""
    blocks: list[ResultBlock] = Field(default_factory=list[ResultBlock])
    attachment_ids: list[str] = Field(default_factory=list)
    at: datetime
    status: Literal["pending", "completed", "failed", "interrupted", "expired", "timeout", "budget"] = "completed"
    backend_binding: BackendBinding | None = None
    billing: Billing | None = None
    cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class RecipientPreview(BaseModel):
    """A resolved recipient in the exact snapshot awaiting human review."""

    principal_id: str
    name: str
    address: str
    role: str


class ActionPreview(BaseModel):
    """Service-produced text and recipients; the client never infers a target or recipient list."""

    revision: int = Field(ge=1)
    sha256: Digest
    kind: Literal["operation", "document-section", "email"]
    title: str
    target: str
    source_revision: str | None = None
    before: str | None = None
    after: str | None = None
    diff: str | None = None
    recipients: list[RecipientPreview] = Field(default_factory=list[RecipientPreview])
    recipient_snapshot_hash: Digest | None = None


class ActionProposal(BaseModel):
    """The exact stored call a human may authorize; no decision proof is returned in a snapshot."""

    id: RequestId
    turn_id: str
    operation: str
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    argument_hash: Digest
    contract_version: int | None = Field(default=None, ge=1)
    grant_revision: int = Field(ge=1)
    policy_revision: int = Field(ge=1)
    status: Literal["pending", "running", "done", "failed", "declined", "uncertain"] = "pending"
    command: str
    preview: ActionPreview | None = None


class Question(BaseModel):
    """An answerable, stored question; answering it grants no new permission."""

    id: RequestId
    turn_id: str
    revision: int = Field(default=1, ge=1)
    prompt: str = Field(min_length=1)
    answer_schema: dict[str, JsonValue] = Field(default_factory=dict)
    choices: list[str] = Field(default_factory=list)
    status: Literal["waiting", "answered", "cancelled"] = "waiting"


class ContractVersion(BaseModel):
    """A versioned contract artifact; only an explicit approval can activate its schedule."""

    id: RequestId
    version: int = Field(ge=1)
    sha256: Digest
    status: Literal["proposed", "approved", "superseded", "rejected"] = "proposed"
    title: str
    text: str
    parent_version: int | None = Field(default=None, ge=1)
    reason: str | None = None
    created_at: datetime
    approved_at: datetime | None = None


class SessionCosts(BaseModel):
    """The same visible budget for every head and backend, with plan estimates labelled separately."""

    spent_usd: float = Field(default=0, ge=0, allow_inf_nan=False)
    session_max_usd: float = Field(gt=0, allow_inf_nan=False)
    message_max_usd: float = Field(ge=0, allow_inf_nan=False)
    unknown_turns: int = Field(default=0, ge=0)


class HeinzelAppearance(BaseModel):
    """Personal appearance on the renderer's closed vocabulary; state and theme are application-owned."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    age: Literal["baby", "young", "grown", "old"] = "grown"
    beard: Literal["none", "short", "medium", "long"] | None = None
    hat: Literal["red", "rust", "ochre", "green", "teal", "blue", "purple", "slate"] | None = None
    shirt: Literal["green", "blue", "purple", "red-brown", "ochre", "teal", "slate", "rose"] | None = None
    build: Literal["slim", "regular", "round"] = "regular"
    accessories: list[Literal["umbrella", "sunglasses", "scarf"]] = Field(
        default_factory=list[Literal["umbrella", "sunglasses", "scarf"]]
    )


class SessionSnapshot(BaseModel):
    """Current metadata plus one message page; a fresh Heinzel can chat before it has an approved contract."""

    session_id: SessionId
    kind: Literal["heinzel"] = "heinzel"
    heinzel_id: SessionId
    name: str
    appearance: HeinzelAppearance = Field(default_factory=HeinzelAppearance)
    profile_revision: int = Field(default=1, ge=1)
    contract_version: int | None = Field(default=None, ge=1)
    state: Literal["proposed", "asleep", "awake", "waiting", "paused", "retired"] = "proposed"
    last_seq: int = Field(default=0, ge=0)
    messages: list[SessionMessage] = Field(default_factory=list[SessionMessage])
    pending_actions: list[ActionProposal] = Field(default_factory=list[ActionProposal])
    questions: list[Question] = Field(default_factory=list[Question])
    contracts: list[ContractVersion] = Field(default_factory=list[ContractVersion])
    backend_binding: BackendBinding | None = None
    costs: SessionCosts
    attachments: list[AttachmentRef] = Field(default_factory=list[AttachmentRef])
    next_after_seq: int | None = Field(default=None, ge=0)
    has_more: bool = False

    @model_validator(mode="after")
    def one_identity(self) -> "SessionSnapshot":
        """A backend or transport cannot substitute its own id for the Heinzel's session.

        Raises:
            ValueError: If the two public identities differ.
        """
        if self.session_id != self.heinzel_id:
            msg = "A Heinzel conversation uses its Heinzel id as the session id"
            raise ValueError(msg)
        return self


class SessionSummary(BaseModel):
    """Picker metadata from the index, including proposed chats with no approved contract."""

    session_id: SessionId
    name: str
    state: Literal["proposed", "asleep", "awake", "waiting", "paused", "retired"]
    last_seq: int = Field(ge=0)
    updated_at: datetime
    contract_version: int | None = Field(default=None, ge=1)


class SessionList(BaseModel):
    """The personal conversations visible to this installation's current identity."""

    sessions: list[SessionSummary] = Field(default_factory=list[SessionSummary])


class MessageRequest(BaseModel):
    """An idempotent inbox request, durably accepted before any provider is called."""

    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: RequestId
    text: str = Field(default="", max_length=32000)
    when_busy: Literal["follow_up", "steer"] = "follow_up"
    attachment_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def has_content(self) -> "MessageRequest":
        """An attachment-only request is valid; an entirely empty one is not.

        Raises:
            ValueError: If neither text nor attachments were supplied.
        """
        if not self.text.strip() and not self.attachment_ids:
            msg = "Say something or attach a file"
            raise ValueError(msg)
        return self


class MessageAccepted(BaseModel):
    """The inbox acknowledgement; it reports durable acceptance, not completed model work."""

    request_id: RequestId
    session_id: SessionId
    disposition: Literal["started", "queued", "steered"]
    turn_id: str | None = None
    inbox_position: int = Field(ge=1)


class AnswerRequest(BaseModel):
    """An idempotent answer to the exact question version the person saw."""

    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: RequestId
    question_id: RequestId
    question_revision: int = Field(ge=1)
    answer: JsonValue
