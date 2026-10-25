"""The system module: what is installed and how it is configured."""

from typing import Annotated, Literal

from pydantic import Field

from heinzel import Kernel, Module
from heinzel.kernel import Installation, ReleaseSource
from heinzel.modules.system._models import ModuleInfo, ReleaseCheck, ReleaseNotes, SystemInfo, UpdateResult
from heinzel.modules.system._render import render_info, render_modules, render_release, render_update
from heinzel.modules.system._service import GitHubReleases, SystemService, github_releases
from heinzel.modules.system._update import UpdateService

module = Module("system", "Inspect, configure, diagnose and update Heinzelmännchen itself.", essential=True)
module.service(SystemService)
module.service(UpdateService)
module.service(ReleaseSource, github_releases)  # what the CLI's hourly update check asks (ADR 0031)


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
    repository) with only a `heinzel/<version>` User-Agent (ADR 0031); offline or rate-limited, the result carries a
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
        Literal["release", "main"] | None,
        Field(
            description="Follow `release` (the default, setting `update_channel`): the newest release, also from "
            "`main` once a release is as new as it; or `main`: its newest commit."
        ),
    ] = None,
) -> UpdateResult:
    """Update Heinzelmännchen, keeping plugins; see what it brings with `hz system changelog --newer`.

    Installed from a release tag (`@vX.Y.Z`), it moves to the tag of the highest release in SemVer order (pre-releases
    only if a pre-release is installed): one unauthenticated GET to the GitHub releases API, as `hz system release`
    makes. With `--channel main` (or the setting
    `update_channel`) it updates to the newest commit of `main`. Pinned to another branch, a commit or a local source,
    it changes nothing and says which `--source` moves it. It never moves or suggests a release older than the
    installed version.
    Following a repository's default branch or the package index, uv upgrades it. If a plugin doesn't allow the new
    version, the error names it and the previous version stays installed.

    Raises:
        HeinzelError: If Heinzelmännchen wasn't installed with `uv tool install`, or the update fails.
    """
    return service.update(source, channel=channel or kernel.settings.update_channel)


__all__ = [
    "GitHubReleases",
    "ModuleInfo",
    "ReleaseCheck",
    "ReleaseNotes",
    "SystemInfo",
    "SystemService",
    "UpdateResult",
    "UpdateService",
    "github_releases",
    "module",
]
