"""Private captured-write contracts; a declaration is not authority to perform an effect."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._services import ServiceContract

_MAX_CONTENT_BYTES = 64 * 1024


class CapturedWriteDraft(BaseModel):
    """Exact immutable draft, including a provider-enforced mutation precondition."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    owner: str = Field(pattern=r"^u-[0-9a-f]{32}$")
    session: str = Field(min_length=1, max_length=128)
    request_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    provider: str = Field(min_length=1, max_length=256)
    binding_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    account: str = Field(min_length=1, max_length=256)
    source: str = Field(min_length=1, max_length=256)
    reference: str = Field(min_length=1, max_length=2048)
    operation: str = Field(min_length=1, max_length=128)
    section: str = Field(min_length=1, max_length=256)
    base_revision: str = Field(min_length=1, max_length=256)
    write_scope: str = Field(min_length=1, max_length=256)
    content: SecretStr

    @field_validator("content")
    @classmethod
    def bounded_content(cls, value: SecretStr) -> SecretStr:
        """Reject empty or oversized proposed content before persistence.

        Raises:
            ValueError: If the UTF-8 content does not fit the private draft bound.
        """
        raw = value.get_secret_value().encode("utf-8")
        if not raw or len(raw) > _MAX_CONTENT_BYTES:
            msg = "The proposed content is empty or too large"
            raise ValueError(msg)
        return value


class CapturedWriteReview(BaseModel):
    """Fresh adapter observation for exactly one host-selected mutation; never an approval."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    owner: str = Field(pattern=r"^u-[0-9a-f]{32}$")
    session: str = Field(min_length=1, max_length=128)
    provider: str = Field(min_length=1, max_length=256)
    binding_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    account: str = Field(min_length=1, max_length=256)
    source: str = Field(min_length=1, max_length=256)
    reference: str = Field(min_length=1, max_length=2048)
    operation: str = Field(min_length=1, max_length=128)
    section: str = Field(min_length=1, max_length=256)
    base_revision: str = Field(min_length=1, max_length=256)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    write_scope: str = Field(min_length=1, max_length=256)
    conditional_mutation: Literal[True]


class CapturedWriteAcknowledgement(BaseModel):
    """Provider reply to the exact conditional mutation, not an observation from a later read."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    reference: str = Field(min_length=1, max_length=2048)
    revision: str | None = Field(default=None, min_length=1, max_length=256)


class CapturedWriteTarget(BaseModel):
    """What a provider observed about a place to draft in; it reads no content and grants nothing."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    account: str = Field(min_length=1, max_length=256)
    source: str = Field(min_length=1, max_length=256)
    reference: str = Field(min_length=1, max_length=2048)
    base_revision: str = Field(min_length=1, max_length=256)
    write_scope: str = Field(min_length=1, max_length=256)


class CapturedWriteLease:
    """Synchronous lease; no method may return or raise while its write work can still mutate state.

    The host must select and independently review an adapter that meets this contract. This is not a sandbox.
    """

    def review(self) -> CapturedWriteReview:
        """Observe exact source, account, scope and precondition without performing the write."""
        raise NotImplementedError

    def write(self) -> CapturedWriteAcknowledgement:
        """Perform only the captured mutation using its provider-enforced conditional revision.

        The method must not return or raise until all local work capable of mutating the target is quiescent.
        """
        raise NotImplementedError

    def close(self) -> None:
        """Release the captured credential; even a failure must leave no write work running."""
        raise NotImplementedError


class CapturedWriteService(ServiceContract):
    """An explicitly selected provider; the default denies every write."""

    def target(self, _reference: str) -> CapturedWriteTarget:
        """Observe the account and current revision of `reference`, without writing.

        Raises:
            HeinzelError: If this provider has no explicitly implemented captured-write support.
        """
        msg = "This provider does not support reviewed conditional writes"
        raise HeinzelError(msg)

    def open_lease(self, _draft: CapturedWriteDraft) -> CapturedWriteLease:
        """Capture exact reviewed parameters without executing a provider write.

        Raises:
            HeinzelError: If this provider has no explicitly implemented captured-write support.
        """
        msg = "This provider does not support reviewed conditional writes"
        raise HeinzelError(msg)
