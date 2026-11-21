"""Provider-independent session records and projections (ADR 0049).

The envelope carries metadata; model messages and tool results belong in turn bodies. A caller owns the vocabulary
of `kind` and the projection, so storage does not depend on any module or agent library.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator


class SessionEvent(BaseModel):
    """One durable checkpoint, linked to the exact bytes of the preceding line."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    v: Literal[1] = 1
    seq: Annotated[int, Field(strict=True, gt=0)]
    ts: datetime
    prev: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None
    turn: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    kind: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,127}$")]
    key: Annotated[str, Field(min_length=1, max_length=1024)] | None = None
    data: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("v", mode="before")
    @classmethod
    def supported_version(cls, value: object) -> int:
        """Do not let JSON's boolean `true` masquerade as envelope version 1.

        Raises:
            ValueError: If the version is not the integer 1.
        """
        if not isinstance(value, int) or isinstance(value, bool) or value != 1:
            msg = "Unsupported session envelope version"
            raise ValueError(msg)
        return value

    @field_validator("ts")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        """Require an aware timestamp and normalize it to UTC.

        Raises:
            ValueError: If the timestamp has no offset.
        """
        if value.tzinfo is None:
            msg = "A session event timestamp needs a UTC offset"
            raise ValueError(msg)
        return value.astimezone(UTC)


class SessionRead(BaseModel):
    """The verified prefix and a damage report; readers never silently repair a spine."""

    events: list[SessionEvent] = Field(default_factory=list[SessionEvent])
    hashes: list[str] = Field(default_factory=list)
    problem: str | None = None


class SessionHead(BaseModel):
    """A session's indexed position and caller-defined metadata, without reading its spine."""

    session: str
    seq: int
    hash: str | None
    updated: datetime | None
    projection: dict[str, JsonValue] = Field(default_factory=dict)
    problem: str | None = None


SessionProjector = Callable[[dict[str, JsonValue], SessionEvent], dict[str, JsonValue]]
"""A pure metadata reducer; callers skip kinds their vocabulary does not know."""
