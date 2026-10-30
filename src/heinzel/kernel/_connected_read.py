"""Public contracts for privately reviewed, owner-bound connected reads.

A plugin registers its own concrete service class, not this shared base, so several providers can coexist. These
contracts convey no user approval, provider-account verification or authority on their own.
"""

import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from heinzel.kernel._services import ServiceContract

_MAX_SCOPE_LENGTH = 512
_MAX_REVIEW_BYTES = 16_384


class ConnectedReadReview(BaseModel):
    """Private observed source/account and exact operation constraints for human review.

    The host must independently bind the selected registry record and operation schema, verify the owner/session,
    and compare these same fields on a fresh lease before invoking. Provider scopes are reported separately from
    host-granted arguments and may include previously granted rights that the host will not exercise.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    owner: str = Field(pattern=r"^u-[0-9a-f]{32}$", description="The authenticated owner identity.")
    session: str = Field(min_length=1, max_length=128, description="The owner-bound personal agent session.")
    operation: str = Field(min_length=1, max_length=256, description="The exact read capability and method.")
    schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$", description="Digest of the reviewed capability schema.")
    source_revision: str = Field(
        min_length=1, max_length=256, description="The provider-reported source continuity revision."
    )
    account_identity: str = Field(
        min_length=1, max_length=256, description="Provider-reported identity of the selected account."
    )
    account_display: str | None = Field(
        default=None, max_length=256, description="Optional provider-reported display name or address."
    )
    provider_scopes: tuple[str, ...] = Field(
        max_length=128, description="Observed provider scopes, which may exceed the host read grant."
    )
    fixed_arguments: dict[str, JsonValue] = Field(max_length=32, description="Exact host-constrained read arguments.")

    @model_validator(mode="after")
    def bounded(self) -> "ConnectedReadReview":
        """Keep private review data bounded and reject ambiguous provider scope lists.

        Raises:
            ValueError: If the review is oversized or has invalid scope identifiers.
        """
        if (
            len(set(self.provider_scopes)) != len(self.provider_scopes)
            or any(
                not scope or len(scope) > _MAX_SCOPE_LENGTH or not scope.isascii() or not scope.isprintable()
                for scope in self.provider_scopes
            )
            or len(json.dumps(self.model_dump(mode="json"), ensure_ascii=True).encode()) > _MAX_REVIEW_BYTES
        ):
            msg = "The connected read review is invalid or too large"
            raise ValueError(msg)
        return self


class ConnectedReadMethod(BaseModel):
    """One provider-declared captured read; this is neither an access grant nor a schema assertion."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    capability: str = Field(
        pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", max_length=64, description="Canonical capability name."
    )
    method: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64, description="Canonical public read-method name.")


class ConnectedReadLease:
    """A private short-lived connection: no serialized credentials, writes or automatic refresh."""

    def review(self) -> ConnectedReadReview:
        """Observe current identity and exact constraints; observation itself is not an approval."""
        raise NotImplementedError

    def read(self, arguments: Mapping[str, JsonValue]) -> Any:  # noqa: ANN401 - registered schemas define values
        """Read only the reviewed operation while enforcing the lease's current owner and constraints."""
        raise NotImplementedError

    def close(self) -> None:
        """Close the lease and release private resources, without claiming secure erasure."""
        raise NotImplementedError


class ConnectedReadService(ServiceContract):
    """Marker for a concrete installed provider; the host selects one by its actual kernel module record."""

    def supported_reads(self) -> tuple[ConnectedReadMethod, ...]:
        """Declare supported captured reads; an absent declaration authorizes and advertises nothing."""
        return ()

    def open_lease(self, session: str, operation: str, fixed_arguments: Mapping[str, JsonValue]) -> ConnectedReadLease:
        """Capture current selected credentials privately; the returned lease provides no authority by itself."""
        raise NotImplementedError
