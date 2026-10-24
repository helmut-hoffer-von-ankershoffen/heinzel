"""What the state module keeps and returns: its settings, the lines of its two streams, and push and status results.

A line is strict metadata (ADR 0062): a kind, a local date, the hz version and the operating system's family. It has
no user and no machine, because the writer branch it is pushed to names both; no time of day, no count, no command,
no path and no host, so there is nothing in it to aggregate into a measure of a person.
"""

import datetime as dt
import re
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from heinzel import ModuleSettings
from heinzel.kernel import check_repository_url

HANDLE_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
"""A roster handle, as `[report] user` holds it."""
MACHINE = re.compile(r"^[a-z0-9]{8}$")
"""A machine's id: eight random characters made once on this machine, never its host name."""
VERSION_PATTERN = r"^[0-9A-Za-z.+-]{1,40}$"
Os = Literal["macos", "linux", "other"]


class StateSettings(ModuleSettings):
    """Settings of the state module: the `[system.state]` table or `HZ_SYSTEM_STATE__*` variables.

    A project `hz.toml` can't set them: a repository someone else controls must not decide where your machine pushes.
    """

    personal: str | None = Field(
        default=None,
        description="Your own private state repository, which hz pushes this machine's presence and adoption to, "
        "on the branch hz/<your handle>/<machine>; e.g. https://github.com/mira-example/heinzel-state-mira. "
        "Unset, nothing leaves this machine.",
    )

    ignored_in_project: ClassVar[frozenset[str]] = frozenset({"personal"})

    @field_validator("personal")
    @classmethod
    def _repository(cls, value: str | None) -> str | None:
        return None if value is None or not value.strip() else check_repository_url(value)


class ReportUser(ModuleSettings):
    """Your handle in your team's roster, read from `[report] user`, the one telemetry entries carry."""

    user: str | None = Field(default=None, pattern=HANDLE_PATTERN, max_length=39)

    ignored_in_project: ClassVar[frozenset[str]] = frozenset({"user"})


class _Line(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    v: Literal[1] = Field(default=1, description="The line's format version.")
    date: dt.date = Field(description="The local date it happened on; never a time of day.")
    hz: str = Field(pattern=VERSION_PATTERN, description="The hz version that wrote it.")

    @field_validator("v", mode="before")
    @classmethod
    def _integer(cls, value: object) -> object:
        if type(value) is not int:  # `true` equals 1 to a Literal
            msg = "the version is an integer"
            raise ValueError(msg)
        return value

    @field_validator("date", mode="before")
    @classmethod
    def _iso_date(cls, value: object) -> object:
        return dt.date.fromisoformat(value) if isinstance(value, str) else value


class PresenceLine(_Line):
    """One day on which hz was used on this machine (stream `presence`): at most one line per local day."""

    kind: Literal["active"] = Field(default="active", description="What the line says: hz was used that day.")


class AdoptionLine(_Line):
    """A lifecycle event of hz on this machine (stream `adoption`): each version once, the first as `installed`."""

    kind: Literal["installed", "upgraded"] = Field(description="installed: the first line; upgraded: a new version.")
    os: Os = Field(description="The operating system's family.")


STREAMS: dict[str, type[_Line]] = {"presence": PresenceLine, "adoption": AdoptionLine}
"""The streams this module declares, by name, with the model every one of their lines must be."""


class StreamStatus(BaseModel):
    """One stream on this machine and what of it the state repository doesn't have yet."""

    name: str = Field(description="The stream: presence or adoption.")
    lines: int = Field(description="Lines this machine wrote.")
    pending: int = Field(description="Lines the last push didn't carry yet.")


class StateStatus(BaseModel):
    """Where this machine's state goes and how the last push went; asks nothing over the network."""

    repository: str | None = Field(description="Your personal state repository; none when nothing leaves.")
    branch: str | None = Field(
        description="This machine's writer branch, hz/<handle>/<machine>; none without a handle."
    )
    machine: str = Field(description="This machine's id: random, never its host name.")
    streams: list[StreamStatus] = Field(default_factory=list[StreamStatus], description="Each stream's lines.")
    last_push: dt.datetime | None = Field(default=None, description="When a push last succeeded.")
    last_attempt: dt.datetime | None = Field(default=None, description="When a push was last tried.")
    failures: int = Field(default=0, description="Failed pushes since the last that worked.")
    error: str | None = Field(default=None, description="Why the last push failed.")
    refused: bool = Field(
        default=False, description="Whether pushing stopped because the branch holds lines this machine didn't write."
    )
    next: list[str] = Field(default_factory=list[str], description="What to run next, if anything.")


class Pushed(BaseModel):
    """What a push carried to your personal state repository."""

    repository: str = Field(description="Your personal state repository.")
    branch: str = Field(description="This machine's writer branch.")
    lines: int = Field(description="New lines it carried.")
    segments: int = Field(description="Files those lines are in.")
    commit: str | None = Field(description="The branch's commit after the push; none when it is still empty.")
    pushed: bool = Field(description="Whether anything went over the network; false when the branch was current.")
