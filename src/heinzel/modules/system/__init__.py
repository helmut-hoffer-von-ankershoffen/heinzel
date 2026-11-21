"""The system module: what is installed and how it is configured."""

import os
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from pydantic import Field
from rich.console import RenderableType

from heinzel import Homebrew, Kernel, ManualInstall, Module, Progress, __version__
from heinzel.kernel import (
    KERNEL_SECTION,
    CoreMetadataVerifier,
    DaemonAttestation,
    DaemonInstallationLifecycle,
    Installation,
    OfflineUninstall,
    PluginReleases,
    ReleaseSource,
    Settings,
    UninstallResult,
    WebHeadRefresh,
)
from heinzel.kernel._tool_python import PythonNeed
from heinzel.modules.system._announced import Announcement, ToolSnapshot, trial_start
from heinzel.modules.system._metadata_identity import CoreMetadataIdentity
from heinzel.modules.system._models import (
    AnnouncedUpdate,
    AutomaticUpdates,
    ChannelReport,
    ModuleInfo,
    PluginUpdate,
    ReleaseCheck,
    ReleaseNotes,
    SystemInfo,
    ToolReference,
    UpdatePlan,
    UpdateResult,
)
from heinzel.modules.system._plugin_releases import PyPIPluginReleases, plugin_releases
from heinzel.modules.system._service import GitHubReleases, SystemService, github_releases
from heinzel.modules.system._tools import render as render_tools_text
from heinzel.modules.system._tools import tools as tool_list
from heinzel.modules.system._uninstall import SystemOfflineUninstall
from heinzel.modules.system._update import UpdateService
from heinzel.modules.system.config._service import ConfigService


def render_channel(report: ChannelReport) -> RenderableType:
    """The channel and what a switch changes, with how to confirm one that hasn't happened."""
    from heinzel.modules.system._render import render_channel  # noqa: PLC0415 - CLI presentation is loaded only when rendered

    return render_channel(report)


def render_info(info: SystemInfo) -> RenderableType:
    """An overview panel, the modules, plugin errors and the module sections."""
    from heinzel.modules.system._render import render_info  # noqa: PLC0415 - CLI presentation is loaded only when rendered

    return render_info(info)


def render_modules(modules: list[ModuleInfo]) -> RenderableType:
    """A table of modules and their lets."""
    from heinzel.modules.system._render import render_modules  # noqa: PLC0415 - CLI presentation is loaded only when rendered

    return render_modules(modules)


def render_release(check: ReleaseCheck) -> RenderableType:
    """One line: the update and where to read about it, that this is the latest, or why nobody could tell."""
    from heinzel.modules.system._render import render_release  # noqa: PLC0415 - CLI presentation is loaded only when rendered

    return render_release(check)


def render_update(result: UpdateResult) -> RenderableType:
    """What the update did, or what the installation is pinned to and the command that moves it."""
    from heinzel.modules.system._render import render_update  # noqa: PLC0415 - CLI presentation is loaded only when rendered

    return render_update(result)


module = Module("system", "Inspect, configure, diagnose and update Heinzelmännchen itself.", essential=True)
module.service(SystemService)
module.service(OfflineUninstall, SystemOfflineUninstall)
module.service(CoreMetadataVerifier, CoreMetadataIdentity)


def _update_service(installation: Installation, releases: ReleaseSource, kernel: Kernel) -> UpdateService:
    lifecycle = kernel.resolve(DaemonInstallationLifecycle) if kernel.provides(DaemonInstallationLifecycle) else None
    web = kernel.resolve(WebHeadRefresh) if kernel.provides(WebHeadRefresh) else None
    plugins = kernel.resolve(PluginReleases) if kernel.provides(PluginReleases) else None
    daemon = kernel.resolve(DaemonAttestation) if kernel.provides(DaemonAttestation) else None
    return UpdateService(
        installation,
        releases,
        lifecycle=lifecycle,
        require_lifecycle=True,
        web=web,
        progress=kernel.resolve(Progress),
        pinned=plugins.pinned if isinstance(plugins, PyPIPluginReleases) else None,
        newer=plugins.newer if plugins is not None else None,
        need=PythonNeed(kernel.settings.cache_dir),
        daemon=daemon,
    )


module.service(UpdateService, _update_service)
module.service(ReleaseSource, github_releases)  # what the CLI's hourly update check asks
module.service(PluginReleases, plugin_releases)  # what automatic updates ask about plugins (ADR 0116)

