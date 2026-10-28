"""The plugin module: find, add, remove, enable, disable, list and scaffold plugins."""

from pathlib import Path
from typing import Annotated

from pydantic import Field

from heinzel import CurrentUser, HeinzelError, Module, Prompter
from heinzel.modules.system.plugin._models import (
    AvailablePlugin,
    PluginCandidate,
    PluginChange,
    PluginEvent,
    PluginInfo,
    PluginLayer,
    PluginOperation,
    PluginSearch,
    PluginSettings,
)
from heinzel.modules.system.plugin._render import render_change, render_list, render_search
from heinzel.modules.system.plugin._service import TEMPLATE, PluginService

module = Module("plugin", "Find, add, remove, enable, disable, list and scaffold plugins.")
module.settings(PluginSettings)
module.service(PluginService)

# Changing plugins installs or switches off code that runs with your rights, so it happens where a person or their
# agent is (the CLI, MCP over stdio) or behind the web head's session token, never on the unauthenticated REST head or
# MCP over HTTP, which whatever reaches their port could call (ADR 0019, 0039, 0047).
CHANGES = frozenset({"cli", "mcp-stdio", "web"})
Distribution = Annotated[str, Field(description="Distribution name, as `hz system plugin list` shows it.")]


@module.operation(name="list", render=render_list)
def list_(
    service: PluginService,
    user: CurrentUser,
    fresh: Annotated[
        bool,
        Field(description="Read the installed environment in a fresh interpreter, without reloading this server."),
    ] = False,
) -> list[PluginInfo]:
    """List installed plugins, enabled or not, with their source and the modules and adapters they bring.

    `fresh` reads the installed environment in a separate interpreter; the default keeps this process's loaded
    inventory. Reading fresh metadata does not load new plugin code into this server or remove a required restart.

    Raises:
        HeinzelError: If the requested fresh interpreter cannot read the installed environment.
    """
    if fresh:
        try:
            user.context()
        except HeinzelError as error:
            message = "Refreshing the installed plugin inventory requires an authenticated head"
            raise HeinzelError(message) from error
    return service.installed(fresh=fresh)


@module.operation(read_only=False, heads=CHANGES, render=render_change)
def add(
    service: PluginService,
    prompter: Prompter,
    spec: Annotated[
        str,
        Field(
            description="A first-party plugin by name (`github`, `acme`), a PyPI package named heinzel-<name> "
            "(`heinzel-crm==1.2.0`), or a git repository over https "
            "(`git+https://github.com/acme-example/heinzel-crm@v1.0.0`); private GitHub repositories use your gh "
            "login. Pass `requirement` from an earlier answer to install exactly what it showed."
        ),
    ],
    confirm: Annotated[
        bool,
        Field(
            description="Install it. Without it, nothing is installed: the answer shows the source, version and "
            "publisher to confirm first, because a plugin runs with your rights."
        ),
    ] = False,
    operation_id: Annotated[
        str | None,
        Field(description="Opaque receipt returned by a preview; confirms that exact candidate only once."),
    ] = None,
) -> PluginChange:
    """Show what a plugin would install, then add it with `--confirm`; adding it again replaces it.

    Without `confirm` it only reads PyPI or GitHub and answers what `requirement` would be installed, pinned to the
    release or commit it read; the answer includes a receipt that can confirm exactly that candidate once. Existing
    callers may still confirm by spec. Running servers (`hz ui`, MCP) see the plugin after a restart
    (`restart_required`).
    """
    change = service.add(spec, confirm=confirm, operation_id=operation_id)
    if confirm or not prompter.interactive or change.candidate is None:
        return change
    # A person at a terminal sees the preview and answers, instead of retyping the command with --confirm.
    prompter.show(render_change(change))
    candidate = change.candidate
    name = f"{candidate.distribution or spec} {candidate.version or ''}".strip()
    if prompter.confirm(f"Install {name} now?", default=False):
        return service.add(spec, confirm=True, operation_id=change.operation_id)
    return change.model_copy(update={"previewed": True})


@module.operation(heads=CHANGES)
def operation(
    service: PluginService, operation_id: Annotated[str, Field(description="Receipt id from a plugin preview.")]
) -> PluginOperation:
    """Read the status and result of a plugin add without repeating it."""
    return service.operation(operation_id)


@module.operation(read_only=False, heads={"cli"}, render=render_change)  # a path on this machine, typed at it
def develop(
    service: PluginService,
    path: Annotated[Path, Field(description="The plugin's checkout, e.g. `.` in a repository scaffolded by `new`.")],
) -> PluginChange:
    """Add the plugin in a directory on this machine, to try a plugin you are writing."""
    return service.develop(path)


@module.operation(read_only=False, heads=CHANGES, render=render_change)
def remove(service: PluginService, distribution: Distribution) -> PluginChange:
    """Remove a plugin."""
    return service.remove(distribution)


@module.operation(read_only=False, heads=CHANGES, render=render_change)
def disable(service: PluginService, distribution: Distribution) -> PluginChange:
    """Switch a plugin off without removing it, in `[hz] disabled_modules` of your user file."""
    return service.disable(distribution)


@module.operation(read_only=False, heads=CHANGES, render=render_change)
def enable(service: PluginService, distribution: Distribution) -> PluginChange:
    """Switch a disabled plugin on again."""
    return service.enable(distribution)


@module.operation(heads=CHANGES)  # what was installed from where is yours to see, not any local page's
def history(service: PluginService) -> list[PluginEvent]:
    """List plugin changes made on this machine, oldest first: what was installed, from where, by whom."""
    return service.history()


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


__all__ = [
    "AvailablePlugin",
    "PluginCandidate",
    "PluginChange",
    "PluginEvent",
    "PluginInfo",
    "PluginLayer",
    "PluginOperation",
    "PluginSearch",
    "PluginService",
    "PluginSettings",
    "module",
]
