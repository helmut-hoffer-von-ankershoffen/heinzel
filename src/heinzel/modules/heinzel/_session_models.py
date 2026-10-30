"""The transport-neutral projection of one durable personal conversation (ADR 0049).

These are private application models, not kernel primitives. Heads render the same snapshot; providers never own
the public session identity, pending decisions or contract versions.
"""

import json
import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from heinzel.kernel import Billing, Effort, skill_name_problem

SessionId = Annotated[str, Field(pattern=r"^hz-[0-9A-HJKMNP-TV-Z]{26}$")]
RequestId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
BackendMode = Literal["api", "cli-model", "native-cli"]
MessageOrigin = Literal["unknown", "cli", "mcp-stdio", "daemon", "api-bearer", "web-session", "web-device"]
MAX_DRAFT_FILES = 16
MAX_DRAFT_PATH = 160
MAX_DRAFT_FILE_BYTES = 24 * 1024
MAX_DRAFT_BYTES = 48 * 1024


class SkillDraftReference(BaseModel):
    """An owner-scoped reference, without draft content."""

    request_id: RequestId = Field(description="The stable request identity for the private draft or sharing request.")
    seq: int = Field(description="Its position in the owner's session journal.", gt=0)


class SkillDraftPage(BaseModel):
    """A stable page in control-journal order."""

    items: list[SkillDraftReference] = Field(description="Private draft references in journal order.")
    next_cursor: int | None = Field(default=None, description="Resume after this journal position; null at the end.")


class SkillSharingProposal(BaseModel):
    """Private request for later review; no membership, approval or publication is asserted."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    session_id: SessionId = Field(description="The authenticated owner's personal session.")
    request_id: RequestId = Field(description="The stable identity of this exact sharing review request.")
    draft_request_id: RequestId = Field(description="The immutable skill draft being proposed for sharing.")
    draft_sha256: Digest = Field(description="The exact private draft fingerprint, including its intended target.")
    requested_scope: Literal["team", "org", "site", "company"] = Field(
        description="The requested, unverified audience."
    )
    scope_ref: RequestId = Field(description="The opaque requested audience identity; no authority is established.")
    status: Literal["awaiting_review"] = Field(default="awaiting_review", description="No sharing has been approved.")
    authority: Literal["unverified"] = Field(
        default="unverified", description="Membership and publishing authority are unverified."
    )


class SkillSharingPage(BaseModel):
    """Owner-private references to pending sharing requests."""

    items: list[SkillDraftReference] = Field(description="Owner-private pending sharing request references.")
    next_cursor: int | None = Field(default=None, description="Resume after this journal position; null at the end.")


class BackendBinding(BaseModel):
    """The effective choice pinned to a turn; changing it takes effect between turns."""

    backend: str = Field(description="The selected installed AI backend.")
    mode: BackendMode = Field(description="Whether the turn uses direct API, a CLI model bridge or a native CLI loop.")
    model: str | None = Field(default=None, description="The selected model, when explicitly configured.")
    effort: Effort | None = Field(default=None, description="The selected reasoning effort, when supported.")
    revision: int = Field(description="The binding revision pinned to this turn.", default=1, ge=1)
    capabilities: list[str] = Field(
        description="Capabilities the selected adapter has proved it supports.", default_factory=list
    )


class AttachmentRef(BaseModel):
    """One session's immutable private upload; no server path or arbitrary URL is exposed."""

    id: RequestId = Field(description="The opaque retained upload id.")
    session_id: SessionId = Field(description="The owning durable session id.")
    name: str = Field(description="The display filename; never a server path.", max_length=200)
    mime: str = Field(description="The validated media type.", max_length=100)
    bytes: int = Field(description="The retained content size in bytes.", ge=0)
    sha256: Digest = Field(description="The exact retained content hash.")
    state: Literal["retained", "expired"] = Field(
        default="retained", description="Whether private content remains available."
    )


class TextBlock(BaseModel):
    """A portable text answer, also the fallback for heads without rich rendering."""

    kind: Literal["text"] = Field(default="text", description="A portable plain-text block.")
    id: str = Field(description="The stable block id within its message.")
    text: str = Field(description="The plain text to render.")


