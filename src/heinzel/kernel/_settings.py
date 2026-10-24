"""Kernel settings: the `[hz]` table of the configuration files and `HZ_*` environment variables."""

from pathlib import Path
from typing import Annotated, ClassVar, Literal

import platformdirs
from pydantic import Field, field_validator
from pydantic_settings import NoDecode, SettingsConfigDict

from heinzel.kernel._config import APP_NAME, ModuleSettings


class Settings(ModuleSettings):
    """Kernel settings. See `hz system config show` for the effective values and where they come from."""

    model_config = SettingsConfigDict(env_prefix="HZ_")

    data_dir: Path = Field(
        default_factory=lambda: platformdirs.user_data_path(APP_NAME),
        description="Where Heinzelmännchen keeps its local state.",
    )
    cache_dir: Path = Field(
        default_factory=lambda: platformdirs.user_cache_path(APP_NAME),
        description="Where Heinzelmännchen keeps caches that can be deleted at any time.",
    )
    state_dir: Path = Field(
        default_factory=lambda: platformdirs.user_state_path(APP_NAME),
        description="Where Heinzelmännchen keeps runtime state such as locks and the daemon's pause flag.",
    )
    log_dir: Path = Field(
        default_factory=lambda: platformdirs.user_log_path(APP_NAME),
        description="Where background work such as the daemon writes its logs.",
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
        description="Check for a newer release at most once an hour, in the background with a 2-second timeout, "
        "and say so on stderr after human output (ADR 0031): one unauthenticated request that carries nothing about "
        "you. A failed check waits an hour too. Never in JSON, MCP or REST output, and off in CI. Set to false to "
        "turn the check and the notice off; not settable in a project's hz.toml.",
    )
    disabled_modules: Annotated[frozenset[str], NoDecode] = Field(
        default=frozenset(),
        description="Qualified names of modules to switch off, with their submodules. Comma-separated in the "
        "environment, a list in TOML.",
    )

    api_allowed_hosts: Annotated[frozenset[str], NoDecode] = Field(
        default=frozenset(),
        description="Host names `hz system serve api` answers besides localhost, 127.0.0.1 and [::1], for when it "
        "listens on another interface (`--host`); requests naming any other host are refused, so a web page can't "
        "reach the server through DNS rebinding. Comma-separated in the environment; not settable in a project's "
        "hz.toml.",
    )

    ignored_in_project: ClassVar[frozenset[str]] = frozenset({"api_allowed_hosts", "update_check"})

    @field_validator("disabled_modules", "api_allowed_hosts", mode="before")
    @classmethod
    def _split_names(cls, value: object) -> object:
        if isinstance(value, str):
            return frozenset(name.strip() for name in value.split(",") if name.strip())
        return value
