"""Models of the config submodule."""

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class ConfigFileInfo(BaseModel):
    """A configuration file in precedence order."""

    scope: str = Field(description="site, user, project or explicit.")
    path: Path = Field(description="Location of the file.")
    exists: bool = Field(description="Whether the file exists; missing files are skipped.")


class SettingInfo(BaseModel):
    """One effective setting."""

    section: str = Field(description="TOML table: `hz` for the kernel, the module's qualified name otherwise.")
    key: str = Field(description="Setting name within its section.")
    value: Any = Field(description="Value in effect.")
    env: str = Field(description="Environment variable that overrides it.")
    description: str = Field(description="What the setting controls.")
    default: Any = Field(default=None, description="Its default; none when it has none.")
    source: str = Field(
        default="default",
        description="Where the value in effect comes from: `default`, a file's scope (`site`, `user`, `project`, "
        "`explicit`) or `env`.",
    )
    json_schema: dict[str, Any] | None = Field(
        default=None,
        description="The setting's JSON Schema, self-contained (no `$ref`): its type, nested models, enums, bounds "
        "such as minimum and maximum, formats such as `iana-timezone`, descriptions and default; none when its type "
        "has no JSON Schema.",
    )


class ConfigReport(BaseModel):
    """Where configuration comes from and what is in effect."""

    files: list[ConfigFileInfo] = Field(description="Lowest precedence first; environment variables override all.")
    settings: list[SettingInfo] = Field(description="Every setting in effect.")
    can_reveal: bool = Field(
        default=False,
        description="Whether `hz system config reveal` can open this machine's file manager here: false on Linux "
        "without a desktop session, or when the opener isn't installed.",
    )
    file_manager: str = Field(
        default="file manager",
        description="What this machine's file manager is called, to label a reveal action: `Finder` on macOS, "
        "`Explorer` on Windows, `file manager` elsewhere.",
    )


RevealScope = Literal["site", "user", "project"]


class ConfigRevealed(BaseModel):
    """What `hz system config reveal` did."""

    scope: RevealScope = Field(description="Which configuration file: site, user or project.")
    path: Path | None = Field(description="The file; none for project when there is no hz.toml here or above.")
    supported: bool = Field(description="Whether this machine has a file manager hz can open.")
    opened: bool = Field(description="Whether the file manager was opened.")
    message: str = Field(description="What happened, or why nothing did, with the file's path.")
