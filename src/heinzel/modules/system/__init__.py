"""The system module: what is installed and how it is configured."""

from typing import Annotated, Literal

from pydantic import Field

from heinzel import Kernel, Module
from heinzel.kernel import CoreMetadataVerifier, DaemonInstallationLifecycle, Installation, ReleaseSource
from heinzel.modules.system._metadata_identity import CoreMetadataIdentity
from heinzel.modules.system._models import (
    ChannelReport,
    ModuleInfo,
    PluginUpdate,
    ReleaseCheck,
    ReleaseNotes,
    SystemInfo,
    UpdateResult,
)
from heinzel.modules.system._render import render_channel, render_info, render_modules, render_release, render_update
from heinzel.modules.system._service import GitHubReleases, SystemService, github_releases
from heinzel.modules.system._update import UpdateService
from heinzel.modules.system.config._service import ConfigService

module = Module("system", "Inspect, configure, diagnose and update Heinzelmännchen itself.", essential=True)
module.service(SystemService)
module.service(CoreMetadataVerifier, CoreMetadataIdentity)


def _update_service(installation: Installation, releases: ReleaseSource, kernel: Kernel) -> UpdateService:
    lifecycle = kernel.resolve(DaemonInstallationLifecycle) if kernel.provides(DaemonInstallationLifecycle) else None
    return UpdateService(installation, releases, lifecycle=lifecycle, require_lifecycle=True)


module.service(UpdateService, _update_service)
module.service(ReleaseSource, github_releases)  # what the CLI's hourly update check asks


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
def update(
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
            "newest accepted release, also from `main` or edge once it is as new; `edge`: the newest edge release; "
            "or `main`: its newest commit."
        ),
    ] = None,
) -> UpdateResult:
    """Update Heinzelmännchen and its plugins; see what it brings with `hz system changelog --newer`.

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

    Raises:
        HeinzelError: If Heinzelmännchen wasn't installed with `uv tool install`, or the update fails.
    """
    return service.update(source, channel=channel or kernel.settings.update_channel)


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
