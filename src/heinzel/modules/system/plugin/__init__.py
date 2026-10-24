"""The plugin module: find, add, remove, list and scaffold plugins."""

from pathlib import Path
from typing import Annotated

from pydantic import Field

from heinzel import Module
from heinzel.modules.system.plugin._models import AvailablePlugin, PluginInfo, PluginSearch, PluginSettings
from heinzel.modules.system.plugin._render import render_search
from heinzel.modules.system.plugin._service import TEMPLATE, PluginService

module = Module("plugin", "Find, add, remove, list and scaffold plugins.")
module.settings(PluginSettings)
module.service(PluginService)


@module.operation(name="list")
def list_(service: PluginService) -> list[PluginInfo]:
    """List installed plugins and the modules they bring."""
    return service.installed()


@module.operation(read_only=False)
def add(
    service: PluginService,
    spec: Annotated[
        str,
        Field(
            description="A first-party plugin by name (`clock`), or anything uv installs: a git URL "
            "(git+https://github.com/org/heinzel-x@v1.0.0), a local path or a package name."
        ),
    ],
) -> list[PluginInfo]:
    """Add a plugin; adding one again replaces the installed version."""
    return service.add(spec)


@module.operation(read_only=False)
def remove(
    service: PluginService,
    distribution: Annotated[str, Field(description="Distribution name, as `hz system plugin list` shows it.")],
) -> list[PluginInfo]:
    """Remove a plugin."""
    return service.remove(distribution)


@module.operation(read_only=False, heads={"cli"})  # copier asks questions on the terminal
def new(
    service: PluginService,
    destination: Annotated[Path, Field(description="Directory for the new plugin repository.")],
    data: Annotated[
        list[str] | None,
        Field(description="Answer a template question as key=value, e.g. plugin_name=crm; repeat for each."),
    ] = None,
    template: Annotated[
        str, Field(description="Copier template: a git URL, gh:owner/repo or a local path.")
    ] = TEMPLATE,
    ref: Annotated[
        str | None, Field(description="Template version; defaults to this Heinzelmännchen's release tag.")
    ] = None,
) -> Path:
    """Scaffold a plugin repository from the Heinzelmännchen template, asking for answers not given."""
    return service.new(destination, template, ref, data or ())


@module.operation(render=render_search)
def search(
    service: PluginService,
    text: Annotated[
        str,
        Field(
            description="Words that name or describe the plugin, e.g. `calendar`; sent to GitHub's search. "
            "All plugins if omitted."
        ),
    ] = "",
) -> PluginSearch:
    """Find plugins to install: first-party ones, and community ones by querying the GitHub API.

    Community plugins are GitHub repositories with the topic heinzel-plugin; GH_TOKEN or GITHUB_TOKEN raises the rate
    limit. Offline, only first-party plugins are listed. PyPI has no keyword search, so it is not queried. Community
    plugins are not reviewed by the Heinzelmännchen maintainers, and every plugin runs with your rights.
    """
    return service.search(text)


__all__ = ["AvailablePlugin", "PluginInfo", "PluginSearch", "PluginService", "PluginSettings", "module"]