# The baseline of the standard app (ADR 0112); installing them never signs anyone in.
module.prerequisite(
    "git",
    title="Git",
    purpose="Clones team repositories and plugin sources and keeps shared state in version control.",
    when="install",
    macos=Homebrew("git"),
    requires_apple_developer_tools=True,
    linux=ManualInstall(
        "Install Git with your distribution's package manager, e.g. `sudo apt install git` or `sudo dnf install git`.",
        "https://git-scm.com/downloads/linux",
    ),
)
module.prerequisite(
    "gh",
    title="GitHub CLI",
    purpose="Reads team rosters and plugin sources on GitHub and opens pull requests as you; "
    "sign in separately with `gh auth login`.",
    when="install",
    macos=Homebrew("gh"),
    linux=ManualInstall(
        "Install the GitHub CLI for your distribution (https://github.com/cli/cli/blob/trunk/docs/install_linux.md).",
        "https://cli.github.com",
    ),
)


@module.operation(render=render_info)
def info(service: SystemService) -> SystemInfo:
    """Show versions, interpreter, settings, modules and what modules report."""
    return service.info()


@module.operation(render=render_modules)
def modules(service: SystemService) -> list[ModuleInfo]:
    """List every module and the lets it advertises."""
    return service.modules()


def render_tools(reference: ToolReference) -> RenderableType:
    """The reference as plain text, unwrapped, so it pipes into a file."""
    from heinzel.modules.system._render import render_tools  # noqa: PLC0415 - CLI presentation is loaded only when rendered

    return render_tools(reference)


@module.operation(render=render_tools)
def tools(
    kernel: Kernel,
    output: Annotated[
        Literal["text", "markdown", "yaml", "json"],
        Field(description="How to write the list; the CLI's own `--format json` wraps the whole result instead."),
    ] = "text",
    head: Annotated[
        Literal["all", "cli", "mcp", "rest", "web"],
        Field(description="Only what this head exposes; its input schemas too."),
    ] = "all",
    module: Annotated[
        list[str] | None, Field(description="Only these modules and the ones under them, e.g. `system.config`.")
    ] = None,
) -> ToolReference:
    """List every operation and tool with its heads, parameters and result, in four formats."""
    entries = tool_list(kernel, modules=module or (), head=head)
    return ToolReference(format=output, count=len(entries), text=render_tools_text(entries, output, head))


@module.operation(render=render_release)
def release(service: SystemService, installation: Installation) -> ReleaseCheck:
    """Ask where Heinzelmännchen was installed from for its latest release, and whether to update.

    One unauthenticated GET to the GitHub releases API of the repository in the uv tool receipt (or the default
    repository) with only a `heinzel/<version>` User-Agent; offline or rate-limited, the result carries a
    warning. Always asks, even when the hourly check (setting `update_check`) asked a minute ago; the answer is
    cached for the update notice.
    """
    requirements = installation.requirements()
    return service.release(requirements[0] if requirements else None)


APP_CALLER = "desktop-app"
"""The value of `HZ_CALLER` the desktop app sets for the service it starts: that service never quits the app."""


