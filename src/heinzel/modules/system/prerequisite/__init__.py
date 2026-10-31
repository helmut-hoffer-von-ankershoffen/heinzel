"""Prerequisites modules declare, inspected locally and installed only through explicit setup flows (ADR 0112)."""

from typing import Annotated

from pydantic import Field

from heinzel import (
    Check,
    HeinzelError,
    Kernel,
    Module,
    PrerequisiteCheck,
    PrerequisiteContext,
    PrerequisiteDefinition,
    PrerequisiteReport,
    PrerequisiteService,
    Progress,
    Status,
)
from heinzel.kernel import PREREQUISITE_READY
from heinzel.modules.system.prerequisite._render import render_list, render_report
from heinzel.modules.system.prerequisite._service import ToolPrerequisites

module = Module("prerequisite", "Inspect and prepare the external tools modules need.", essential=True)


def _service(kernel: Kernel, progress: Progress) -> PrerequisiteService:
    return ToolPrerequisites(kernel, progress)


module.service(PrerequisiteService, _service)

Owner = Annotated[
    str | None, Field(description="Qualified module that declares the tool; every enabled module if left out.")
]
Context = Annotated[
    PrerequisiteContext,
    Field(description="`install`: the standard app; `setup`: also what setup needs; `feature`: also named features."),
]


def _exit_code(report: PrerequisiteReport) -> int:
    return 0 if report.ready else 1


def _fix(item: PrerequisiteCheck) -> str:
    """What the person does next: the command that installs it, or the owner's instructions."""
    if item.automatic:
        return f"hz system prerequisite install --owner {item.requirement.module} --confirm"
    return item.detail


@module.operation(name="list", render=render_list)
def list_(manager: PrerequisiteService, owner: Owner = None) -> list[PrerequisiteDefinition]:
    """List the external tools modules declare and how each is installed, without checking this machine."""
    return manager.definitions(owner)


@module.operation(exit_code=_exit_code, private=True, render=render_report)
def check(manager: PrerequisiteService, owner: Owner = None, context: Context = "setup") -> PrerequisiteReport:
    """Check locally whether the declared tools are usable, without installing anything or signing in."""
    return manager.check(owner, context=context)


# Installing programs is a decision of the person at this machine, never of a web page or a tool call.
@module.operation(read_only=False, private=True, heads={"cli"}, exit_code=_exit_code, render=render_report)
def install(
    manager: PrerequisiteService,
    owner: Owner = None,
    context: Context = "setup",
    confirm: Annotated[
        bool, Field(description="Agree to install the missing tools that Homebrew can install.")
    ] = False,
) -> PrerequisiteReport:
    """Install missing tools with Homebrew on macOS, or show what to do by hand; nothing is signed in.

    Raises:
        HeinzelError: If `confirm` is not given.
    """
    if not confirm:
        msg = "Check what is missing with `hz system prerequisite check`, then run this again with --confirm."
        raise HeinzelError(msg)
    return manager.install(owner, context=context)


@module.health
def prerequisites(manager: PrerequisiteService) -> list[Check]:
    """The external tools the selected features need; looking never installs or signs in."""
    return [
        Check(
            name=f"{item.requirement.module}: {item.requirement.name}",
            status=Status.UP if item.status in PREREQUISITE_READY else Status.DEGRADED,
            detail=item.detail or f"{item.requirement.title} {item.version or ''}".strip(),
            fix="" if item.status in PREREQUISITE_READY else _fix(item),
        )
        for item in manager.check().checks
        if item.status != "skipped"
    ]
