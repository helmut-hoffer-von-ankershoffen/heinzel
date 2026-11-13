"""The narrow persistence seam shared by personal chat and contract execution."""

from contextlib import AbstractContextManager
from datetime import datetime
from typing import Literal, Protocol

from pydantic import JsonValue

from heinzel import SessionHead, SessionRead, SessionWriter
from heinzel.kernel import Authentication


class SessionRepository(Protocol):
    """Every method enforces the current authenticated principal's ownership before reading bodies or writing.

    Main and control journals have separate private roots. A control method never waits for a main writer; active
    turns acquire main first and then the short control writer. Body ids are opaque ids, never host paths.
    """

    @property
    def principal_id(self) -> str:
        """The current trusted principal; absence of a trusted context is an error, never an owner fallback."""
        ...

    @property
    def search_storage_id(self) -> str:
        """Distinguish independent private roots in a process-local search cache."""
        ...

    @property
    def authentication(self) -> Authentication:
        """The verified current call's channel; never a request argument or approval claim."""
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

    def subscription_read(self, session_id: str) -> SessionRead:
        """Read owner-scoped subscription request slots."""
        ...

    def subscription_writer(self, session_id: str) -> AbstractContextManager[SessionWriter]:
        """Serialize the owner-scoped subscription request ledger."""
        ...

    def main_read(self, session_id: str) -> SessionRead:
        """Verified turn/checkpoint/effect metadata, refusing a damaged authoritative spine."""
        ...

    def search_catalog_page(
        self, *, position: tuple[int, str | None], anchor: tuple[int, str | None] | None, limit: int
    ) -> tuple[SessionRead, tuple[int, str | None]]:
        """Read one bounded authenticated owner catalog page."""
        ...

    def search_body_inventory(
        self, session_id: str, source: str, *, known: tuple[int, int, int, int] | None
    ) -> tuple[tuple[int, int, int, int] | None, frozenset[str] | None]:
        """Inspect retained body IDs under the current owner."""
        ...

    def control_indexed_page(
        self,
        session_id: str,
        *,
        position: tuple[int, str | None],
        anchor: tuple[int, str | None] | None,
        limit: int,
    ) -> tuple[SessionRead, tuple[int, str | None]]:
        """Read an owner-checked bounded derived event page."""
        ...

    def control_read(self, session_id: str) -> SessionRead:
        """Verified accepted messages and acknowledged controls, without waiting for an active turn."""
        ...

    def main_writer(self, session_id: str, *, wait: bool = False) -> AbstractContextManager[SessionWriter]:
        """Own the active turn; wait is reserved for short creation repair, before taking the control writer."""
        ...

    def is_main_writer(self, session_id: str, writer: SessionWriter) -> bool:
        """Verify an active writer is scoped to this principal's main journal."""
        ...

    def channel_writer(self, session_id: str) -> AbstractContextManager[SessionWriter]:
        """Serialize channel claim/recovery without nesting captured-write main/control locks."""
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

    def deletion_state(self, session_id: str) -> Literal["live", "deleting", "deleted"]:
        """Where one of this owner's reserved sessions stands in deletion for good (ADR 0148)."""
        ...

    def deleted_at(self, session_id: str) -> datetime | None:
        """The time of this owner's content-free deletion tombstone, if the session was deleted."""
        ...

    def pending_deletions(self) -> tuple[str, ...]:
        """Sessions whose deletion started and has not finished."""
        ...

    def mark_deleting(self, session_id: str, posts: list[dict[str, str]]) -> None:
        """Record the intent to delete, with content-free channel references; the caller holds the writers."""
        ...

    def deletion_posts(self, session_id: str) -> list[dict[str, str]]:
        """The channel references recorded with this session's deletion intent, not yet forgotten."""
        ...

    def mark_forgotten(self, session_id: str, channel: str, reference: str) -> None:
        """Record that a channel plugin forgot one posted request of a session being deleted."""
        ...

    def remove_session_store(self, namespace: str, session_id: str) -> None:
        """Remove one per-Heinzel folder of a session marked for deletion; idempotent."""
        ...

    def mark_deleted(self, session_id: str) -> None:
        """Record the content-free tombstone once every per-Heinzel folder is gone."""
        ...

    def reserved_session_ids(self) -> tuple[str, ...]:
        """Every reserved session of this owner that is not deleted or being deleted."""
        ...

    def unsettled_target_intents(self, session_id: str) -> int:
        """How many document appends this session started without a recorded outcome."""
        ...
