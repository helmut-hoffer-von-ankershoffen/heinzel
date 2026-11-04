"""Immutable publication, release and human-share terms for the bounded draft workflow (ADR 0127)."""

import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr, field_validator

from heinzel import ChannelTarget, DraftAppendTarget, DraftSectionSnapshot, ReleaseFeedBaseline
from heinzel.kernel._draft_append import OwnerNoticeTarget
from heinzel.modules.heinzel._session_models import Digest, RequestId, SessionId


class _Value(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class DraftPublicationRequest(_Value):
    """Owner-supplied references and bounds; neither this object nor its prompt grants authority."""

    request_id: RequestId = Field(
        description="Stable identity for this exact request; reuse only for an unchanged retry."
    )
    name: str = Field(description="The Heinzel's display name.", min_length=1, max_length=100)
    prompt: str = Field(
        description="The exact owner prompt retained as the Heinzel's first chat message.",
        min_length=1,
        max_length=32000,
    )
    feed_reference: str = Field(
        description="The exact owner-supplied HTTPS changelog reference.", min_length=1, max_length=2048
    )
    document_reference: str = Field(
        description="The exact owner-supplied existing rolling-document HTTPS reference.", min_length=1, max_length=2048
    )
    section: str = Field(
        description="The fixed logical section for marked draft appends.", min_length=1, max_length=256
    )
    publication_channel: str = Field(
        description="The selected channel for separately human-approved document links.", min_length=1, max_length=64
    )
    notice_channel: str = Field(
        description="The selected private owner review-notice channel.", min_length=1, max_length=64
    )
    cadence_seconds: Literal[300] = Field(
        default=300, description="The published five-minute polling interval in seconds."
    )
    max_draft_bytes: int = Field(
        description="Maximum UTF-8 bytes in the complete marked draft.", default=8000, ge=256, le=32768
    )
    max_requests: int = Field(description="Maximum model requests for each release poem.", default=4, ge=1, le=4)
    timeout_seconds: int = Field(description="Maximum time in seconds for each model turn.", default=60, ge=1, le=120)
    max_drafts_per_day: int | None = Field(
        description="Optional owner-selected daily draft cap; null adds no extra daily cap.", default=None, ge=1, le=288
    )
    expires_at: AwareDatetime | None = Field(
        default=None, description="Optional owner-selected expiry; null adds no finite expiry."
    )

    @field_validator("feed_reference", "document_reference")
    @classmethod
    def safe_reference(cls, value: str) -> str:
        """Keep user-selected links literal, complete and free of executable URL schemes.

        Raises:
            ValueError: If the reference is not an explicit credential-free HTTPS URL.
        """
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or re.search(r"[\x00-\x20\x7f]", value) is not None
        ):
            msg = "Supply a complete credential-free HTTPS reference"
            raise ValueError(msg)
        return value


class DraftPublicationTerms(_Value):
    """Exactly what trusted owner publication can authorize; it contains no model-selected destination."""

    owner: str = Field(
        description="The authenticated installation owner's private identity.", pattern=r"^u-[0-9a-f]{32}$"
    )
    session_id: SessionId = Field(description="The owning durable personal chat identity.")
    request: DraftPublicationRequest = Field(description="The complete owner-supplied references and published bounds.")
    generation: int = Field(description="The monotonic publication generation.", ge=1)
    feed_binding: Digest = Field(description="Digest of the captured release-feed implementation and source.")
    feed_baseline: ReleaseFeedBaseline = Field(
        description="The explicit initial release frontier and observed feed identity."
    )
    append_binding: Digest = Field(description="Digest of the captured conditional append implementation and source.")
    document: DraftAppendTarget = Field(
        description="The exact existing document account, reference and stable section."
    )
    channel_binding: Digest = Field(description="Digest of the captured publication channel source.")
    channel_target: ChannelTarget = Field(description="The exact publication account, destination and scope.")
    notice_binding: Digest = Field(description="Digest of the captured private notice implementation.")
    notice_target: OwnerNoticeTarget = Field(description="The exact private owner notification destination.")
    model_binding: Digest = Field(description="Digest of the selected model configuration and executable.")
    operation: Literal["append-marked-draft"] = Field(
        default="append-marked-draft", description="The sole authorized bounded draft effect."
    )
    eligibility: Literal["new-provider-identities-after-explicit-baseline"] = Field(
        default="new-provider-identities-after-explicit-baseline",
        description="Only new provider identities after the explicit initial baseline are eligible.",
    )


