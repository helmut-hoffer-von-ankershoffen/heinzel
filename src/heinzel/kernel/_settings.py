"""Kernel settings: the `[hz]` table of the configuration files and `HZ_*` environment variables."""

import re
from pathlib import Path
from typing import Annotated, ClassVar, Literal

import platformdirs
from pydantic import AnyHttpUrl, BeforeValidator, Field, field_validator
from pydantic_settings import NoDecode, SettingsConfigDict

from heinzel.kernel._config import APP_NAME, ModuleSettings

ORIGIN_MAX_LENGTH = 512
MAX_PORT = 65535
MAX_HOST_LENGTH = 253
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def _update_channel(value: object) -> object:
    """Earlier configurations say `release`, which is now called stable."""
    return "stable" if value == "release" else value


class Settings(ModuleSettings):
    """Kernel settings. See `hz system config show` for the effective values and where they come from."""

    model_config = SettingsConfigDict(env_prefix="HZ_")
    section_title: ClassVar[str | None] = "General"  # the heading in Settings

    # Titles and descriptions are what Settings in the web app shows: plain words, no markup (docs/SETTINGS_UX.md).
    # The `x-` keys of the JSON Schema pick the control: `x-expert` lists a setting only in expert mode, `x-hidden`
    # never (another control edits it), `x-labels` names the choices, `x-unit` says what a number counts.
    data_dir: Path = Field(
        default_factory=lambda: platformdirs.user_data_path(APP_NAME),
        title="Data folder",
        description="Where Heinzelmännchen keeps its data on this computer.",
        json_schema_extra={"x-expert": True},
    )
    cache_dir: Path = Field(
        default_factory=lambda: platformdirs.user_cache_path(APP_NAME),
        title="Cache folder",
        description="Where Heinzelmännchen keeps caches; anything in it can be deleted at any time.",
        json_schema_extra={"x-expert": True},
    )
    state_dir: Path = Field(
        default_factory=lambda: platformdirs.user_state_path(APP_NAME),
        title="State folder",
        description="Where Heinzelmännchen keeps runtime state, such as locks and whether the daemon is paused.",
        json_schema_extra={"x-expert": True},
    )
    log_dir: Path = Field(
        default_factory=lambda: platformdirs.user_log_path(APP_NAME),
        title="Log folder",
        description="Where background work, such as the daemon, writes its logs.",
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="WARNING",
        title="Log detail",
        description="How much Heinzelmännchen and its modules write to the log.",
        json_schema_extra={
            "x-expert": True,
            "x-labels": {
                "DEBUG": "Everything, for debugging",
                "INFO": "Information",
                "WARNING": "Warnings and errors",
                "ERROR": "Errors only",
                "CRITICAL": "Critical errors only",
            },
        },
    )
    output_format: Literal["text", "json"] = Field(
        default="text",
        title="Output format",
        description="What every command prints unless it is asked for another format: text for people, or JSON for "
        "scripts and agents.",
        json_schema_extra={"x-expert": True, "x-labels": {"text": "Text", "json": "JSON"}},
    )
    expert: bool = Field(
        default=False,
        title="Expert mode",
        description="List every command in help, shell completion and the web app, not just the everyday ones. "
        "Hidden commands still work, and MCP and REST always list everything. Turn it on or off with hz system "
        "expert; a project's settings can't.",
        json_schema_extra={"x-hidden": True},  # its own switch in General says what it does now
    )
    playful: bool = Field(
        default=True,
        title="Playful terminal",
        description="A small Heinzelmännchen in the terminal: in the start screen, the setup, the spinner of long work "
        "and the done and error lines. Only on a colour terminal, never in JSON, a pipe or CI.",
    )
    plugins_enabled: bool = Field(
        default=True,
        title="Load plugins",
        description="Load the modules that installed plugins bring. Turn it off only to find a broken plugin.",
        json_schema_extra={"x-expert": True},
    )
    update_check: bool = Field(
        default=True,
        title="Check for updates",
        description="Look for a newer release at most once an hour and mention it after a command's output. The "
        "check sends nothing about you, and never runs in CI or in JSON, MCP or REST output.",
    )
    update_channel: Annotated[Literal["stable", "edge", "main"], BeforeValidator(_update_channel)] = Field(
        default="stable",
        title="Update channel",
        description="What updating moves to. Stable has passed acceptance. Edge is every green change, sooner and less "
        "tested. Choosing a channel installs nothing until you update. Going back to stable never downgrades: edge "
        "stays installed until stable is as new, and your data stays as it is.",
        json_schema_extra={
            "x-labels": {
                "stable": "Stable (recommended)",
                "edge": "Edge (every green change)",
                "main": "The main branch (newest commit)",
            },
        },
    )
    features: Annotated[frozenset[str], NoDecode] = Field(
        default=frozenset(),
        title="Preview features",
        description="Unfinished work to switch on by name; it may change or break. In the environment, separate them "
        "with commas.",
        json_schema_extra={"x-expert": True},
    )
    disabled_modules: Annotated[frozenset[str], NoDecode] = Field(
        default=frozenset(),
        title="Switched-off modules",
        description="Modules Heinzelmännchen doesn't load, with their submodules. In the environment, separate them "
        "with commas.",
        json_schema_extra={"x-expert": True, "x-options": {"source": "modules"}},
    )

    ui_port: int = Field(
        default=8737,
        ge=1024,
        le=65535,
        title="Web app port",
        description="The port the web app is served on, reachable from this computer only. It stays the same so an "
        "installed app keeps its address.",
        json_schema_extra={"x-expert": True},
    )
    ui_allowed_origins: Annotated[frozenset[str], NoDecode] = Field(
        default=frozenset(),
        max_length=16,
        title="Shared web app origins",
        description="Exact HTTPS origins allowed by the authenticated web app behind a private reverse proxy, "
        "such as https://workstation.example.ts.net. The listener stays on this computer. Restart and apply "
        "after changing this user-only setting. In the environment, separate origins with commas.",
        json_schema_extra={"x-expert": True, "x-private": True},
    )
    api_allowed_hosts: Annotated[frozenset[str], NoDecode] = Field(
        default=frozenset(),
        title="Other host names for the API",
        description="Host names the REST API answers besides this computer's own, for when it listens on another "
        "network interface. Requests naming any other host are refused. In the environment, separate them with "
        "commas.",
        json_schema_extra={"x-expert": True},
    )

    ignored_in_project: ClassVar[frozenset[str]] = frozenset(
        {"api_allowed_hosts", "expert", "ui_allowed_origins", "ui_port", "update_check"}
    )

    @field_validator("features", "disabled_modules", "api_allowed_hosts", mode="before")
    @classmethod
    def _split_names(cls, value: object) -> object:
        if isinstance(value, str):
            return frozenset(name.strip() for name in value.split(",") if name.strip())
        return value

    @field_validator("ui_allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            if any(char < " " or char == "\x7f" for char in value):
                msg = "HTTPS origins cannot contain control characters"
                raise ValueError(msg)
            return frozenset(origin.strip() for origin in value.split(",") if origin.strip())
        return value

    @field_validator("ui_allowed_origins")
    @classmethod
    def _https_origins(cls, values: frozenset[str]) -> frozenset[str]:
        canonical: set[str] = set()
        for value in values:
            if len(value) > ORIGIN_MAX_LENGTH or any(
                char.isspace() or char in "\\%*" or char < " " or char == "\x7f" for char in value
            ):
                msg = "Use exact HTTPS origins without whitespace, controls, escapes or wildcards"
                raise ValueError(msg)
            url = AnyHttpUrl(value)
            if (
                url.scheme != "https"
                or not _origin_host(url.host)
                or url.username is not None
                or url.password is not None
                or url.path not in (None, "/")
                or url.query is not None
                or url.fragment is not None
                or url.port is None
                or not 1 <= url.port <= MAX_PORT
            ):
                msg = "Use an HTTPS origin with only a host and optional port, for example https://workstation.example"
                raise ValueError(msg)
            canonical.add(str(url).removesuffix("/"))
        return frozenset(canonical)


def _origin_host(host: str | None) -> bool:
    """A parsed IPv6 literal or DNS host with nonempty valid labels."""
    if host is None:
        return False
    if host.startswith("["):
        return True  # AnyHttpUrl has already validated the IPv6 literal.
    name = host.removesuffix(".")
    return len(name) <= MAX_HOST_LENGTH and all(_HOST_LABEL.fullmatch(label) for label in name.split("."))
