"""Models of the notify module: its settings and what its operations return."""

from typing import ClassVar, Literal

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
    mail: bool = Field(
        default=True, description="Notify when important unread mail arrives (`[mail] notify_important`)."
    )
    meeting: bool = Field(
        default=True,
        description="Notify shortly before a meeting starts, with its join link (`[calendar] remind_minutes`).",
    )
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
            "meeting",
            "mail",
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


ChannelName = Literal["web", "native", "daemon"]
ChannelState = Literal["shown", "ready", "closed", "blocked", "missing", "off", "unknown"]
ChannelFix = Literal["open-settings", "install-notifier", "install-daemon", "turn-on"]


class Channel(BaseModel):
    """One way a notice can reach you, and whether it can now."""

    name: ChannelName = Field(
        description="`web`: the open web app; `native`: the system's pop-ups (Heinzel Notifier on macOS); `daemon`: "
        "the background ticks that hand due notices to the native notifier when no web app is open."
    )
    state: ChannelState = Field(
        description="`shown`: it popped the test up now; `ready`: it can; `closed`: no web app is open; `blocked`: the "
        "system refuses it; `missing`: not installed; `off`: turned off in the settings; `unknown`: can't tell."
    )
    detail: str = Field(description="Why, in words, with the system's own reason when it gave one.")
    fix: ChannelFix | None = Field(default=None, description="What fixes it, for a button; none when nothing must.")
    command: str | None = Field(default=None, description="The command that fixes it in a terminal, if one does.")


class Tested(BaseModel):
    """What `test` recorded, how each way of reaching you stands, and whether anything can pop it up."""

    notice: Notice = Field(description="The test notice.")
    native: str | None = Field(
        description="`shown` when the native notifier popped it up now, else why not; none when not asked to."
    )
    channels: list[Channel] = Field(
        default_factory=list[Channel], description="The web app, the native notifier, the daemon."
    )
    pops_up: bool = Field(
        default=True,
        description="Whether anything pops the test up: it was shown, a web app is open, or the daemon hands it to a "
        "native notifier that may show it. False means nothing will: see the channels' fixes.",
    )


class Opened(BaseModel):
    """Whether the system's notification settings were opened."""

    opened: bool = Field(description="Whether they were opened on this machine.")
    message: str = Field(description="What happened, or what to open by hand.")


class Installed(BaseModel):
    """What `install` did: the notifier, where it lives, and how the test went."""

    notifier: str | None = Field(description="The native notifier: heinzel-notifier, osascript, notify-send, or none.")
    path: str | None = Field(default=None, description="Where Heinzel Notifier is installed; none elsewhere.")
    state: str | None = Field(default=None, description="current, installed, outdated or missing; none elsewhere.")
    shown: bool = Field(description="Whether the test notification was handed to the system.")
    problem: str | None = Field(default=None, description="Why it wasn't, or why there is no notifier.")
    next: list[str] = Field(default_factory=list, description="What to do now, in words.")
