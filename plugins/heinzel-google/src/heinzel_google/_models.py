"""Settings and the models the plugin's own operations return."""

from pathlib import Path
from typing import ClassVar

from pydantic import AwareDatetime, BaseModel, Field, field_validator

from heinzel import ModuleSettings


class GoogleAbout(BaseModel):
    """What this plugin is, where it runs and how it reaches Google."""

    plugin: str = Field(description="Distribution name of the plugin.")
    version: str = Field(description="Installed version of the plugin.")
    heinzel_version: str = Field(description="Version of the Heinzelmännchen it runs in.")
    modules: list[str] = Field(description="Qualified names of the modules the plugin provides.")
    backend: str = Field(description="The program that talks to Google: the Google Workspace CLI, `gws`.")
    gws: str | None = Field(description="The `gws` executable in use; none if it is not found.")
    scopes: list[str] = Field(description="The read-only OAuth scopes the adapters need from `gws auth login`.")
    write_scopes: dict[str, str] = Field(
        default_factory=dict,
        description="The scope each capability's writes need, asked for only when you let hz write it (and Gmail's "
        "read-only scope when mail is read).",
    )
    apis: list[str] = Field(description="The Google APIs the adapters call through `gws`.")


class DriveFile(BaseModel):
    """A Google Doc or Sheet in Drive, as `hz google files` lists it: no owners, no people."""

    ref: str = Field(description="What `documents.read` takes: the file's URL.")
    name: str = Field(description="Name of the file in Drive.")
    kind: str = Field(description="`document` or `spreadsheet`.")
    modified_at: AwareDatetime = Field(description="When it last changed.")


class GoogleSettings(ModuleSettings):
    """Settings of the Google adapters: the `[google]` table of the `hz` config files, or `HZ_GOOGLE__<FIELD>`.

    Both are ignored in a project hz.toml: a repository someone else controls must not choose the program that reads
    your Google data, nor make every read wait for minutes.
    """

    gws_path: str | None = Field(
        default=None,
        description="Absolute path of the Google Workspace CLI (`gws`) executable; `gws` on PATH when not set. Only "
        "the site, user and HZ_CONFIG files and the environment can set it, never a project hz.toml.",
    )
    timeout: float = Field(
        default=60.0, gt=0, le=600, description="Seconds to wait for each `gws` run before stopping it."
    )

    ignored_in_project: ClassVar[frozenset[str]] = frozenset({"gws_path", "timeout"})

    @field_validator("gws_path")
    @classmethod
    def _absolute(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        if not Path(value.strip()).is_absolute():
            msg = (
                f"gws_path must be an absolute path to the gws executable, such as /opt/homebrew/bin/gws, not {value!r}"
            )
            raise ValueError(msg)
        return value.strip()