class RendererBlock(BaseModel):
    """Validated data for an installed renderer; executable markup is never a message block."""

    kind: Literal["renderer"] = Field(default="renderer", description="A block for an installed data renderer.")
    id: str = Field(description="The stable block id within its message.")
    renderer: str = Field(description="The installed renderer identifier.")
    manifest_sha256: Digest = Field(description="The exact renderer manifest fingerprint.")
    data: dict[str, JsonValue] = Field(description="Validated renderer data without executable markup.")
    fallback: str = Field(description="Plain-text content for heads without this renderer.", min_length=1)


class ReferenceBlock(BaseModel):
    """A question, proposal or attachment whose state is held by the application service."""

    kind: Literal["question", "proposal", "attachment"] = Field(
        description="Whether this block references a question, proposal or attachment."
    )
    id: str = Field(description="The stable block id within its message.")
    ref: str = Field(description="The application-owned object id.")
    fallback: str = Field(description="Plain-text explanation of the referenced object.", min_length=1)


ResultBlock = Annotated[TextBlock | RendererBlock | ReferenceBlock, Field(discriminator="kind")]


class PersonalArtifact(BaseModel):
    """A principal-submitted association; neither access nor provider ownership has been verified."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    session_id: SessionId = Field(description="The personal session this association belongs to.")
    request_id: RequestId = Field(description="The stable registration request identity.")
    provider: Literal["google"] = Field(
        default="google", description="The user-supplied provider, not an access grant."
    )
    kind: Literal["document", "spreadsheet", "presentation"] = Field(description="The declared Google file type.")
    ref: str = Field(
        description="Canonical Google editor URL supplied under the authenticated principal; not verified remotely.",
        max_length=256,
    )
    provenance: Literal["principal-submitted"] = Field(
        default="principal-submitted", description="How this association was recorded; not provider ownership."
    )

    @model_validator(mode="after")
    def validate_ref(self) -> "PersonalArtifact":
        """Reject noncanonical URLs, credentials, query strings and mismatched file types.

        Raises:
            ValueError: If the declared type and canonical editor URL do not match.
        """
        path = {"document": "document", "spreadsheet": "spreadsheets", "presentation": "presentation"}[self.kind]
        if not re.fullmatch(rf"https://docs\.google\.com/{path}/d/[A-Za-z0-9_-]{{10,128}}/edit", self.ref):
            msg = "Provide a canonical Google editor link matching the declared file type"
            raise ValueError(msg)
        return self


class PersonalArtifacts(BaseModel):
    """Local associations, not a list of files the provider says are accessible."""

    session_id: SessionId = Field(description="The owning personal session.")
    artifacts: list[PersonalArtifact] = Field(
        description="Locally recorded associations; provider access is not verified."
    )


def _validate_skill_draft_files(files: dict[str, str]) -> None:
    if not 1 <= len(files) <= MAX_DRAFT_FILES or not files.get("SKILL.md"):
        msg = "Supply a nonempty SKILL.md and at most 16 files"
        raise ValueError(msg)
    seen: set[str] = set()
    for path, content in files.items():
        script = re.fullmatch(r"scripts/(?:[A-Za-z0-9_-]+/){0,2}[A-Za-z0-9_-]+\.py", path)
        supporting = re.fullmatch(
            r"(?:references|templates|assets)/(?:[A-Za-z0-9_-]+/){0,2}[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)?", path
        )
        if path != "SKILL.md" and not (script or supporting):
            msg = "Use safe paths for supporting text or Python scripts"
            raise ValueError(msg)
        if path.casefold() in seen or len(path) > MAX_DRAFT_PATH or "\x00" in content:
            msg = "Draft files contain duplicate or invalid paths or content"
            raise ValueError(msg)
        seen.add(path.casefold())
        try:
            size = len(content.encode("utf-8"))
        except UnicodeError:
            msg = "Draft files must be valid UTF-8 text"
            raise ValueError(msg) from None
        if size > MAX_DRAFT_FILE_BYTES:
            msg = "Each draft file must be at most 24 KiB"
            raise ValueError(msg)


class SkillDraft(BaseModel):
    """Private inert text and requested sharing intent; readiness, publication and grants are not verified."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    session_id: SessionId = Field(description="The authenticated owner's personal session.")
    request_id: RequestId = Field(description="Stable request identity for exact retries.")
    name: str = Field(description="The proposed skill name.", min_length=1, max_length=64)
    requested_scope: Literal["personal", "team", "org", "site", "company"] = Field(
        description="Proposed sharing scope, not an access grant or verified membership."
    )
    scope_ref: RequestId | None = Field(
        default=None, description="Opaque proposed shared-scope reference; not verified."
    )
    files: dict[str, str] = Field(
        description="Exact SKILL.md, supporting text and optional Python scripts; not validated for publication."
    )
    status: Literal["draft"] = Field(default="draft", description="Not published, approved or executable.")
    preview_sha256: Digest = Field(description="Fingerprint of the proposed terms; not an approval proof.")

    @model_validator(mode="after")
    def validate_draft(self) -> "SkillDraft":
        """Validate bounds and names without opening, writing or executing any supplied path.

        Raises:
            ValueError: If a name, sharing intent, file path or size is invalid.
        """
        if skill_name_problem(self.name):
            msg = "Choose a valid unreserved skill name"
            raise ValueError(msg)
        if (self.requested_scope == "personal") != (self.scope_ref is None):
            msg = "Shared scopes require a target reference; personal drafts do not"
            raise ValueError(msg)
        _validate_skill_draft_files(self.files)
        try:
            size = len(json.dumps(self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True).encode("utf-8"))
        except UnicodeError:
            msg = "Draft files must be valid UTF-8 text"
            raise ValueError(msg) from None
        if size > MAX_DRAFT_BYTES:
            msg = "The complete draft must be at most 48 KiB"
            raise ValueError(msg)
        return self


