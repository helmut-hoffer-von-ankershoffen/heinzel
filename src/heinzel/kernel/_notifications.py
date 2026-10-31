"""Notifications: what deserves a person's attention now, recorded on this machine for heads to show (ADR 0043).

Modules report a `Notice` through the injected `Notifications` service: a draft is ready, a contract run failed,
doctor found a problem, a release is out. The `system.notify` module provides the service and owns the one policy
(which kinds, quiet mode, the profile's working hours, de-duplication, a rate limit) and the local store. Heads only
show what the policy lets through: the web app while it is open, the daemon as a native notification when it isn't.
Nothing leaves the machine.
"""

from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, Field


class NoticeKind(StrEnum):
    """What a notice is about; each kind can be switched off in `[system.notify]`."""

    DRAFT = "draft"
    """A draft is ready to read, edit and approve (ADR 0038)."""
    FAILED = "failed"
    """A contract run failed."""
    DOCTOR = "doctor"
    """`hz system doctor` found something degraded or down."""
    RELEASE = "release"
    """A newer release of Heinzelmännchen is available."""
    PLUGIN = "plugin"
    """A notice published by an installed provider through the shared notification policy."""
    TEST = "test"
    """`hz system notify test`: to see that notifications reach you."""
    HEINZEL = "heinzel"
    """A scheduled durable personal Heinzel checked in."""


class Delivery(StrEnum):
    """Who showed a notice to the person."""

    WEB = "web"
    """The web app, while it was open."""
    DAEMON = "daemon"
    """The daemon, as a native notification, because no app was open."""


class Notice(BaseModel):
    """One thing that deserves attention, as recorded: what it is, where to look, and whether it was shown."""

    id: str = Field(description="Identifier of the notice, unique on this machine; later notices sort after it.")
    kind: NoticeKind = Field(description="What it is about.")
    title: str = Field(description="One line: what happened.")
    body: str = Field(description="A sentence more, in plain text; may be empty.")
    route: str = Field(description="Where the web app shows it, a path such as `/doctor`.")
    command: str = Field(description="The command that shows it in a terminal, such as `hz system doctor`.")
    key: str = Field(description="What makes two notices the same, so a repeat within the window is dropped.")
    urgent: bool = Field(default=False, description="Whether it may interrupt outside working hours.")
    at: AwareDatetime = Field(description="When it was recorded.")
    due: AwareDatetime = Field(description="When it may pop up: `at`, or the next start of your working hours.")
    popup: bool = Field(description="Whether it is shown as a desktop notification at all; false: in the list only.")
    held: str | None = Field(default=None, description="Why it isn't shown as a desktop notification; none if it is.")
    delivered_by: Delivery | None = Field(default=None, description="Who showed it; none while it waits.")
    delivered_at: AwareDatetime | None = Field(default=None, description="When it was shown; none while it waits.")


class Notifications:
    """Where modules report notices and heads find what to show; `system.notify` provides the subclass (ADR 0043).

    A base class rather than a protocol, so that the kernel can inject it by type like any other service. Every
    method raises `NotImplementedError` here.
    """

    def emit(  # noqa: PLR0913 - what a notice is, all by keyword
        self,
        kind: NoticeKind,
        title: str,
        body: str = "",
        *,
        route: str,
        command: str,
        key: str,
        urgent: bool = False,
    ) -> Notice | None:
        """Record a notice under the policy; the recorded one (or the earlier one it repeats), None if its kind is off.

        Never raises for a full or unwritable store: a lost notice must not fail the job that reported it.
        """
        raise NotImplementedError

    def emit_once(  # noqa: PLR0913 - the notice fields match emit
        self, kind: NoticeKind, title: str, body: str, *, route: str, command: str, key: str
    ) -> bool:
        """Atomically persist one notice and its durable key, or recognize the previously recorded key."""
        raise NotImplementedError

    def emit_once_now(  # noqa: PLR0913 - the notice fields match emit
        self, kind: NoticeKind, title: str, body: str, *, route: str, command: str, key: str
    ) -> bool:
        """`emit_once` for a notice its owner timed themselves, in a reviewed schedule: working hours don't delay it."""
        return self.emit_once(kind, title, body, route=route, command=command, key=key)

    def forget_once(self, key: str) -> bool:
        """Forget a completed outbox key after its authoritative owner journal commits completion."""
        raise NotImplementedError

    def forget_completed(self, keys: set[str]) -> bool:
        """Compact a batch of completed outbox keys while preserving unresolved keys and visible notices."""
        raise NotImplementedError

    def has_once(self, key: str) -> bool | None:
        """Check a durable key; None means the store could not answer safely."""
        raise NotImplementedError

    def recent(self, limit: int = 50) -> list[Notice]:
        """The latest notices, newest first, at most `limit`."""
        raise NotImplementedError

    def due(self, now: datetime | None = None) -> list[Notice]:
        """Notices to pop up now: shown by nobody yet, their time come, not stale; oldest first."""
        raise NotImplementedError

    def deliver(self, ids: Sequence[str], by: Delivery) -> list[Notice]:
        """Mark notices as shown by `by`, so no one shows them again; the ones that weren't shown before."""
        raise NotImplementedError

    def watched(self) -> None:
        """Say that a head shows notices right now (the web app's stream), so the daemon holds back for a while."""
        raise NotImplementedError

    def post_native(self) -> int:
        """Pop up the due notices as native notifications unless a head shows them; how many were posted."""
        raise NotImplementedError
