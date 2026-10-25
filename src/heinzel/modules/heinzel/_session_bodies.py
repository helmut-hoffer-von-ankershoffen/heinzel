"""Immutable private body snapshots referenced by authoritative session metadata."""

import re
import secrets
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from heinzel import HeinzelError, SessionWriter


class BodyRef(BaseModel):
    """A bounded opaque reference with the expected hash of the complete private file."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    source: Literal["main", "control"]
    body_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    body_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def immutable_body(writer: SessionWriter, stem: str, value: JsonValue, *, control: bool = False) -> BodyRef:
    """Fsync a new snapshot without ever appending to a previously committed body's id.

    Raises:
        HeinzelError: If a generated id already exists or the stem is unsafe.
        OSError: If private storage cannot be read or written.
    """
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", stem) is None:
        msg = "A snapshot prefix must be one bounded opaque id"
        raise HeinzelError(msg)
    body_id = f"{stem}-{secrets.token_hex(12)}"
    try:
        writer.body(body_id)
    except FileNotFoundError:
        digest = writer.append_body(body_id, value)
    else:
        msg = "This immutable snapshot id already exists; retry with a new id"
        raise HeinzelError(msg)
    return BodyRef(source="control" if control else "main", body_id=body_id, body_hash=digest)
