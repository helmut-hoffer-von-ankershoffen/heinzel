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
    expert: bool = Field(
        default=False,
        description="Expert mode: list every command in help, shell completion and the web app, not just the "
        "everyday ones. Hidden commands run all the same, and MCP and REST always list everything. Change it with "
        "`hz system expert on` or `off`; not settable in a project's hz.toml (ADR 0041).",
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
    update_channel: Literal["release", "main"] = Field(
        default="release",
        description="What `hz system update` follows: `release` moves to the newest release, `main` to the newest "
        "commit of the default branch. An installation on `main` moves to releases once one is as new as it.",
    )
    disabled_modules: Annotated[frozenset[str], NoDecode] = Field(
        default=frozenset(),
        description="Qualified names of modules to switch off, with their submodules. Comma-separated in the "
        "environment, a list in TOML.",
        json_schema_extra={"x-options": {"source": "modules"}},  # chosen from the modules, never typed (ADR 0055)
    )

    ui_port: int = Field(
        default=8737,
        ge=1024,
        le=65535,
        description="Port `hz ui` serves the web app on, on 127.0.0.1 only. It stays the same so an installed app (a "
        "Dock icon) keeps its address; `hz ui --port 0` picks a free one for this session. Not settable in a "
        "project's hz.toml (ADR 0046).",
    )
    api_allowed_hosts: Annotated[frozenset[str], NoDecode] = Field(
        default=frozenset(),
        description="Host names `hz system serve api` answers besides localhost, 127.0.0.1 and [::1], for when it "
        "listens on another interface (`--host`); requests naming any other host are refused, so a web page can't "
        "reach the server through DNS rebinding. Comma-separated in the environment; not settable in a project's "
        "hz.toml.",
    )

    ignored_in_project: ClassVar[frozenset[str]] = frozenset({"api_allowed_hosts", "expert", "ui_port", "update_check"})

    @field_validator("disabled_modules", "api_allowed_hosts", mode="before")
    @classmethod
    def _split_names(cls, value: object) -> object:
        if isinstance(value, str):
            return frozenset(name.strip() for name in value.split(",") if name.strip())
        return value