class ScheduledAgentProposal(BaseModel):
    """An inert execution proposal; creating or reading it never authorizes a run."""

    session_id: SessionId = Field(description="The owner-scoped personal session.")
    request_id: RequestId = Field(description="The proposal's stable request identity.")
    revision: int = Field(description="The sequential execution proposal revision.", ge=1)
    based_on_revision: int = Field(
        description="The previous revision inspected for this proposal; zero for the first.", ge=0
    )
    contract: str = Field(
        description="Exact instructions proposed for each scheduled model turn.", min_length=1, max_length=4000
    )
    permitted_tools: list[str] = Field(
        description="The tools the proposed turn may use; this slice permits none.", max_length=0
    )
    max_usd: float | None = Field(
        description="Proposed API USD limit, or null for subscription plan limits and unknown cost; not yet enforced.",
        gt=0,
        allow_inf_nan=False,
    )
    timeout_seconds: int = Field(description="Proposed maximum duration per turn in seconds.", ge=1, le=300)
    status: Literal["proposed"] = Field(default="proposed", description="No execution approval has been granted.")
    preview_sha256: Digest = Field(description="Hash of the exact proposed terms, not an approval proof.")


class SessionMessage(BaseModel):
    """A visible message or an explicit gap where private body content expired."""

    id: RequestId = Field(description="The stable message id; accepted user messages retain their request id.")
    seq: int = Field(description="The authoritative control-journal position; gaps are permitted.", ge=1)
    turn_id: str | None = Field(default=None, description="The owning turn id, when one has been assigned.")
    role: Literal["user", "assistant", "tool", "system"] = Field(description="Who produced this visible message.")
    origin: MessageOrigin = Field(
        default="unknown",
        description="Authenticated acceptance channel if recorded; not proof of human authorship or permission.",
    )
    text: str = Field(default="", description="The portable plain-text content.")
    blocks: list[ResultBlock] = Field(
        description="Validated rich blocks with portable text fallbacks.", default_factory=list[ResultBlock]
    )
    attachment_ids: list[str] = Field(description="Retained uploads attached to this message.", default_factory=list)
    at: datetime = Field(description="When the message was durably recorded.")
    status: Literal["pending", "completed", "failed", "interrupted", "expired", "timeout", "budget"] = Field(
        default="completed", description="The persisted state of this message or its expired body."
    )
    backend_binding: BackendBinding | None = Field(
        default=None, description="The exact backend choice used for this turn."
    )
    billing: Billing | None = Field(
        default=None, description="Whether usage came from a subscription plan or direct API billing."
    )
    cost_usd: float | None = Field(
        description="Reported or priced cost in US dollars; absent when unknown.",
        default=None,
        ge=0,
        allow_inf_nan=False,
    )


