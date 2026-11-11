"""The installer's prestep: the baseline tools every standard app needs, before any wheel is fetched (ADR 0112).

The Core-only kernel discovers no plugin, so only Core's own `install` declarations are seen here. A manual outcome
never fails the installation: the app installs, and what remains is repeated at the end.
"""

from collections.abc import Callable

from heinzel import HeinzelError, PrerequisiteCheck, PrerequisiteService, Settings
from heinzel.kernel import boot


def _describe(check: PrerequisiteCheck) -> str:
    return f"  {check.requirement.title} ({check.requirement.name}): {check.detail}"


def prepare(settings: Settings, *, install: bool, write: Callable[[str], object]) -> list[str]:
    """Check the baseline tools and, when `install`, let Core's service install what the machine allows.

    Returns:
        The lines still to act on by hand; empty when everything is usable.
    """
    kernel = boot(settings.model_copy(update={"plugins_enabled": False}))
    if not kernel.provides(PrerequisiteService):
        return []
    service = kernel.resolve(PrerequisiteService)
    try:
        report = service.check(context="install")
        automatic = [check for check in report.unmet if check.automatic]
        if automatic and install:
            write("Installing the tools Heinzelmännchen needs with Homebrew:\n")
            write("".join(_describe(check) + "\n" for check in automatic))
            report = service.install(context="install")
    except HeinzelError as error:
        write(f"The tools Heinzelmännchen needs could not be checked: {error}\n")
        return []
    found = [f"{c.requirement.name} {c.version}" for c in report.checks if c.status in {"available", "installed"}]
    if found:
        write(f"Tools ready: {', '.join(found)}.\n")
    pending = [_describe(check) for check in report.unmet]
    if pending:
        heading = (
            "These tools are missing; this run installs nothing:"
            if not install
            else "These tools are still missing; install them yourself, the app works without them for now:"
        )
        write(heading + "\n" + "".join(line + "\n" for line in pending))
    return pending
