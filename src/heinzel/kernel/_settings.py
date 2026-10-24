"""Kernel settings: the `[hz]` table of the configuration files and `HZ_*` environment variables."""

from pathlib import Path
from typing import Annotated, Literal

import platformdirs
from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import NoDecode, SettingsConfigDict

from heinzel.kernel._config import APP_NAME, ModuleSettings


class Settings(ModuleSettings):
    """Kernel settings. See `hz system config show` for the effective values and where they come from."""

    # populate_by_name: the aliases of github_token are environment variable names; the field name still works in TOML.
    model_config = SettingsConfigDict(env_prefix="HZ_", populate_by_name=True)

    data_dir: Path = Field(
        default_factory=lambda: platformdirs.user_data_path(APP_NAME),
        description="Where Heinzelmännchen keeps its local state.",
    )
    cache_dir: Path = Field(
        default_factory=lambda: platformdirs.user_cache_path(APP_NAME),
        description="Where Heinzelmännchen keeps caches that can be deleted at any time.",
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="WARNING",
        description="Log level for Heinzelmännchen and its modules.",
    )
    output_format: Literal["text", "json"] = Field(
        default="text",
        description="Default output of every operation; `json` suits scripts and agents. `--format` overrides it.",
    )
    plugins_enabled: bool = Field(
        default=True,
        description="Discover modules from installed plugins. Disable to debug a broken plugin.",
    )
    update_check: bool = Field(
        default=True,
        description="Say in `hz --version` and `hz system info` when a newer release is available, from what "
        "`hz system release` last found; never in JSON output, when output is not a terminal or in CI. Ordinary "
        "commands never ask the network.",
    )
    github_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("HZ_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"),
        description="GitHub token for `hz system release` and `hz system plugin search`, which work without one at "
        "a lower rate limit; also read from GH_TOKEN or GITHUB_TOKEN. Sent to api.github.com only.",
    )
    disabled_modules: Annotated[frozenset[str], NoDecode] = Field(
        default=frozenset(),
        description="Qualified names of modules to switch off, with their submodules. Comma-separated in the "
        "environment, a list in TOML.",
    )

    @field_validator("disabled_modules", mode="before")
    @classmethod
    def _split_names(cls, value: object) -> object:
        if isinstance(value, str):
            return frozenset(name.strip() for name in value.split(",") if name.strip())
        return value
