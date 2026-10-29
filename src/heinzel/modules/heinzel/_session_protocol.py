"""The narrow persistence seam shared by personal chat and contract execution."""

from contextlib import AbstractContextManager
from typing import Protocol

from pydantic import JsonValue

from heinzel import SessionHead, SessionRead, SessionWriter


class SessionRepository(Protocol):
    """Every method enforces the current authenticated principal's ownership before reading bodies or writing.

    Main and control journals have separate private roots. A control method never waits for a main writer; active
    turns acquire main first and then the short control writer. Body ids are opaque ids, never host paths.
    """

    @property
    def principal_id(self) -> str:
        """The current trusted principal; absence of a trusted context is an error, never an owner fallback."""
        ...

    def heads(self) -> list[SessionHead]:
        """Owned agents only; creation catalogs and control journals do not appear as extra agents."""
        ...

    def budget_read(self, session_id: str) -> SessionRead:
        """Read the authenticated owner's shared budget ledger after checking session ownership."""
        ...

    def budget_writer(self, session_id: str) -> AbstractContextManager[SessionWriter]:
        """Serialize this owner's reservations after checking session ownership."""
        ...

    def main_read(self, session_id: str) -> SessionRead:
        """Verified turn/checkpoint/effect metadata, refusing a damaged authoritative spine."""
        ...

    def control_read(self, session_id: str) -> SessionRead:
        """Verified accepted messages and acknowledged controls, without waiting for an active turn."""
        ...

    def main_writer(self, session_id: str, *, wait: bool = False) -> AbstractContextManager[SessionWriter]:
        """Own the active turn; wait is reserved for short creation repair, before taking the control writer."""
        ...

    def control_writer(self, session_id: str) -> AbstractContextManager[SessionWriter]:
        """Own only the short control append; never acquire or wait for the main writer here."""
        ...

    def body(self, session_id: str, body_id: str, *, expected_hash: str, control: bool = False) -> list[JsonValue]:
        """Read an immutable checkpoint body through the public hash-verified reader, without a turn lock."""
        ...

    def reserve(self, request_id: str, payload_hash: str) -> str:
        """Reserve one stable id per canonical creation request; release the catalog before initialization."""
        ...
