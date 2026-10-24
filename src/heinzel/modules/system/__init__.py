"""The system module: what is installed and how it is configured."""

from typing import Annotated

from pydantic import Field

from heinzel import HeinzelError, Module
from heinzel.kernel import Installation
from heinzel.modules.system._models import ModuleInfo, ReleaseCheck, SystemInfo
from heinzel.modules.system._render import render_info, render_modules, render_release
from heinzel.modules.system._service import SystemService

module = Module("system", "Inspect, configure, diagnose and update Heinzelmännchen itself.", essential=True)
module.service(SystemService)


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
    warning. The answer is cached for the hint in `hz --version` and `hz system info` (setting `update_check`).
    """
    requirements = installation.requirements()
    return service.release(requirements[0] if requirements else None)


@module.operation(read_only=False)
def update(
    installation: Installation,
    source: Annotated[
        str | None,
        Field(description="Install from here instead, e.g. git+https://github.com/org/heinzel@v1.2.0 or a wheel."),
    ] = None,
) -> str:
    """Update Heinzelmännchen, keeping installed plugins, and show the version now installed.

    Raises:
        HeinzelError: If Heinzelmännchen wasn't installed with `uv tool install`, or the update fails.
    """
    requirements = installation.requirements()
    if requirements is None:
        msg = "Heinzelmännchen wasn't installed with `uv tool install`; update it the way you installed it."
        raise HeinzelError(msg)
    command = (
        installation.upgrade_command()
        if source is None
        else installation.tool_install_command(source, requirements[1:])
    )
    installation.run(command, "Failed to update Heinzelmännchen.", "Updating Heinzelmännchen")
    return installation.hz("--version").splitlines()[0]


__all__ = ["ModuleInfo", "ReleaseCheck", "SystemInfo", "SystemService", "module"]