@module.operation(read_only=False, audience="everyday", render=render_update, agents="approve")
def update(  # noqa: PLR0913 - the service, where to install from, and each answer a person can give in advance
    service: UpdateService,
    kernel: Kernel,
    source: Annotated[
        str | None,
        Field(description="Install from here instead, e.g. git+https://github.com/org/heinzel@v1.2.0 or a wheel."),
    ] = None,
    channel: Annotated[
        Literal["stable", "edge", "main", "release"] | None,
        Field(
            description="Follow `stable` (the default, setting `update_channel`; `release` is its earlier name): the "
            "newest published stable release, also from `main` or edge once it is as new; `edge`: the newest edge "
            "release; or `main`: its newest commit."
        ),
    ] = None,
    *,
    yes: Annotated[
        bool,
        Field(
            description="Accepted and ignored: the update never asks, so there is nothing to agree to.",
            json_schema_extra={"hidden": True},
        ),
    ] = False,
    plan: Annotated[
        str | None,
        Field(
            description="The `token` of the plan that was shown (`--preview`). The update then installs exactly "
            "that plan; if a release appeared since, it changes nothing and answers with the new plan."
        ),
    ] = None,
    preview: Annotated[
        bool, Field(description="Only show what the update would change (`plan`); nothing is installed.")
    ] = False,
    keep_pins: Annotated[
        bool, Field(description="Leave plugins pinned to an exact version or a commit where they are.")
    ] = False,
    desktop: Annotated[
        bool | None,
        Field(
            description="Earlier name of `desktop_app`: false leaves the desktop app alone.",
            json_schema_extra={"hidden": True},
        ),
    ] = None,
    desktop_app: Annotated[
        bool,
        Field(
            description="Whether to bring an installed desktop app along with `hz app install`; "
            "`--no-desktop-app` leaves it."
        ),
    ] = True,
    desktop_app_restart: Annotated[
        bool,
        Field(
            description="Whether an open desktop app is quit and reopened so it can be replaced. "
            "`--no-desktop-app-restart` never quits it: it then installs when you quit it. The app's own service "
            "never quits the app, whatever this says."
        ),
    ] = True,
    daemon: Annotated[
        bool,
        Field(
            description="Whether the daemon and the web server are restarted onto what was installed. "
            "`--no-daemon` leaves them running and says which version they still run."
        ),
    ] = True,
) -> UpdateResult:
    """Update Heinzelmännchen, every plugin and the desktop app in one pass, without asking anything.

    See what the releases bring with `hz system changelog --newer`. The output is one checklist: hz, each plugin that
    changed (the ones that did not are one row), the daemon and web server, and the desktop app. `--preview` shows
    the plan and installs nothing. Pins move by default: a plugin that `hz system plugin add` pinned to the exact
    version or GitHub commit it installed moves to the newest release on the package index, which it follows from
    then on, or to the commit of its newest release tag (`<distribution>/vX.Y.Z`). A plugin from a branch, a folder,
    a URL, or a commit of a repository that tags no releases stays as it is, as does every pin with `--keep-pins`.
    The terminal, `--json`, the REST API and MCP all run these defaults and never ask; `--yes` is accepted and does
    nothing.

    Installed from a release tag (`@vX.Y.Z`), it moves to the tag of the highest release in SemVer order (pre-releases
    only if a pre-release is installed): one unauthenticated GET to the GitHub releases API, as `hz system release`
    makes. With `--channel main` (or the setting
    `update_channel`) it updates to the newest commit of `main`. Pinned to another branch, a commit or a local source,
    it changes nothing and says which `--source` moves it. It never moves or suggests a release older than the
    installed version.

    Plugins are updated in the same run, each from the install spec it was added with (a git spec pinned to `@main`
    gets its newest commit). A plugin that doesn't allow the new hz never blocks it: it is kept at its version if that
    still works, else taken out with the command that brings it back. The result reports each plugin.
    Following a repository's default branch or the package index, uv upgrades it. If a plugin doesn't allow the new
    version, the error names it and the previous version stays installed.

    The daemon and the web server restart onto the new version when hz or a plugin changed, or when the daemon runs
    older code than is installed (`--no-daemon` leaves them). The desktop app is replaced with `hz app install`; an open
    app is quit and reopened (`--no-desktop-app-restart`: it installs when you quit it, `--no-desktop-app`: left alone).

    Why it is not always automatic: automatic updates (setting `auto_update`, `hz system config set auto_update true`)
    run in the web server. About every 30 minutes it looks for the newest published stable release (not a draft or
    prerelease) and installs it once it is `auto_update_min_age_hours` old (plugins:
    `auto_update_plugin_min_age_hours`); this command installs that same release now, without the wait.

    Each step shows as it runs, on stderr: the checklist on a terminal, a line per step otherwise. With `--json`
    stdout holds only the result and each step goes to stderr as one JSON object per line; with `2>&1` the steps are
    left out so the output still parses.

    Raises:
        HeinzelError: If Heinzelmännchen wasn't installed with `uv tool install`, or the update fails.
    """
    del yes  # accepted for scripts that still pass it; nothing is asked
    settings = kernel.settings
    automatic = AutomaticUpdates(
        enabled=settings.auto_update,
        check_every_minutes=AUTOMATIC_CHECK_MINUTES,
        core_min_age_hours=settings.auto_update_min_age_hours,
        plugin_min_age_hours=settings.auto_update_plugin_min_age_hours,
    )
    chosen = channel or settings.update_channel
    shown = service.plan(channel=chosen) if source is None else None
    if preview:
        return _previewed(shown, "This was a preview; nothing was changed.", source)
    if shown is not None and plan is not None and plan != shown.token:
        return _previewed(shown, PLAN_CHANGED, source)
    result = service.update(
        source,
        channel=chosen,
        agreed=None if keep_pins else shown,
        shown=shown,
        desktop=desktop_app and desktop is not False,
        restart=desktop_app_restart,
        from_app=os.environ.get("HZ_CALLER") == APP_CALLER,
        daemon=daemon,
    )
    return result.model_copy(update={"automatic": automatic, "plan": shown})


PLAN_CHANGED = "The plan changed since it was shown, so nothing was changed. This is the plan now."


def _previewed(plan: UpdatePlan | None, message: str, source: str | None) -> UpdateResult:
    """The answer of an update that only showed its plan."""
    command = "hz system update" + (f" --source {source}" if source else "")
    return UpdateResult(
        before=__version__,
        after=__version__,
        latest=plan.latest if plan else None,
        pinned_to=None,
        updated=False,
        message=message if plan is not None else f"{message} `--source` installs exactly what it names.",
        next=[command],
        plan=plan,
        previewed=True,
    )


