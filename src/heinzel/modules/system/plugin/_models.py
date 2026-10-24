"""Models of the plugin module."""

from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, SecretStr
from pydantic_settings import SettingsConfigDict

from heinzel import ModuleSettings


class PluginSettings(ModuleSettings):
    """Settings of the plugin module: the `[system.plugin]` table or `HZ_SYSTEM_PLUGIN__*` variables."""

    # The aliases are environment variable names; the field name still works in the TOML table and in code.
    model_config = SettingsConfigDict(populate_by_name=True)

    github_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("HZ_SYSTEM_PLUGIN__GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"),
        description="GitHub token for `hz system plugin search`, which works without one at a lower rate limit; "
        "also read from GH_TOKEN or GITHUB_TOKEN. Sent to api.github.com only.",
    )


class PluginInfo(BaseModel):
    """An installed plugin distribution and the modules it brings."""

    distribution: str = Field(description="Distribution name, e.g. heinzel-clock.")
    version: str = Field(description="Installed version.")
    modules: list[str] = Field(description="Qualified names of the modules the plugin provides.")


class AvailablePlugin(BaseModel):
    """A plugin one can install with `hz system plugin add`."""

    name: str = Field(description="Distribution name, `heinzel-<name>` by convention (ADR 0025).")
    short_name: str = Field(
        description="The name without `heinzel-`: its command group (`hz <short_name>`); for first-party plugins "
        "also what `hz system plugin add` takes."
    )
    description: str = Field(description="What the plugin does, in its authors' words.")
    source: Literal["first-party", "github"] = Field(
        description="Where it was found: `first-party` (ships with Heinzelmännchen) or `github` (a repository with "
        "the topic heinzel-plugin)."
    )
    community: bool = Field(description="Whether it is a community plugin, not maintained with Heinzelmännchen.")
    install_spec: str = Field(description="What to pass to `hz system plugin add` to install it.")
    url: str = Field(description="Where to read about it.")
    stars: int | None = Field(description="GitHub stars; none for first-party plugins.")
    installed: bool = Field(description="Whether a plugin of this name is installed.")


class PluginSearch(BaseModel):
    """Plugins one can install, and what kept the search from being complete."""

    plugins: list[AvailablePlugin] = Field(description="First-party plugins first, then community plugins by stars.")
    warnings: list[str] = Field(description="Sources that could not be searched, e.g. GitHub while offline.")