class RecipientPreview(BaseModel):
    """A resolved recipient in the exact snapshot awaiting human review."""

    principal_id: str = Field(description="The resolved recipient identity.")
    name: str = Field(description="The recipient display name.")
    address: str = Field(description="The exact destination address awaiting approval.")
    role: str = Field(description="The resolved role used to select this recipient.")


class ActionPreview(BaseModel):
    """Service-produced text and recipients; the client never infers a target or recipient list."""

    revision: int = Field(description="The immutable preview revision.", ge=1)
    sha256: Digest = Field(description="The exact preview hash bound to a human decision.")
    kind: Literal["operation", "document-section", "email"] = Field(description="The kind of proposed external change.")
    title: str = Field(description="A short description of the change.")
    target: str = Field(description="The exact operation target.")
    source_revision: str | None = Field(default=None, description="The source revision the draft was based on.")
    before: str | None = Field(default=None, description="The original section text, when applicable.")
    after: str | None = Field(default=None, description="The proposed replacement section text.")
    diff: str | None = Field(default=None, description="The service-produced exact change for review.")
    recipients: list[RecipientPreview] = Field(
        description="The exact recipient snapshot awaiting approval.", default_factory=list[RecipientPreview]
    )
    recipient_snapshot_hash: Digest | None = Field(
        default=None, description="The hash of that resolved recipient snapshot."
    )


class ActionProposal(BaseModel):
    """The exact stored call a human may authorize; no decision proof is returned in a snapshot."""

    id: RequestId = Field(description="The stable stored action proposal id.")
    turn_id: str = Field(description="The turn that proposed this action.")
    operation: str = Field(description="The declared installed operation to invoke.")
    arguments: dict[str, JsonValue] = Field(description="The exact stored operation arguments.", default_factory=dict)
    argument_hash: Digest = Field(description="The canonical argument hash bound to approval.")
    contract_version: int | None = Field(description="The approved contract version, when present.", default=None, ge=1)
    grant_revision: int = Field(description="The effective grant revision bound to this proposal.", ge=1)
    policy_revision: int = Field(description="The effective policy revision bound to this proposal.", ge=1)
    status: Literal["pending", "running", "done", "failed", "declined", "uncertain"] = Field(
        default="pending", description="The durable decision or execution state."
    )
    command: str = Field(description="A readable equivalent command for review.")
    preview: ActionPreview | None = Field(
        default=None, description="The exact server-produced change and recipient preview."
    )


class Question(BaseModel):
    """An answerable, stored question; answering it grants no new permission."""

    id: RequestId = Field(description="The stable question id.")
    turn_id: str = Field(description="The turn that is waiting for this answer.")
    revision: int = Field(description="The exact question revision an answer must reference.", default=1, ge=1)
    prompt: str = Field(description="The question shown to the user.", min_length=1)
    answer_schema: dict[str, JsonValue] = Field(
        description="The validated answer shape, when constrained.", default_factory=dict
    )
    choices: list[str] = Field(description="Suggested explicit answer choices.", default_factory=list)
    status: Literal["waiting", "answered", "cancelled"] = Field(
        default="waiting", description="Whether the question is still waiting, answered or cancelled."
    )


class ContractVersion(BaseModel):
    """A versioned contract artifact; only an explicit approval can activate its schedule."""

    id: RequestId = Field(description="The stable contract artifact id.")
    version: int = Field(description="The immutable contract version.", ge=1)
    sha256: Digest = Field(description="The exact artifact hash.")
    status: Literal["proposed", "approved", "superseded", "rejected"] = Field(
        default="proposed", description="The proposal or approval state of this version."
    )
    title: str = Field(description="The human-readable responsibility title.")
    text: str = Field(description="The versioned OpenProse contract text.")
    parent_version: int | None = Field(description="The approved version this amendment follows.", default=None, ge=1)
    reason: str | None = Field(default=None, description="The recorded reason for the amendment or decision.")
    created_at: datetime = Field(description="When this version was proposed.")
    approved_at: datetime | None = Field(default=None, description="When this exact version was approved, if ever.")


