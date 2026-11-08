"""The plugin module: find, add, remove, enable, disable, list and scaffold plugins."""

import json
from pathlib import Path
from typing import Annotated, Literal, cast

from pydantic import Field, JsonValue
from rich.console import RenderableType

from heinzel import CurrentUser, HeinzelError, Kernel, Module, Prompter
from heinzel.kernel import DaemonInstallationLifecycle, Installation
from heinzel.modules.system.plugin._models import (
    AvailablePlugin,
    PluginCandidate,
    PluginChange,
    PluginEvent,
    PluginInfo,
    PluginLayer,
    PluginOperation,
    PluginPending,
    PluginRepair,
    PluginSearch,
    PluginSettings,
)
from heinzel.modules.system.plugin._service import TEMPLATE, PluginService
from heinzel.modules.system.plugin._setup import SetupService
from heinzel.modules.system.plugin._setup_models import Checklist, SetupBind, SetupResult


def render_change(change: PluginChange) -> RenderableType:
    """For `add` without confirming: what would be installed and the command that confirms; otherwise what was done."""
    from heinzel.modules.system.plugin._render import render_change  # noqa: PLC0415 - CLI presentation is loaded only when rendered

    return render_change(change)


def render_repair(repair: PluginRepair) -> RenderableType:
    """Each required plugin a repair installed or couldn't install, then what it means."""
    from heinzel.modules.system.plugin._render import render_repair  # noqa: PLC0415 - CLI presentation is loaded only when rendered

    return render_repair(repair)


def render_list(plugins: list[PluginInfo]) -> RenderableType:
    """A table of installed plugins: version, source, whether enabled, and what each brings."""
    from heinzel.modules.system.plugin._render import render_list  # noqa: PLC0415 - CLI presentation is loaded only when rendered

    return render_list(plugins)


def render_search(result: PluginSearch) -> RenderableType:
    """A table of installable plugins, then any warnings and a reminder that plugins run with your rights."""
    from heinzel.modules.system.plugin._render import render_search  # noqa: PLC0415 - CLI presentation is loaded only when rendered

    return render_search(result)


module = Module("plugin", "Find, add, remove, enable, disable, list and scaffold plugins.")
module.settings(PluginSettings)


def _plugin_service(kernel: Kernel, installation: Installation, settings: PluginSettings) -> PluginService:
    lifecycle = kernel.resolve(DaemonInstallationLifecycle) if kernel.provides(DaemonInstallationLifecycle) else None
    return PluginService(kernel, installation, settings, lifecycle=lifecycle)


