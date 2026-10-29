"""Owner-scoped irreversible request slots for an internal subscription model bridge.

A slot represents an attempted external request, not a completed response. It is never released or replayed.
This journal does not establish model isolation, subscription billing, or authority to start a turn.
"""

import hashlib
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from heinzel import HeinzelError
from heinzel.modules.heinzel._session_protocol import SessionRepository

_MAX_SLOTS = 100


class _Slot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    owner: str
    session: str
    turn: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: int = Field(ge=1)
    limit: int = Field(ge=1, le=100)
    ordinal: int = Field(ge=1, le=100)


class SubscriptionSlots:
    """Durable slots; the caller must already hold a trusted owner-bound turn authorization."""

    def __init__(self, repository: SessionRepository) -> None:
        self._repository = repository

    @property
    def repository(self) -> SessionRepository:
        """The owner-bound repository used for slot reservations."""
        return self._repository

    def reserve(self, owner: str, session: str, turn: str, revision: int, limit: int, ordinal: int) -> None:  # noqa: PLR0913, PLR0917 - exact host binding
        """Reserve one unique slot before launch; all existing or uncertain slots refuse replay.

        Raises:
            HeinzelError: If the slot or owner binding cannot be verified.
        """
        denied = "The subscription request slot cannot be reserved"
        if (
            owner != self._repository.principal_id
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", turn)
            or type(revision) is not int
            or revision < 1
            or type(limit) is not int
            or not 1 <= limit <= _MAX_SLOTS
            or type(ordinal) is not int
            or not 1 <= ordinal <= limit
        ):
            raise HeinzelError(denied)
        turn_hash = hashlib.sha256(turn.encode()).hexdigest()
        requested = _Slot(owner=owner, session=session, turn=turn_hash, revision=revision, limit=limit, ordinal=ordinal)
        key = hashlib.sha256(f"{session}\0{turn_hash}\0{ordinal}".encode()).hexdigest()
        with self._repository.subscription_writer(session) as writer:
            records = []
            seen = set()
            try:
                for event in self._repository.subscription_read(session).events:
                    if event.kind != "subscription.slot":
                        raise HeinzelError(denied)
                    record = _Slot.model_validate(event.data)
                    expected = hashlib.sha256(f"{record.session}\0{record.turn}\0{record.ordinal}".encode()).hexdigest()
                    if (
                        record.owner != owner
                        or record.ordinal > record.limit
                        or event.key != expected
                        or expected in seen
                    ):
                        raise HeinzelError(denied)
                    seen.add(expected)
                    records.append(record)
            except ValidationError:
                raise HeinzelError(denied) from None
            same = [r for r in records if r.session == session and r.turn == turn_hash]
            if key in seen or any(r.revision != revision or r.limit != limit for r in same) or ordinal != len(same) + 1:
                raise HeinzelError(denied)
            writer.append("subscription.slot", key=key, data=requested.model_dump(mode="json"))
