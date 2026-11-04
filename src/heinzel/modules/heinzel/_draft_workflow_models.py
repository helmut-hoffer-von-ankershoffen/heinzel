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

    request_id: RequestId
    name: str = Field(min_length=1, max_length=100)
    prompt: str = Field(min_length=1, max_length=32000)
    feed_reference: str = Field(min_length=1, max_length=2048)
    document_reference: str = Field(min_length=1, max_length=2048)
    section: str = Field(min_length=1, max_length=256)
    publication_channel: str = Field(min_length=1, max_length=64)
    notice_channel: str = Field(min_length=1, max_length=64)
    cadence_seconds: Literal[300] = 300
    max_draft_bytes: int = Field(default=8000, ge=256, le=32768)
    max_requests: int = Field(default=4, ge=1, le=4)
    timeout_seconds: int = Field(default=60, ge=1, le=120)
    max_drafts_per_day: int | None = Field(default=None, ge=1, le=288)
    expires_at: AwareDatetime | None = None

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

    owner: str = Field(pattern=r"^u-[0-9a-f]{32}$")
    session_id: SessionId
    request: DraftPublicationRequest
    generation: int = Field(ge=1)
    feed_binding: Digest
    feed_baseline: ReleaseFeedBaseline
    append_binding: Digest
    document: DraftAppendTarget
    channel_binding: Digest
    channel_target: ChannelTarget
    notice_binding: Digest
    notice_target: OwnerNoticeTarget
    model_binding: Digest
    operation: Literal["append-marked-draft"] = "append-marked-draft"
    eligibility: Literal["new-provider-identities-after-explicit-baseline"] = (
        "new-provider-identities-after-explicit-baseline"
    )


class DraftRelease(_Value):
    """A host-derived immutable observed release, retained independently of display/body retention."""

    key: Digest
    provider_identity: str = Field(min_length=1, max_length=256)
    provider_revision: str = Field(min_length=1, max_length=256)
    release_sha256: Digest
    observed_at: AwareDatetime
    text: SecretStr


class DraftShareReview(_Value):
    """The edited historical snapshot and exact mutable-document link the human is asked to share."""

    owner: str = Field(pattern=r"^u-[0-9a-f]{32}$")
    session_id: SessionId
    publication_request: RequestId
    generation: int = Field(ge=1)
    grant_sha256: Digest
    draft_key: Digest
    snapshot: DraftSectionSnapshot
    content_sha256: Digest
    channel: str = Field(min_length=1, max_length=64)
    channel_binding: Digest
    channel_target: ChannelTarget
    link_text: str = Field(min_length=1, max_length=500)


class DraftReleaseStatus(_Value):
    """Honest source/effect observations; queued local notices are not confirmed native delivery."""

    key: Digest
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
    ]
    review_notice: Literal["pending", "attempted", "queued", "delivered", "refused", "uncertain"] = "pending"
    confirmation_notice: Literal["pending", "attempted", "queued", "delivered", "refused", "uncertain"] = "pending"
    revision: str | None = None


class DraftWorkflowStatus(_Value):
    """Owner-private current generation and bounded release dispositions."""

    session_id: SessionId
    request_id: RequestId
    generation: int = Field(ge=1)
    state: Literal["active", "paused", "revoked", "held", "uncertain"]
    releases: list[DraftReleaseStatus] = Field(max_length=100)
    hold_reason: str | None = Field(default=None, max_length=200)
