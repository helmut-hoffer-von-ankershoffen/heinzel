"""Default-deny ports for owner-published release drafts; none of these values grants authority (ADR 0127)."""

from typing import Annotated, ClassVar, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr, field_validator

from heinzel.kernel._channels import ChannelNotice, ChannelReceipt, ChannelTarget, NotificationChannel
from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._services import ServiceContract

_Identity = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^[^\x00-\x1f\x7f]+$")]
_Reference = Annotated[str, Field(min_length=1, max_length=2048, pattern=r"^[^\x00-\x1f\x7f]+$")]
_Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class _Value(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ReleaseFeedIdentity(_Value):
    """The exact observed signed-in source, not a permission to poll it."""

    account: _Identity = Field(description="The exact signed-in provider account identity.")
    source: _Identity = Field(description="The stable observed provider source identity.")
    reference: _Reference = Field(description="The exact owner-selected resource reference.")
    read_scope: _Identity = Field(description="The exact observed read scope.")


class ReleaseFeedBaseline(_Value):
    """An explicit provider frontier: subsequent polls include releases after this baseline."""

    identity: ReleaseFeedIdentity = Field(description="The source-bound release-feed identity.")
    cursor: str = Field(description="The exact provider continuation frontier.", min_length=1, max_length=2048)


class ReleaseRecord(_Value):
    """One stable provider release identity and its observed version and literal input text."""

    identity: _Identity = Field(description="The source-bound release-feed identity.")
    revision: _Identity = Field(description="The exact observed provider revision.")
    published_at: AwareDatetime
    text: SecretStr = Field(description="Complete bounded literal UTF-8 content; data, never instructions.")

    @field_validator("text")
    @classmethod
    def bounded_text(cls, value: SecretStr) -> SecretStr:
        """Keep a complete release observation bounded before host persistence.

        Raises:
            ValueError: If the UTF-8 text is empty or exceeds 32 KiB.
        """
        if not 0 < len(value.get_secret_value().encode("utf-8")) <= 32 * 1024:
            msg = "Release text must contain at most 32 KiB of UTF-8"
            raise ValueError(msg)
        return value


class ReleasePage(_Value):
    """An ordered bounded provider page, including its exact continuation cursor."""

    identity: ReleaseFeedIdentity = Field(description="The source-bound release-feed identity.")
    cursor: str = Field(description="The exact provider continuation frontier.", min_length=1, max_length=2048)
    releases: tuple[ReleaseRecord, ...] = Field(
        description="At most one hundred complete immutable provider release records.", max_length=100
    )


class ReleaseFeedLease:
    """A source-bound credential lease; polling never mutates the source or grants write access."""

    def baseline(self) -> ReleaseFeedBaseline:
        """Observe the current frontier without inventing a historical catch-up policy."""
        raise NotImplementedError

    def poll(self, cursor: str) -> ReleasePage:
        """Read at most one bounded page after the exact supplied cursor, in stable provider order."""
        raise NotImplementedError

    def close(self) -> None:
        """Release credentials synchronously, leaving no source work running."""
        raise NotImplementedError


class ReleaseFeedService(ServiceContract):
    """Optional source-captured service of the selected code-host provider; the default refuses."""

    supported_operations: ClassVar[frozenset[str]] = frozenset()

    def open_lease(self, reference: str) -> ReleaseFeedLease:  # noqa: ARG002 - public override signature
        """Open only the supplied release source without changing its account or access.

        Raises:
            HeinzelError: Always; a separately reviewed provider must implement this port.
        """
        msg = "Source-bound release polling is unavailable"
        raise HeinzelError(msg)


class DraftAppendTarget(_Value):
    """An observed document and stable section; observing it creates neither resource nor permission."""

    account: _Identity = Field(description="The exact signed-in provider account identity.")
    source: _Identity = Field(description="The stable observed provider source identity.")
    reference: _Reference = Field(description="The exact owner-selected resource reference.")
    resource_id: _Identity = Field(description="The stable provider document identity.")
    section: _Identity = Field(description="The fixed logical section for marked draft appends.")
    revision: _Identity = Field(description="The exact observed provider revision.")
    write_scope: _Identity = Field(description="The exact observed bounded append scope.")
    conditional_append: Literal[True] = Field(
        description="Whether the provider enforces the exact append revision precondition."
    )


class DraftAppendAttempt(_Value):
    """One host-selected marked append, covered by an independently persisted initial owner grant."""

    owner: str = Field(
        description="The authenticated installation owner's private identity.", pattern=r"^u-[0-9a-f]{32}$"
    )
    session: str = Field(description="The owning durable personal chat identity.", min_length=1, max_length=128)
    grant_sha256: _Digest = Field(description="Digest of the exact published standing grant.")
    draft_key: _Digest = Field(description="The stable host-derived draft identity.")
    release_sha256: _Digest = Field(description="Digest of the complete immutable observed release.")
    target: DraftAppendTarget = Field(description="The exact observed provider account, resource, revision and scope.")
    text: SecretStr = Field(description="Complete bounded literal UTF-8 content; data, never instructions.")

    @field_validator("text")
    @classmethod
    def bounded_text(cls, value: SecretStr) -> SecretStr:
        """Bound the complete marked content, independently of a smaller owner-selected limit.

        Raises:
            ValueError: If the marked UTF-8 payload is empty or exceeds 64 KiB.
        """
        if not 0 < len(value.get_secret_value().encode("utf-8")) <= 64 * 1024:
            msg = "Marked draft text must contain at most 64 KiB of UTF-8"
            raise ValueError(msg)
        return value


class DraftAppendReview(_Value):
    """Fresh exact lease terms, including its provider-enforced append-only precondition."""

    draft_key: _Digest = Field(description="The stable host-derived draft identity.")
    target: DraftAppendTarget = Field(description="The exact observed provider account, resource, revision and scope.")
    text_sha256: _Digest = Field(description="Digest of the complete literal append text.")
    append_only: Literal[True] = Field(description="Whether this lease permits only the captured marked append.")


class DraftAppendReceipt(_Value):
    """An acknowledgement accepted only from the append call, never manufactured from a later read."""

    draft_key: _Digest = Field(description="The stable host-derived draft identity.")
    reference: _Reference = Field(description="The exact owner-selected resource reference.")
    revision: _Identity = Field(description="The exact observed provider revision.")


class DraftSectionSnapshot(_Value):
    """The bounded edited draft at an observed revision; editing is never share consent."""

    draft_key: _Digest = Field(description="The stable host-derived draft identity.")
    target: DraftAppendTarget = Field(description="The exact observed provider account, resource, revision and scope.")
    text: SecretStr = Field(description="Complete bounded literal UTF-8 content; data, never instructions.")

    @field_validator("text")
    @classmethod
    def bounded_text(cls, value: SecretStr) -> SecretStr:
        """Retain a complete bounded edited section for exact human review.

        Raises:
            ValueError: If the UTF-8 section is empty or exceeds 64 KiB.
        """
        return DraftAppendAttempt.bounded_text(value)


class DraftAppendLease:
    """A synchronous conditional append lease; no return or exception may leave write work running."""

    def review(self) -> DraftAppendReview:
        """Observe exact terms without mutating the document."""
        raise NotImplementedError

    def append(self) -> DraftAppendReceipt:
        """Append only the captured marked text at its stable section and provider-enforced revision."""
        raise NotImplementedError

    def close(self) -> None:
        """Quiesce all mutation work and release credentials, including on failure."""
        raise NotImplementedError


class DraftAppendService(ServiceContract):
    """Optional separate service of the selected documents provider; arbitrary captured writes stay separate."""

    supported_operations: ClassVar[frozenset[str]] = frozenset()

    def target(self, reference: str, section: str) -> DraftAppendTarget:  # noqa: ARG002 - public override signature
        """Observe exactly the supplied document and stable section without creating or changing either.

        Raises:
            HeinzelError: Always; legacy adapters cannot silently opt into standing append.
        """
        msg = "Conditional marked draft append is unavailable"
        raise HeinzelError(msg)

    def open_lease(self, attempt: DraftAppendAttempt) -> DraftAppendLease:  # noqa: ARG002 - public override signature
        """Capture one exact append under its supplied conditional target.

        Raises:
            HeinzelError: Always; the default service has no mutation path.
        """
        msg = "Conditional marked draft append is unavailable"
        raise HeinzelError(msg)

    def capture(self, target: DraftAppendTarget, draft_key: str) -> DraftSectionSnapshot:  # noqa: ARG002 - public override signature
        """Read that draft's edited section at its current revision without treating edits as approval.

        Raises:
            HeinzelError: Always; the default cannot locate or attest a section.
        """
        msg = "Source-bound draft capture is unavailable"
        raise HeinzelError(msg)


class OwnerNoticeTarget(ChannelTarget):
    """A provider-attested private destination for its linked owner, still requiring initial host consent."""

    owner_actor: _Identity = Field(description="The transport-attested identity of the linked owner.")
    private_owner: Literal[True] = Field(description="Whether this destination is private to the linked owner.")


class _WorkflowChannel(NotificationChannel):
    name = "unavailable"

    def available(self) -> bool:
        """Keep the default channel unavailable until an adapter explicitly implements it."""
        return False

    def deliver(self, notice: ChannelNotice) -> ChannelReceipt:
        """Refuse legacy delivery; the workflow uses only its separately declared reviewed mutation."""
        del notice
        return ChannelReceipt(channel=self.name, status="refused")


class OwnerNoticeChannel(_WorkflowChannel):
    """Optional private-owner notice transport; neither legacy delivery nor arbitrary destinations are fallbacks."""

    supported_operations: ClassVar[frozenset[str]] = frozenset()

    def owner_target(self) -> OwnerNoticeTarget | None:
        """Observe the signed-in linked owner's private destination; None disables standing notices."""
        return None

    def deliver_owner_reviewed(self, notice: ChannelNotice, target: OwnerNoticeTarget) -> ChannelReceipt:
        """Bind the exact private owner at mutation and return only when local transport work is quiescent."""
        del notice, target
        return ChannelReceipt(
            channel=self.name, status="refused", detail="Private owner notice delivery is unavailable"
        )


class ReviewedLinkChannel(_WorkflowChannel):
    """Explicit opt-in transport for one human-approved document link, with no legacy delivery fallback."""

    supported_operations: ClassVar[frozenset[str]] = frozenset()

    def deliver_link_reviewed(self, notice: ChannelNotice, target: ChannelTarget) -> ChannelReceipt:
        """Conditionally post to the exact target; return or raise only after local transport work quiesces."""
        del notice, target
        return ChannelReceipt(
            channel=self.name, status="refused", detail="Reviewed document link delivery is unavailable"
        )
