"""Models of the notify module: its settings and what its operations return."""

from typing import ClassVar

from pydantic import BaseModel, Field

from heinzel import ModuleSettings, Notice, NoticeKind


class NotifySettings(ModuleSettings):
    """When hz pops up a desktop notification: the `[system.notify]` table or `HZ_SYSTEM_NOTIFY__*` variables.

    Your user configuration only: a project's `hz.toml` can't decide what interrupts you.
    """

    enabled: bool = Field(default=True, description="Record notices at all; false turns every kind off.")
    draft: bool = Field(default=True, description="Notify when a draft is ready to read and approve.")
    failed: bool = Field(default=True, description="Notify when a contract run on your daemon failed.")
    doctor: bool = Field(default=True, description="Notify when `hz system doctor` finds something degraded or down.")
    release: bool = Field(default=False, description="Notify when a newer release of Heinzelmännchen is out.")
    quiet: bool = Field(
        default=False, description="Quiet mode: record notices in the list, never pop one up, urgent ones included."
    )
    working_hours: bool = Field(
        default=True,
        description="Hold notices that aren't urgent until your working hours ([org] working_hours, Monday to "
        "Friday, in [org] timezone) start.",
    )
    per_hour: int = Field(
        default=6, ge=0, le=60, description="At most this many pop-ups an hour; more go to the list only."
    )
    repeat_hours: int = Field(
        default=24, ge=0, le=24 * 30, description="The same notice again within this many hours is dropped."
    )
    native: bool = Field(
        default=True,
        description="Let the daemon pop up native notifications (Heinzel Notifier on macOS, "
        "notify-send on Linux) when no web app is open.",
    )

    ignored_in_project: ClassVar[frozenset[str]] = frozenset(
        {
            "enabled",
            "draft",
            "failed",
            "doctor",
            "release",
            "quiet",
            "working_hours",
            "per_hour",
            "repeat_hours",
            "native",
        }
    )

    def wants(self, kind: NoticeKind) -> bool:
        """Whether notices of `kind` are recorded at all; a test always is, unless everything is off."""
        if not self.enabled:
            return False
        return kind is NoticeKind.TEST or bool(getattr(self, kind.value))


class NoticeList(BaseModel):
    """The latest notices, and how they reach you on this machine."""

    notices: list[Notice] = Field(description="The latest notices, newest first.")
    waiting: int = Field(description="How many of them wait to pop up: due, or held until your working hours.")
    native: str = Field(description="How the daemon pops them up when no web app is open, in words.")


class Delivered(BaseModel):
    """What `ack` marked as shown."""

    delivered: list[str] = Field(description="Ids of the notices that weren't marked as shown before.")


class Tested(BaseModel):
    """What `test` recorded, and how the native pop-up went when it was asked for."""

    notice: Notice = Field(description="The test notice.")
    native: str | None = Field(
        description="`shown` when the native notifier popped it up now, else why not; none when not asked to."
    )


class Installed(BaseModel):
    """What `install` did: the notifier, where it lives, and how the test went."""

    notifier: str | None = Field(description="The native notifier: heinzel-notifier, osascript, notify-send, or none.")
    path: str | None = Field(default=None, description="Where Heinzel Notifier is installed; none elsewhere.")
    state: str | None = Field(default=None, description="current, installed, outdated or missing; none elsewhere.")
    shown: bool = Field(description="Whether the test notification was handed to the system.")
    problem: str | None = Field(default=None, description="Why it wasn't, or why there is no notifier.")
    next: list[str] = Field(default_factory=list, description="What to do now, in words.")
