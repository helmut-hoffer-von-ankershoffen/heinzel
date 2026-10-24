"""Models of the system module."""

import datetime as dt
from typing import Any

from pydantic import BaseModel, Field


class ModuleInfo(BaseModel):
    """A registered module."""

    qualname: str = Field(description="Qualified name, e.g. `system.changelog`.")
    summary: str = Field(description="What the module is for, in one sentence.")
    distribution: str = Field(description="Distribution that ships the module; `hz` for built-ins.")
    version: str = Field(description="Version of that distribution.")
    builtin: bool = Field(description="Whether the module ships with Heinzelmännchen.")
    repository: str | None = Field(
        description="Source repository of that distribution, from its Project-URL metadata; none if it names none."
    )
    lets: dict[str, list[str]] = Field(description="Names of the advertised lets, by kind.")


class SystemInfo(BaseModel):
    """The state of this Heinzelmännchen installation."""

    version: str = Field(description="Installed Heinzelmännchen version.")
    repository: str | None = Field(description="Source repository of Heinzelmännchen, from its Project-URL metadata.")
    python_version: str = Field(description="Version of the Python running hz.")
    python_executable: str = Field(description="Path of the Python interpreter running hz.")
    platform: str = Field(description="Operating system and architecture.")
    settings: dict[str, Any] = Field(description="Kernel settings in effect, by name.")
    modules: list[ModuleInfo] = Field(description="Every registered module.")
    plugin_errors: dict[str, str] = Field(description="Plugins that failed to load, by entry point name.")
    sections: dict[str, Any] = Field(description="Sections contributed by modules, by qualified module name.")
    latest_release: str | None = Field(
        description="Latest release last seen by `hz system release` or the CLI's hourly check, from the cache; "
        "none if never asked."
    )


class ReleaseCheck(BaseModel):
    """The installed version, the latest release and whether updating would bring something new."""

    current: str = Field(description="Installed Heinzelmännchen version.")
    latest: str | None = Field(description="Version of the latest release; none if the source couldn't be asked.")
    update_available: bool = Field(description="Whether the latest release is newer than the installed version.")
    url: str | None = Field(description="Release notes of the latest release, on github.com.")
    source: str = Field(description="Where the latest release was asked for.")
    warnings: list[str] = Field(description="Why the source could not be asked, e.g. offline or rate-limited.")


class ReleaseNotes(BaseModel):
    """A release newer than the installed version and what it brings, from its release page."""

    version: str = Field(description="Version of the release.")
    date: dt.date | None = Field(description="When it was published; none if the source didn't say.")
    notes: str = Field(
        description="Its release notes as Markdown: third-party text, without control or bidirectional characters."
    )
    url: str | None = Field(description="Its release page, on github.com.")


class UpdateResult(BaseModel):
    """What `hz system update` did, or why it left the installation as it was."""

    before: str = Field(description="Heinzelmännchen version installed before.")
    after: str = Field(description="Heinzelmännchen version installed now; the same as `before` if nothing changed.")
    latest: str | None = Field(
        description="Highest published release in SemVer order, when it was asked for: for an installation pinned to "
        "a release tag, a branch, a commit or a local source; none otherwise or if it couldn't be asked."
    )
    pinned_to: str | None = Field(
        description="The release tag, branch, commit, path, URL or version specifier the installation is pinned to "
        "now; none if it follows a git repository's default branch or the package index without a specifier."
    )
    updated: bool = Field(description="Whether Heinzelmännchen was reinstalled.")
    message: str = Field(description="What happened, or what the installation is pinned to and how to move it.")
    next: list[str] = Field(description="Commands that would move the installation, when it was left pinned.")
