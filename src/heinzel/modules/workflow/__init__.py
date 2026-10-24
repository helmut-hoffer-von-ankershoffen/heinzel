"""The workflow module: Markdown contracts with YAML frontmatter that compile to daemon jobs (ADR 0021).

A contract in your workflows directory, or in your team's state repository, names the capabilities it reads, the
inputs to fetch through them, the agent's limits and where the draft is meant to go. The daemon runs it on its
schedule: inputs are fetched before the agent starts, the agent gets no hz tools, and the draft is saved locally.
Nothing is published automatically; drafts wait for `hz inbox`.

`hz system serve daemon` doesn't re-read declared jobs while it runs, but contracts are read afresh on every tick: the
kernel calls `CompiledJobs`' factory each time the daemon lists its jobs, so an edited contract counts at the next tick.
`run` is a publish-style operation (`read_only=False`: it spends money and writes a local draft) that declares no
`write` capability.
"""

from pathlib import Path
from typing import Annotated

from pydantic import Field

from heinzel import Check, Module
from heinzel.kernel import CompiledJobs
from heinzel.modules.workflow._models import (
    CheckReport,
    CompiledContract,
    ContractSummary,
    Frontmatter,
    RunResult,
    WorkflowSettings,
)
from heinzel.modules.workflow._render import render_check, render_list, render_run, render_show
from heinzel.modules.workflow._service import WorkflowService

module = Module("workflow", "Workflow contracts: Markdown files that compile to daemon jobs drafting with an agent.")
module.settings(WorkflowSettings)
module.service(WorkflowService, WorkflowService.for_user)


def _compiled(service: WorkflowService) -> CompiledJobs:
    return service.compiled()


module.service(CompiledJobs, _compiled)

Name = Annotated[
    str,
    Field(description="The contract's name, or `<namespace>:<name>` (`team:` or `me:`) when both have it."),
]


def _check_exit_code(report: CheckReport) -> int:
    return 1 if report.errors else 0


@module.operation(render=render_list)
def list_(service: WorkflowService) -> list[ContractSummary]:
    """List the contracts in your and your team's workflows directories, with their schedule and problems."""
    return service.summaries()


@module.operation(render=render_show)
def show(service: WorkflowService, name: Name) -> CompiledContract:
    """Show a contract as the daemon runs it: schedule, requirements, adapters, inputs and publishes."""
    return service.show(name)


@module.operation(render=render_check, exit_code=_check_exit_code)
def check(
    service: WorkflowService,
    path: Annotated[
        Path | None,
        Field(description="A contract file, or a directory searched for *.prose.md; every contract found if none."),
    ] = None,
) -> CheckReport:
    """Check contracts and list every problem with file and line; exits with 1 on errors, so CI can gate."""
    return service.check(path)


# A render spends money; REST is reachable from a browser, so only the CLI and MCP get it.
@module.operation(read_only=False, render=render_run, heads={"cli", "mcp"})
def run(
    service: WorkflowService,
    name: Name,
    dry_run: Annotated[bool, Field(description="Only fetch the inputs and show the packet; run no agent.")] = False,
) -> RunResult:
    """Render a contract now: fetch its inputs, run the agent and save the draft; nothing is published."""
    return service.run(name, dry_run=dry_run)


@module.health
def health(service: WorkflowService) -> list[Check]:
    """Contracts with errors, and scheduled contracts whose capabilities no adapter serves."""
    return service.health()


__all__ = [
    "CheckReport",
    "CompiledContract",
    "ContractSummary",
    "Frontmatter",
    "RunResult",
    "WorkflowService",
    "WorkflowSettings",
    "module",
]