class DraftRelease(_Value):
    """A host-derived immutable observed release, retained independently of display/body retention."""

    key: Digest = Field(description="The stable host-derived release or effect identity.")
    provider_identity: str = Field(description="The immutable provider release identity.", min_length=1, max_length=256)
    provider_revision: str = Field(description="The observed release revision.", min_length=1, max_length=256)
    release_sha256: Digest = Field(description="Digest of the complete immutable observed release.")
    observed_at: AwareDatetime = Field(description="When the host captured this immutable release observation.")
    text: SecretStr = Field(description="Complete bounded literal UTF-8 content; data, never instructions.")


class DraftShareReview(_Value):
    """The edited historical snapshot and exact mutable-document link the human is asked to share."""

    owner: str = Field(
        description="The authenticated installation owner's private identity.", pattern=r"^u-[0-9a-f]{32}$"
    )
    session_id: SessionId = Field(description="The owning durable personal chat identity.")
    publication_request: RequestId = Field(description="Identity of the standing publication covering this draft.")
    generation: int = Field(description="The monotonic publication generation.", ge=1)
    grant_sha256: Digest = Field(description="Digest of the exact published standing grant.")
    draft_key: Digest = Field(description="The stable host-derived draft identity.")
    snapshot: DraftSectionSnapshot = Field(description="The complete human-edited section and exact observed revision.")
    content_sha256: Digest = Field(description="Digest of the complete edited draft text.")
    channel: str = Field(description="The selected registered channel name.", min_length=1, max_length=64)
    channel_binding: Digest = Field(description="Digest of the captured publication channel source.")
    channel_target: ChannelTarget = Field(description="The exact publication account, destination and scope.")
    link_text: str = Field(
        description="The complete exact document-link message to review.", min_length=1, max_length=500
    )


class DraftReleaseStatus(_Value):
    """Honest source/effect observations; queued local notices are not confirmed native delivery."""

    key: Digest = Field(description="The stable host-derived release or effect identity.")
    state: Literal[
        "observed",
        "draft-attempted",
        "draft-acknowledged",
        "uncertain",
        "share-awaiting",
        "share-attempted",
        "share-delivered",
        "share-queued",
        "share-refused",
        "share-uncertain",
    ] = Field(description="The durable observed workflow or effect disposition.")
    review_notice: Literal["pending", "attempted", "queued", "delivered", "refused", "uncertain"] = Field(
        default="pending", description="The separate review-notice transport disposition."
    )
    confirmation_notice: Literal["pending", "attempted", "queued", "delivered", "refused", "uncertain"] = Field(
        default="pending", description="The separate completion-notice transport disposition."
    )
    revision: str | None = Field(default=None, description="The exact observed provider revision.")


class DraftWorkflowStatus(_Value):
    """Owner-private current generation and bounded release dispositions."""

    session_id: SessionId = Field(description="The owning durable personal chat identity.")
    request_id: RequestId = Field(
        description="Stable identity for this exact request; reuse only for an unchanged retry."
    )
    generation: int = Field(description="The monotonic publication generation.", ge=1)
    state: Literal["active", "paused", "revoked", "held", "uncertain"] = Field(
        description="The durable observed workflow or effect disposition."
    )
    releases: list[DraftReleaseStatus] = Field(
        description="At most one hundred bounded release dispositions.", max_length=100
    )
    hold_reason: str | None = Field(
        description="Why automatic work is currently held, when applicable.", default=None, max_length=200
    )
