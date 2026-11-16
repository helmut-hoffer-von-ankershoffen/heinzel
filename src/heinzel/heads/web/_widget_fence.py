"""Pure state transitions for recoverable, paired-device widget rotation.

The caller must authenticate the exact pairing incarnation under its storage lock
and persist the returned state atomically before returning a receipt. No raw bearer
is accepted or stored here. This module is not yet connected to any endpoint.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[0-9a-f]{32}\Z")


class FenceConflictError(ValueError):
    """The attempt is invalid, stale, or conflicts with an existing receipt."""

    def __init__(self) -> None:
        """Do not expose request details in an error."""
        super().__init__("Widget operation conflict")


@dataclass(frozen=True)
class Request:
    """An operation and its expected pairing and revision."""

    epoch: str
    revision: str
    attempt: str
    operation: str
    commitment: str | None


@dataclass(frozen=True)
class Receipt:
    """The most recent operation, sufficient to replay its exact result."""

    attempt: str
    operation: str
    previous_revision: str
    commitment: str | None
    revision: str
    expires: datetime | None


@dataclass(frozen=True)
class FenceState:
    """State is per pairing incarnation; revisions are never reset on expiry."""

    epoch: str
    revision: str
    child_hash: str | None = None
    child_expires: datetime | None = None
    receipt: Receipt | None = None

    @classmethod
    def new(cls) -> FenceState:
        """Create a fresh pairing epoch and initial revision."""
        return cls(epoch=secrets.token_hex(16), revision=secrets.token_hex(16))


def transition(state: FenceState, request: Request, now: datetime) -> tuple[FenceState, Receipt]:
    """Return the next state and receipt, or replay only an identical operation.

    Raises:
        FenceConflictError: The operation is malformed, stale or conflicts with a receipt.
    """
    if not _ID.fullmatch(request.attempt) or request.epoch != state.epoch or now.utcoffset() is None:
        raise FenceConflictError
    if request.operation == "issue":
        if request.commitment is None or not _HEX.fullmatch(request.commitment):
            raise FenceConflictError
    elif request.operation != "revoke" or request.commitment is not None:
        raise FenceConflictError
    previous = state.receipt
    if previous and previous.attempt == request.attempt:
        if (previous.operation, previous.previous_revision, previous.commitment) != (
            request.operation,
            request.revision,
            request.commitment,
        ):
            raise FenceConflictError
        return state, previous
    if request.revision != state.revision:
        raise FenceConflictError
    new_revision = secrets.token_hex(16)
    expires = now + timedelta(hours=24) if request.operation == "issue" else None
    receipt = Receipt(request.attempt, request.operation, request.revision, request.commitment, new_revision, expires)
    return FenceState(state.epoch, new_revision, request.commitment, expires, receipt), receipt