def render_plan(plan: UpdatePlan) -> RenderableType:
    """What the update would change: hz, each plugin, the desktop app and the daemon."""
    from heinzel.modules.system._render import render_plan  # noqa: PLC0415 - CLI presentation is loaded only when rendered

    return render_plan(plan)


AUTOMATIC_CHECK_MINUTES = 30
"""How often the web server checks for updates (`heinzel.heads.web._auto_update.CHECK_EVERY`; a test checks)."""


@module.operation(read_only=False, heads={"cli"}, audience="everyday")
def uninstall() -> UninstallResult:
    """Uninstall the verified Heinzel tool offline, keeping your data and configuration."""
    from heinzel.modules.system._uninstall import uninstall_installation  # noqa: PLC0415 - only explicit offline removal

    return uninstall_installation()


@module.operation(private=True, read_only=False, heads={"auto-update"}, audience="everyday")
def apply_announced(  # noqa: PLR0913, PLR0917 - the services and each part of the announcement
    service: UpdateService,
    kernel: Kernel,
    core: Annotated[str | None, Field(description="The announced hz version; none to keep the running one.")] = None,
    plugins: Annotated[
        list[str], Field(description="Each announced plugin as `name==version`; every other plugin stays.")
    ] = [],  # noqa: B006 - never changed
    commit: Annotated[str | None, Field(description="The commit hz's tag named when it was announced.")] = None,
    plugin_commits: Annotated[
        list[str],
        Field(description="Each announced plugin pinned to a GitHub commit as `name@commit`, its tag's commit then."),
    ] = [],  # noqa: B006 - never changed
) -> AnnouncedUpdate:
    """Install exactly the announced versions for automatic updates, or put everything back.

    Only the web app's server calls it (ADR 0116): no CLI, MCP or REST head offers it. The versions were announced
    once they were old enough; uv installs exactly these or nothing, the new hz must start on throwaway folders, and
    any failure puts the tool folder back as it was.

    Raises:
        HeinzelError: If the installation can't update itself, before anything was installed.
    """
    now = datetime.now(UTC)
    settings = kernel.config.reread().settings(Settings, KERNEL_SECTION)
    announcement = Announcement(
        core=core,
        plugins=dict(item.split("==", 1) for item in plugins),
        commit=commit,
        plugin_commits=dict(item.split("@", 1) for item in plugin_commits),
        exclude_newer=now - timedelta(hours=settings.auto_update_plugin_min_age_hours),
        exclude_newer_core=now - timedelta(hours=settings.auto_update_min_age_hours),
    )
    return service.apply_announced(announcement, folder=kernel.settings.state_dir / "auto-update", trial=trial_start)


@module.operation(private=True, read_only=False, heads={"auto-update"}, audience="everyday")
def recover_announced(installation: Installation, kernel: Kernel) -> bool:
    """Finish putting back an automatic update that stopped half way (ADR 0116); whether there was one.

    The web app's server asks once when it starts, holding the apply lock. A copy left behind is the truth: the
    process that took it died before the update ended, so the tool folder goes back to it.
    """
    folder = kernel.settings.state_dir / "auto-update"
    if not installation.is_tool or not (folder / "before-update").is_dir():
        return False
    ToolSnapshot(installation.prefix.resolve(strict=True), folder / "before-update", folder / "after-update").recover()
    return True


@module.operation(read_only=False, audience="everyday", render=render_channel, agents="approve")
def channel(
    service: UpdateService,
    config: ConfigService,
    kernel: Kernel,
    to: Annotated[
        Literal["stable", "edge", "main"] | None,
        Field(description="The channel to move to; without it, shows the current one."),
    ] = None,
    *,
    yes: Annotated[bool, Field(description="Make the change that was described; without it, nothing changes.")] = False,
) -> ChannelReport:
    """Show the update channel, or say what switching changes and, with `--yes`, switch.

    Default is stable. Switching installs nothing until `hz system update`, and survives updates. Going from edge to
    stable never downgrades: edge stays until stable is as new.
    """
    selected = kernel.settings.update_channel
    if to is None:
        return ChannelReport(channel=selected, changed=False, statement=service.channel_summary(selected))
    statement = service.channel_statement(selected, to)
    if to == selected or not yes:
        return ChannelReport(channel=selected, changed=False, statement=statement)
    config.set("update_channel", to)
    return ChannelReport(channel=to, changed=True, statement=statement)


__all__ = [
    "GitHubReleases",
    "ModuleInfo",
    "PluginUpdate",
    "ReleaseCheck",
    "ReleaseNotes",
    "SystemInfo",
    "SystemService",
    "UpdateResult",
    "UpdateService",
    "github_releases",
    "module",
]
