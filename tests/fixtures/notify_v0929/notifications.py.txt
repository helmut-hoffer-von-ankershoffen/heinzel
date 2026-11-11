"""Notifications: what deserves a person's attention now, recorded on this machine for heads to show (ADR 0043).

Modules report a `Notice` through the injected `Notifications` service: a draft is ready, a contract run failed,
doctor found a problem, a release is out. The `system.notify` module provides the service and owns the one policy
(which kinds, quiet mode, the profile's working hours, de-duplication, a rate limit) and the local store. Heads only
show what the policy lets through: the web app while it is open, the daemon as a native notification when it isn't.
Nothing leaves the machine.

A notice may carry a declarative condition (`ResolveWhen`) that the service evaluates on reads and ticks: once it
holds, the notice is resolved and disappears from the list, Today and the unread count, with the reason kept in its
history. Resolution never marks anything read: only the person does that (ADR 0043, amendment 2026-11-07).
"""

from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


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
    CALENDAR = "calendar"
    """A time-sensitive calendar reminder, after explicit opt-in."""


class Delivery(StrEnum):
    """Who showed a notice to the person."""

    WEB = "web"
    """The web app, while it was open."""
    DAEMON = "daemon"
    """The daemon, as a native notification, because no app was open."""


HatColour = Literal["red", "rust", "ochre", "green", "teal", "blue", "purple", "slate"]
"""The shared closed palette for a personal Heinzel's hat."""


class NoticeHelper(BaseModel):
    """The personal Heinzel behind a notice, using the bundled face and a closed hat palette."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(max_length=100, description="The personal Heinzel's display name.")
    hat: HatColour | None = Field(default=None, description="Hat colour for the bundled face; none uses red.")
    reason: str = Field(max_length=120, description="Why the Heinzel speaks now, such as scheduled check-in.")


VERSION = r"^[0-9]+(\.[0-9]+){1,3}([.-]?(a|b|rc|dev|post)[0-9]*)?$"
"""A release version a condition may name: PEP 440 shaped, no local part."""
OPERATION = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*:[a-z][a-z0-9_-]*$"
"""`<module qualname>:<operation name>`, such as `system.daemon:status`."""
FIELD = r"^[a-z][a-z0-9_]*$"


class CapabilityState(BaseModel):
    """A capability's reported state, read through a read-only operation of the module that owns it (ADR 0094).

    The core never imports a plugin: it finds the operation among the kernel's lets by name and compares one top-level
    field of its result. An operation that is missing, unavailable, not read-only or failing never resolves a notice.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: str = Field(
        pattern=OPERATION, max_length=120, description="`<module>:<operation>`, read-only, e.g. `system.daemon:status`."
    )
    field: str = Field(pattern=FIELD, max_length=64, description="A top-level field of its result, e.g. `loaded`.")
    equals: str | bool | int = Field(description="The value that field must have for the condition to hold.")


class ResolveWhen(BaseModel):
    """When a notice stops deserving attention; every condition given must hold.

    The service evaluates it, never a head, so a stale browser or process can't keep a notice alive.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    installed_version_at_least: str | None = Field(
        default=None,
        pattern=VERSION,
        description="Holds once the hz installed on this machine (on disk, not the running process) is at least this.",
    )
    capability: CapabilityState | None = Field(default=None, description="Holds once a capability reports a state.")

    @model_validator(mode="after")
    def _not_empty(self) -> "ResolveWhen":
        """Refuse an empty condition.

        Raises:
            ValueError: If no condition is given.
        """
        if self.installed_version_at_least is None and self.capability is None:
            msg = "A resolve condition needs installed_version_at_least or capability"
            raise ValueError(msg)
        return self


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
    expires: AwareDatetime | None = Field(default=None, description="Exclusive deadline for showing this notice.")
    popup: bool = Field(description="Whether it is shown as a desktop notification at all; false: in the list only.")
    held: str | None = Field(default=None, description="Why it isn't shown as a desktop notification; none if it is.")
    helper: NoticeHelper | None = Field(default=None, description="The personal Heinzel behind this notice, if any.")
    delivered_by: Delivery | None = Field(default=None, description="Who showed it; none while it waits.")
    delivered_at: AwareDatetime | None = Field(default=None, description="When it was shown; none while it waits.")
    resolve_when: ResolveWhen | None = Field(
        default=None, description="When it resolves by itself and disappears; none: it stays until it ages out."
    )
    resolved_at: AwareDatetime | None = Field(
        default=None, description="When its condition held and it disappeared; none while it is shown."
    )
    resolved: str | None = Field(default=None, description="Why it disappeared, in words; none while it is shown.")


class Notifications:
    """Where modules report notices and heads find what to show; `system.notify` provides the subclass (ADR 0043).

    A base class rather than a protocol, so that the kernel can inject it by type like any other service. Every
    method raises `NotImplementedError` here.
    """

    def enabled(self, kind: NoticeKind) -> bool:
        """Whether a user has enabled recording this kind of notice."""
        raise NotImplementedError

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
        helper: NoticeHelper | None = None,
        resolve_when: ResolveWhen | None = None,
    ) -> Notice | None:
        """Record a notice under the policy; the recorded one (or the earlier one it repeats), None if its kind is off.

        With `resolve_when`, a notice whose condition already holds is not recorded (None), and one that is recorded
        disappears once it holds. Never raises for a full or unwritable store: a lost notice must not fail the job
        that reported it.
        """
        raise NotImplementedError

    def emit_once(  # noqa: PLR0913 - the notice fields match emit
        self,
        kind: NoticeKind,
        title: str,
        body: str,
        *,
        route: str,
        command: str,
        key: str,
        helper: NoticeHelper | None = None,
        expires: datetime | None = None,
    ) -> bool:
        """Atomically persist one notice and its durable key, or recognize the previously recorded key."""
        raise NotImplementedError

    def withdraw(self, key: str) -> bool:
        """Suppress pending notices with this key, without forgetting their durable replay receipt."""
        raise NotImplementedError

    def forget_once(self, key: str) -> bool:
        """Forget a completed outbox key after its authoritative owner journal commits completion."""
        raise NotImplementedError

    def forget_completed(self, keys: set[str]) -> bool:
        """Compact a batch of completed outbox keys while preserving unresolved keys and visible notices."""
        raise NotImplementedError

    def has_once(self, key: str) -> bool | None:
        """Check a durable key; None means the store could not answer safely."""
        raise NotImplementedError

    def resolve(self) -> int:
        """Resolve the shown notices whose condition now holds, so they disappear; how many were resolved."""
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
