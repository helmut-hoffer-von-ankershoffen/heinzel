"""What `hz report` writes and returns, and its one setting: who you are in your team's roster."""

from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from heinzel import ModuleSettings, TelemetryEntry
from heinzel.kernel import HeinzelError

HANDLE_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"


class TelemetrySettings(ModuleSettings):
    """Telemetry: the `[report]` table or `HZ_REPORT__*` variables.

    A project `hz.toml` can't set them: a repository someone else controls must not decide whose name your
    Heinzelmännchen report under.
    """

    user: str | None = Field(
        default=None,
        pattern=HANDLE_PATTERN,
        max_length=39,
        description="Your handle in your team's roster, which every entry you report carries; e.g. costas.",
    )

    ignored_in_project: ClassVar[frozenset[str]] = frozenset({"user"})

    def handle(self, given: str | None) -> str:
        """`given` when set, else your configured handle.

        Raises:
            HeinzelError: If neither names you, with the command that sets it.
        """
        if given is not None:
            return given
        if self.user is None:
            msg = (
                "hz doesn't know your handle in your team's roster yet, and every entry carries it; set it once: "
                "hz system config set report.user <your handle>"
            )
            raise HeinzelError(msg)
        return self.user


class Reported(BaseModel):
    """An entry appended to its Heinzelmännchen's log."""

    entry: TelemetryEntry = Field(description="The entry as stored, its time in UTC.")
    log: Path = Field(description="The log it was appended to; only this Heinzelmännchen's entries are in it.")
    entries: int = Field(description="How many entries the log holds now.")
    next: list[str] = Field(default_factory=list[str], description="What to run next, if anything.")


class SyncedLog(BaseModel):
    """One Heinzelmännchen's log as copied into the team repository's checkout."""

    heinzel_id: str = Field(description="The Heinzelmännchen.")
    path: Path = Field(description="Where the copy is, in the checkout.")
    entries: int = Field(description="Entries in the copy.")
    new: int = Field(description="Entries the copy didn't have before.")


class Synced(BaseModel):
    """What `hz report sync` copied into your team repository's checkout, and what it couldn't."""

    checkout: Path = Field(description="Your team repository's checkout.")
    logs: list[SyncedLog] = Field(default_factory=list[SyncedLog], description="Every log copied, by Heinzelmännchen.")
    problems: list[str] = Field(default_factory=list[str], description="Logs that couldn't be copied, and why.")
    next: list[str] = Field(default_factory=list[str], description="What to run next: commit and push the copies.")