class SessionCosts(BaseModel):
    """The same visible budget for every head and backend, with plan estimates labelled separately."""

    spent_usd: float = Field(
        description="Accounted usage in US dollars across this conversation.", default=0, ge=0, allow_inf_nan=False
    )
    session_max_usd: float = Field(
        description="The current policy cap for the whole conversation.", gt=0, allow_inf_nan=False
    )
    message_max_usd: float = Field(
        description="The current allowed cap for the next model turn.", ge=0, allow_inf_nan=False
    )
    unknown_turns: int = Field(description="Turns whose actual spend requires reconciliation.", default=0, ge=0)


class HeinzelAppearance(BaseModel):
    """Personal appearance on the renderer's closed vocabulary; state and theme are application-owned."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    age: Literal["baby", "young", "grown", "old"] = Field(
        default="grown", description="The chosen age of this personal character."
    )
    beard: Literal["none", "short", "medium", "long"] | None = Field(
        default=None, description="The explicit beard style; null inherits the age default."
    )
    hat: Literal["red", "rust", "ochre", "green", "teal", "blue", "purple", "slate"] | None = Field(
        default=None, description="The personal hat colour; null inherits the installed theme."
    )
    shirt: Literal["green", "blue", "purple", "red-brown", "ochre", "teal", "slate", "rose"] | None = Field(
        default=None, description="The personal shirt colour; null inherits the installed theme."
    )
    build: Literal["slim", "regular", "round"] = Field(default="regular", description="The chosen body shape.")
    accessories: list[Literal["umbrella", "sunglasses", "scarf"]] = Field(
        description="Personal decorations from the supported closed vocabulary.",
        default_factory=list[Literal["umbrella", "sunglasses", "scarf"]],
    )


class ProfileProposal(BaseModel):
    """Private suggested identity and appearance; applying it requires a separate user save."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    session_id: SessionId = Field(description="The owner's agent receiving this suggested identity.")
    request_id: RequestId = Field(description="The stable request identifying these exact suggested terms.")
    expected_revision: int = Field(ge=1, description="The current profile revision this suggestion is based on.")
    name: str = Field(min_length=1, max_length=100, description="The suggested display name, not yet applied.")
    appearance: HeinzelAppearance = Field(description="The suggested appearance, pending an explicit user save.")
    status: Literal["proposed"] = Field(default="proposed", description="This suggestion has not been applied.")
    preview_sha256: Digest = Field(description="The digest binding the exact suggested profile for review.")