module.service(PluginService, _plugin_service)

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
def add(  # noqa: PLR0913, PLR0917 - one parameter per option of the operation
    service: PluginService,
    prompter: Prompter,
    spec: Annotated[
        str,
        Field(
            description="A first-party plugin by name (`github`, `acme`), a PyPI package named heinzel-<name> "
            "(`heinzel-crm==1.2.0`), or a git repository over https "
            "(`git+https://github.com/acme-example/heinzel-crm@v1.0.0`); private GitHub repositories use your gh "
            "login. If the repository's distribution name cannot be resolved, provide its actual name in a named "
            "requirement, such as `heinzel-example @ git+https://example.com/repository`. Pass `requirement` from an "
            "earlier answer to install exactly what it showed."
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
    with_recommended: Annotated[
        bool,
        Field(
            description="Also install the plugins it recommends (its connectors). Off: they are only offered. Decided "
            "by the preview: a receipt installs what its preview showed."
        ),
    ] = True,
    bind: Annotated[
        bool,
        Field(
            description="After installing, set the connector bindings its playbooks recommend; a binding you set is "
            "always kept, and `hz system plugin unbind <module>` undoes them."
        ),
    ] = True,
) -> PluginChange:
    """Show what a plugin would install, then add it with `--confirm`; adding it again replaces it.

    Without `confirm` it only reads PyPI or GitHub and answers what `requirement` would be installed, pinned to the
    release or commit it read; the answer includes a receipt that can confirm exactly that candidate once. Existing
    callers may still confirm by spec. Running servers (`hz ui serve`, MCP) see the plugin after a restart
    (`restart_required`).
    """
    change = service.add(spec, confirm=confirm, operation_id=operation_id, with_recommended=with_recommended, bind=bind)
    if confirm or not prompter.interactive or change.candidate is None:
        return change
    # A person at a terminal sees the preview and answers, instead of retyping the command with --confirm.
    prompter.show(render_change(change))
    candidate = change.candidate
    name = f"{candidate.distribution or spec} {candidate.version or ''}".strip()
    if prompter.confirm(f"Install {name} now?", default=False):
        return service.add(spec, confirm=True, operation_id=change.operation_id, bind=bind)
    return change.model_copy(update={"previewed": True})


@module.operation(read_only=False, heads=CHANGES, render=render_repair)
def repair(
    service: PluginService,
    automatic: Annotated[
        bool,
        Field(
            description="As the automatic updater asks: try each missing plugin once per version of the plugin that "
            "requires it, so a failure isn't retried every half hour."
        ),
    ] = False,
) -> PluginRepair:
    """Install every plugin an installed plugin requires that is missing or too old, from the same catalog.

    A plugin declares the plugins it can't work without in `[tool.heinzel.plugin] requires-plugins`; `add` installs them
    first, and this repairs an installation that lacks them (one installed before they were declared). It never signs
    in: `hz system doctor` still names each plugin's sign-in step. Running servers load them after a restart.
    """
    return service.repair(automatic=automatic)


@module.operation(heads=CHANGES)
def pending(service: PluginService) -> PluginPending:
    """List plugins changed on this machine since this server started: what a restart would load.

    Raises:
        HeinzelError: If the installed environment cannot be read.
    """
    return service.pending()


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
def new(  # noqa: PLR0913 - each option is independently exposed and documented by the CLI
    service: PluginService,
    destination: Annotated[Path, Field(description="Directory for the new plugin repository.")],
    *,
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
    defaults: Annotated[bool, Field(description="Use template defaults without interactive questions.")] = False,
    layout: Annotated[
        Literal["standalone", "collection"],
        Field(description="A standalone repository, or only owning files inside a plugin collection."),
    ] = "standalone",
    preset: Annotated[
        Literal["minimal", "contributions"],
        Field(description="One operation and test, or the complete UI and notification example."),
    ] = "minimal",
) -> Path:
    """Scaffold a plugin repository from the Heinzelmännchen template, asking for answers not given."""
    return service.new(destination, template, ref, data or (), defaults=defaults, layout=layout, preset=preset)


@module.operation(read_only=False, heads={"cli"})
def sdk(
    service: PluginService,
    destination: Annotated[
        Path, Field(description="New directory for the installed view SDK and TypeScript definitions.")
    ],
) -> list[Path]:
    """Export the versioned plugin view SDK from this installation, without a core checkout."""
    return service.sdk(destination)


@module.operation(render=render_search, private=True)
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

    Configured repositories are scanned for plugin projects; topic heinzel-plugin discovers external repositories.
    Your GitHub login also reads private catalogs; authenticated heads only. GH_TOKEN or GITHUB_TOKEN raises the
    rate limit. Catalog outages retain last successfully read metadata.
    PyPI has no keyword search, so it is not queried. Community
    plugins are not reviewed by the Heinzelmännchen maintainers, and every plugin runs with your rights.
    """
    return service.search(text)


# Setting up what a plugin's playbook recommends (ADR 0144).


def render_bind(result: SetupBind) -> RenderableType:
    """The bindings with their results."""
    from heinzel.modules.system.plugin._render import render_bind  # noqa: PLC0415 - CLI presentation is loaded only when rendered

    return render_bind(result)


def render_checklist(checklist: Checklist) -> RenderableType:
    """One row per connector with its next step."""
    from heinzel.modules.system.plugin._render import render_checklist  # noqa: PLC0415 - CLI presentation is loaded only when rendered

    return render_checklist(checklist)


def render_result(result: SetupResult) -> RenderableType:
    """The steps, then the checklist."""
    from heinzel.modules.system.plugin._render import render_result  # noqa: PLC0415 - CLI presentation is loaded only when rendered

    return render_result(result)


module.service(SetupService)

MODULE = Annotated[str, Field(description="The module whose playbook to set up, e.g. `acme`.")]


@module.operation(read_only=False, heads=CHANGES, render=render_result)
def setup(
    service: SetupService,
    module: MODULE,
    answer: Annotated[
        list[str] | None,
        Field(
            description="A playbook setting to accept, as `key=value` (the value as JSON, else text), e.g. "
            "`acme.region=lisbon`; repeat for each."
        ),
    ] = None,
) -> SetupResult:
    """Set up a module's playbook without questions: its connectors, its settings, then the checklist.

    A binding you set is never changed. Choosing assets and writing templates need a terminal: `hz setup <module>`.
    """
    return service.run(module, parse_answers(answer or []))


def parse_answers(answers: list[str]) -> dict[str, JsonValue]:
    """`key=value` pairs as settings: the value read as JSON when it is JSON, else as text.

    Raises:
        HeinzelError: If a pair has no `=`.
    """
    parsed: dict[str, JsonValue] = {}
    for pair in answers:
        key, sep, raw = pair.partition("=")
        if not sep or not key.strip():
            msg = f"An answer is `key=value`, not {pair!r}."
            raise HeinzelError(msg)
        try:
            parsed[key.strip()] = cast("JsonValue", json.loads(raw))
        except ValueError:
            parsed[key.strip()] = raw
    return parsed


@module.operation(heads=CHANGES, render=render_checklist)
def checklist(
    service: SetupService,
    module: Annotated[
        str | None, Field(description="The module whose playbook it is; every installed playbook's if omitted.")
    ] = None,
) -> Checklist:
    """List each connector a playbook needs, how it stands and the one next step, mostly your sign-ins."""
    return service.checklist(module)


@module.operation(read_only=False, heads=CHANGES, render=render_bind)
def bind(  # noqa: PLR0913, PLR0917 - one parameter per option of the operation
    service: SetupService,
    plugin: Annotated[
        list[str] | None,
        Field(
            description="Only the playbooks of this plugin distribution; repeat for each. Every plugin's if omitted."
        ),
    ] = None,
    module: Annotated[str | None, Field(description="Only this module's playbook.")] = None,
    operation_id: Annotated[
        str | None, Field(description="The plugin operation that installed it, remembered with each binding.")
    ] = None,
    once: Annotated[
        bool, Field(description="Consider each playbook once per version of its plugin, as an automatic repair does.")
    ] = False,
    decline: Annotated[
        bool, Field(description="Write nothing; remember the playbooks as considered, so a repair leaves them.")
    ] = False,
    dry_run: Annotated[bool, Field(description="Say what it would write; write nothing.")] = False,
) -> SetupBind:
    """Write the connector bindings installed playbooks recommend; a binding you set is always kept.

    `hz system plugin add` runs it after an install. A recommendation for a connector that isn't installed is skipped,
    and layers that disagree bind nothing until you choose.
    """
    return service.bind(
        module=module, plugins=plugin or (), operation_id=operation_id, once=once, decline=decline, dry_run=dry_run
    )


@module.operation(read_only=False, heads=CHANGES, render=render_bind)
def unbind(
    service: SetupService,
    by: Annotated[str, Field(description="The module whose recommended bindings to remove, e.g. `acme`.")],
) -> SetupBind:
    """Undo the connector bindings setup wrote for a module; one you changed since stays as you set it."""
    return service.unbind(by)


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
