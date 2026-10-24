"""Models of the config submodule."""

from pathlib import Path
from typing import Any

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


class ConfigReport(BaseModel):
    """Where configuration comes from and what is in effect."""

    files: list[ConfigFileInfo] = Field(description="Lowest precedence first; environment variables override all.")
    settings: list[SettingInfo] = Field(description="Every setting in effect.")
