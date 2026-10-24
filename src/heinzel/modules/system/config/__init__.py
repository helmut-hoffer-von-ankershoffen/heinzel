"""The config submodule of system: where configuration comes from, what is in effect, and a starter file."""

from pathlib import Path
from typing import Annotated

from pydantic import Field

from heinzel import ConfigEditor, Module
from heinzel.modules.system.config._models import (
    ConfigFileInfo,
    ConfigReport,
    ConfigRevealed,
    RevealScope,
    SettingInfo,
)
from heinzel.modules.system.config._render import render_report
from heinzel.modules.system.config._service import ConfigService, Scope

module = Module("config", "Show, get, set and initialize configuration.", essential=True)
module.service(ConfigService)
module.service(ConfigEditor, ConfigService)  # how setup steps write settings (ADR 0032)


@module.operation(render=render_report)
def show(service: ConfigService) -> ConfigReport:
    """Show the configuration files Heinzelmännchen reads and every setting in effect."""
    return service.report()


@module.operation(read_only=False)
def init(
    service: ConfigService,
    path: Annotated[Path | None, Field(description="Where to write; defaults to the user configuration file.")] = None,
    force: Annotated[bool, Field(description="Overwrite an existing file.")] = False,
) -> Path:
    """Write a configuration file with every setting commented out, ready to edit."""
    return service.init(path, force=force)


Key = Annotated[str, Field(description="Setting as section.key, e.g. hz.log_level or example.greeting.")]
ScopeOption = Annotated[Scope, Field(description="Which file to change: user, or the project's hz.toml.")]


@module.operation
def get(service: ConfigService, key: Key) -> SettingInfo:
    """Show one setting in effect, with its environment variable."""
    return service.get(key)


@module.operation(name="set", read_only=False)
def set_(
    service: ConfigService,
    key: Key,
    value: Annotated[str, Field(description="New value; TOML syntax for numbers, booleans and lists.")],
    scope: ScopeOption = "user",
) -> Path:
    """Change a setting in the user or project configuration file, after validating it."""
    return service.set(key, value, scope)


@module.operation(read_only=False)
def unset(service: ConfigService, key: Key, scope: ScopeOption = "user") -> Path:
    """Remove a setting from the user or project configuration file, so its default applies again."""
    return service.unset(key, scope)


# Opening a window is for the person at this machine: the CLI, or the web app behind its token. Not for the
# unauthenticated REST head, which any web page can reach, nor for an agent over MCP.
@module.operation(read_only=False, heads={"cli", "web"})
def reveal(
    service: ConfigService,
    scope: Annotated[RevealScope, Field(description="Which configuration file: site, user or project.")],
) -> ConfigRevealed:
    """Show a configuration file in Finder, Explorer or your desktop's file manager; nothing is created.

    A file that doesn't exist yet is shown by its nearest existing folder. On Linux without a desktop session nothing
    opens and the message names the file.
    """
    return service.reveal(scope)


__all__ = ["ConfigFileInfo", "ConfigReport", "ConfigRevealed", "ConfigService", "SettingInfo", "module"]
