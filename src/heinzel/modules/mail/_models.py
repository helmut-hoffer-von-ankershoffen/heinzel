"""Settings of the mail module and what its operations return."""

from typing import ClassVar

from pydantic import AwareDatetime, BaseModel, Field

from heinzel import DraftedMail, MailLabels, MailMessage, ModuleSettings, SentMail


class MailSettings(ModuleSettings):
    """The `[mail]` table, or `HZ_MAIL__<FIELD>`: how far the inbox looks back, and which mail is important."""

    days: int = Field(default=7, ge=1, le=90, description="Days the inbox (`hz mail`) looks back for unread mail.")
    important_label: str = Field(
        default="IMPORTANT",
        min_length=1,
        description="The label that marks mail as important, in your mailbox's words (Gmail's is `IMPORTANT`): "
        "important unread mail comes first and gets a notice.",
    )
    notify_important: bool = Field(
        default=True,
        description="Record a notice for important unread mail as it arrives (`[system.notify] mail` turns all mail "
        "notices off).",
    )

    ignored_in_project: ClassVar[frozenset[str]] = frozenset({"important_label", "notify_important"})


class Inbox(BaseModel):
    """Mail in a window, the important first, then the newest; no bodies."""

    since: AwareDatetime = Field(description="Start of the window.")
    until: AwareDatetime = Field(description="End of the window, exclusive.")
    messages: list[MailMessage] = Field(default_factory=list[MailMessage], description="The messages listed.")
    important: list[str] = Field(default_factory=list, description="Ids of the listed messages that are important.")


class Unread(BaseModel):
    """How much unread mail waits in your inbox: what a status bar shows. Never fails; a problem says why."""

    unread: int = Field(default=0, description="Unread messages in the inbox within `[mail] days`.")
    important: int = Field(default=0, description="Of them, the important ones.")
    line: str = Field(description="One line for a status bar, e.g. `3 unread (1 important)`.")
    problem: str | None = Field(default=None, description="Why there is no count; none when it was read.")
    fix: str | None = Field(default=None, description="The command that connects a mailbox when none is.")


class MailChange(BaseModel):
    """What a mail write would do, or did: shown first, run only with `confirm`."""

    done: bool = Field(description="Whether it was carried out; false: it only says what `confirm` would do.")
    summary: str = Field(description="What it does, in a sentence.")
    confirm: str | None = Field(default=None, description="The command that carries it out, when it wasn't yet.")
    draft: DraftedMail | None = Field(default=None, description="The draft made, or the one that would be sent.")
    sent: SentMail | None = Field(default=None, description="What was sent, once sent.")
    labels: MailLabels | None = Field(default=None, description="The conversation's labels after filing it.")


class Noticed(BaseModel):
    """What one look for new important mail recorded."""

    noticed: int = Field(description="How many notices were recorded; notify drops a repeat.")
    skipped: str | None = Field(default=None, description="Why nothing was looked at.")