class WakeScheduleView(BaseModel):
    """An authoritative schedule preview or active contract; the browser never runs its own wake timer."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    summary: str = Field(
        description="The authoritative responsibility and schedule summary.", min_length=1, max_length=2000
    )
    cron: str = Field(description="The approved or proposed cron expression.", min_length=1, max_length=128)
    timezone: str = Field(
        description="The IANA time zone used for schedule interpretation.", min_length=1, max_length=128
    )
    status: Literal["proposed", "active", "paused"] = Field(
        description="Whether this schedule is proposed, active or paused."
    )
    contract_version: int = Field(description="The exact contract version behind this schedule.", ge=1)
    revision: int = Field(description="The immutable control or proposal revision.", ge=1)
    preview_hash: Digest = Field(description="The exact preview hash for activation or control review.")
    next_wake: datetime | None = Field(default=None, description="The effective next eligible wake, when known.")
    last_run: datetime | None = Field(default=None, description="The last recorded scheduled run, when one exists.")
    blocked_reason: str | None = Field(
        description="Why the daemon or effective authority currently prevents a wake.", default=None, max_length=4000
    )
    notification_status: str | None = Field(
        description="The recorded notification policy or delivery summary.", default=None, max_length=4000
    )


class SessionSnapshot(BaseModel):
    """Current metadata plus one message page; a fresh Heinzel can chat before it has an approved contract."""

    session_id: SessionId = Field(description="The stable hz-owned conversation identity.")
    kind: Literal["heinzel"] = Field(default="heinzel", description="The durable conversation kind.")
    heinzel_id: SessionId = Field(description="The same stable identity as session_id.")
    name: str = Field(description="The persisted personal display name.")
    appearance: HeinzelAppearance = Field(
        description="The persisted personal character overrides.", default_factory=HeinzelAppearance
    )
    profile_revision: int = Field(
        description="The exact name and appearance revision for optimistic updates.", default=1, ge=1
    )
    contract_version: int | None = Field(
        description="The currently approved contract version, if any.", default=None, ge=1
    )
    state: Literal["proposed", "asleep", "awake", "waiting", "paused", "retired"] = Field(
        default="proposed", description="The authoritative projected lifecycle state."
    )
    last_seq: int = Field(description="The latest acknowledged control-journal sequence.", default=0, ge=0)
    messages: list[SessionMessage] = Field(
        description="One ordered page of visible messages.", default_factory=list[SessionMessage]
    )
    pending_actions: list[ActionProposal] = Field(
        description="Stored actions awaiting a decision or reconciliation.", default_factory=list[ActionProposal]
    )
    questions: list[Question] = Field(
        description="Stored questions and their exact answer revisions.", default_factory=list[Question]
    )
    contracts: list[ContractVersion] = Field(
        description="The versioned contract history visible to this owner.", default_factory=list[ContractVersion]
    )
    backend_binding: BackendBinding | None = Field(
        default=None, description="The effective backend choice for the next safe turn boundary."
    )
    costs: SessionCosts = Field(description="The shared usage accounting and current policy limits.")
    attachments: list[AttachmentRef] = Field(
        description="Retained uploads owned by this conversation.", default_factory=list[AttachmentRef]
    )
    schedule: WakeScheduleView | None = Field(
        default=None, description="The authoritative schedule and effective eligibility, when available."
    )
    next_cursor: str | None = Field(
        description="Opaque cursor for the next message page; absent at the end.", default=None, max_length=1024
    )
    next_after_seq: int | None = Field(
        description="The last returned control sequence when another page remains.", default=None, ge=0
    )
    has_more: bool = Field(default=False, description="Whether another message page remains.")

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
    """Picker metadata, including proposed chats with no approved contract."""

    session_id: SessionId = Field(description="The stable personal conversation identity.")
    name: str = Field(description="The persisted personal display name.")
    appearance: HeinzelAppearance = Field(
        description="The persisted character overrides for the session picker.", default_factory=HeinzelAppearance
    )
    state: Literal["proposed", "asleep", "awake", "waiting", "paused", "retired"] = Field(
        description="The authoritative projected lifecycle state."
    )
    last_seq: int = Field(description="The latest acknowledged control-journal sequence.", ge=0)
    updated_at: datetime = Field(description="When the latest acknowledged control was recorded.")
    contract_version: int | None = Field(
        description="The approved contract version, when one exists.", default=None, ge=1
    )


class SessionList(BaseModel):
    """The personal conversations visible to this installation's current identity."""

    sessions: list[SessionSummary] = Field(
        description="Personal conversations visible to the authenticated owner, newest first.",
        default_factory=list[SessionSummary],
    )


class MessageRequest(BaseModel):
    """An idempotent inbox request, durably accepted before any provider is called."""

    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: RequestId = Field(description="The client id retained across retries of this exact request.")
    text: str = Field(description="The user message text.", default="", max_length=32000)
    when_busy: Literal["follow_up", "steer"] = Field(
        default="follow_up", description="Queue a follow-up or request steering at the next safe turn boundary."
    )
    attachment_ids: list[str] = Field(
        description="Retained upload ids owned by this conversation.", default_factory=list
    )

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

    request_id: RequestId = Field(description="The exact accepted client request id.")
    session_id: SessionId = Field(description="The stable conversation that owns the inbox entry.")
    disposition: Literal["started", "queued", "steered"] = Field(
        description="Acceptance or dispatch state; queued does not mean a completed answer."
    )
    turn_id: str | None = Field(default=None, description="The assigned turn id, when work has started.")
    inbox_position: int = Field(description="The committed control-journal position of the accepted message.", ge=1)


class AnswerRequest(BaseModel):
    """An idempotent answer to the exact question version the person saw."""

    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: RequestId = Field(description="The client id retained across retries of this exact answer.")
    question_id: RequestId = Field(description="The stored question being answered.")
    question_revision: int = Field(description="The exact displayed question revision.", ge=1)
    answer: JsonValue = Field(description="The validated answer value; it grants no additional authority.")
