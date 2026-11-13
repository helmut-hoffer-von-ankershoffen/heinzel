"""The system module: what is installed and how it is configured."""

import functools
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from pydantic import Field
from rich.console import RenderableType

from heinzel import Homebrew, Kernel, ManualInstall, Module, Progress, Prompter, __version__
from heinzel.kernel import (
    KERNEL_SECTION,
    CoreMetadataVerifier,
    DaemonInstallationLifecycle,
    Installation,
    OfflineUninstall,
    PluginReleases,
    ReleaseSource,
    Settings,
    UninstallResult,
    WebHeadRefresh,
)
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
    UpdatePlan,
    UpdateResult,
)
from heinzel.modules.system._plugin_releases import PyPIPluginReleases, plugin_releases
from heinzel.modules.system._service import GitHubReleases, SystemService, github_releases
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
    return UpdateService(
        installation,
        releases,
        lifecycle=lifecycle,
        require_lifecycle=True,
        web=web,
        progress=kernel.resolve(Progress),
        pinned=plugins.pinned if isinstance(plugins, PyPIPluginReleases) else None,
        newer=plugins.newer if plugins is not None else None,
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


@module.operation(read_only=False, audience="everyday", render=render_update)
def update(  # noqa: PLR0913 - the services, where to install from, and each answer a person can give in advance
    service: UpdateService,
    kernel: Kernel,
    prompter: Prompter,
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
            description="Agree to the plan without being asked: update hz and every plugin, also the ones pinned to "
            "the version or commit they were added at, and the desktop app if one is installed. Whoever passes it "
            "agrees for the person: a script, an agent over MCP or a REST client then moves pins and, unless "
            "`desktop` is false, runs `hz app install` without another question."
        ),
    ] = False,
    plan: Annotated[
        str | None,
        Field(
            description="The `token` of the plan that was shown (`--preview`). With `yes`, the update then installs "
            "exactly that plan; if a release appeared since, it changes nothing and answers with the new plan."
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
            description="Whether to update an installed desktop app afterwards with `hz app install`. Unset: asked "
            "on a terminal, yes with `--yes`, otherwise left for the app's own next update."
        ),
    ] = None,
) -> UpdateResult:
    """Update Heinzelmännchen, every plugin and the desktop app, after showing the plan and asking.

    See what the releases bring with `hz system changelog --newer`. One command for everything: on a terminal it
    first shows the plan (hz, each plugin, the desktop app) and asks `Update now? [Y/n]`; `--yes` agrees in advance
    and `--preview` only shows the plan. Once you agree, a plugin that `hz system plugin add` pinned to the exact
    version or GitHub commit it installed moves too: to the newest release on the package index, which it follows
    from then on, or to the commit of its newest release tag (`<distribution>/vX.Y.Z`). A plugin from a branch, a
    folder, a URL, or a commit of a repository that tags no releases stays as it is, as does every pin with
    `--keep-pins`. Where nobody can answer (a script, `--json`, the REST API) and `--yes` is not given, nothing is
    asked and pins stay.

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

    Why it is not always automatic: automatic updates (setting `auto_update`, `hz system config set auto_update true`)
    run in the web server. About every 30 minutes it looks for the newest published stable release (not a draft or
    prerelease) and installs it once it is `auto_update_min_age_hours` old (plugins:
    `auto_update_plugin_min_age_hours`); this command installs that same release now, without the wait. The desktop
    app is not part of the tool: after a change, a terminal asks `Update the desktop app now? [Y/n]`, and `--desktop`,
    `--no-desktop` or `--yes` answer in advance. Left alone, it updates itself at the web server's next automatic
    update check, or with `hz app install`. The output says which applies.

    Each step shows as it runs, on stderr: a bar on a terminal, a line per step otherwise. With `--json` stdout holds
    only the result and each step goes to stderr as one JSON object per line; with `2>&1` the steps are left out so
    the output still parses.

    Raises:
        HeinzelError: If Heinzelmännchen wasn't installed with `uv tool install`, or the update fails.
    """
    settings = kernel.settings
    automatic = AutomaticUpdates(
        enabled=settings.auto_update,
        check_every_minutes=AUTOMATIC_CHECK_MINUTES,
        core_min_age_hours=settings.auto_update_min_age_hours,
        plugin_min_age_hours=settings.auto_update_plugin_min_age_hours,
    )
    chosen = channel or settings.update_channel
    asking = prompter.interactive and not yes and not preview
    planned = preview or asking or (yes and not keep_pins)
    shown = service.plan(channel=chosen) if planned and source is None else None
    if preview:
        return _previewed(shown, "This was a preview; nothing was changed.", source)
    if shown is not None:
        again = functools.partial(service.plan, channel=chosen)
        shown, refusal = _agreement(prompter, shown, again, token=plan, asking=asking)
        if refusal is not None:
            return _previewed(shown, refusal, source)
    result = service.update(
        source,
        channel=chosen,
        agreed=None if keep_pins else shown,
        desktop=desktop is True or (desktop is None and yes),
    )
    if (
        desktop is None
        and asking
        and source is None  # a build from a source has no desktop release to bring along
        and any(head.head == "desktop" and head.status == "pending" for head in result.heads)
        and prompter.confirm("Update the desktop app now?", default=True)
    ):
        result = service.update_desktop(result)
    return result.model_copy(update={"automatic": automatic, "plan": shown})


PLAN_CHANGED = "The plan changed since it was shown, so nothing was changed. This is the plan now."
PLAN_ROUNDS = 3
"""How often a person at a terminal is shown a plan that changed while they read it, before the update gives up."""


def _agreement(
    prompter: Prompter, shown: UpdatePlan, again: Callable[[], UpdatePlan], *, token: str | None, asking: bool
) -> tuple[UpdatePlan, str | None]:
    """The plan the update installs, exactly as a person saw it, or why nothing is changed.

    Where nobody can answer, a `token` given must name the plan as it is now. On a terminal the plan is shown and
    asked about, then read once more: if a release appeared meanwhile, the person sees the new plan and is asked
    again, so nothing is ever installed that wasn't shown.
    """
    if not asking:
        return shown, PLAN_CHANGED if token is not None and token != shown.token else None
    for _ in range(PLAN_ROUNDS):
        prompter.show(render_plan(shown))
        if not prompter.confirm("Update now?", default=True):
            return shown, "Nothing was changed."
        fresh = again()
        if fresh.token == shown.token:
            return fresh, None
        prompter.say("The plan changed while it was shown. This is the plan now:")
        shown = fresh
    return shown, "The plan kept changing, so nothing was changed. Try again in a moment."


def _previewed(plan: UpdatePlan | None, message: str, source: str | None) -> UpdateResult:
    """The answer of an update that only showed its plan."""
    command = "hz system update --yes" + (f" --source {source}" if source else "")
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
    """What the update would change: hz, each plugin, the desktop app."""
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


@module.operation(read_only=False, audience="everyday", render=render_channel)
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
