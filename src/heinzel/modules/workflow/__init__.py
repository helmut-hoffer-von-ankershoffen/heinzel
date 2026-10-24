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

from pydantic import Field, JsonValue

from heinzel import Check, HeinzelError, Module
from heinzel.kernel import CompiledJobs
from heinzel.modules.workflow._models import (
    CheckReport,
    CompiledContract,
    ContractList,
    ContractSummary,
    DraftList,
    Frontmatter,
    NewContract,
    PruneReport,
    RunResult,
    WorkflowSettings,
)
from heinzel.modules.workflow._render import (
    render_check,
    render_drafts,
    render_list,
    render_new,
    render_prune,
    render_run,
    render_show,
)
from heinzel.modules.workflow._service import WorkflowService

module = Module(
    "workflow",
    "Workflow contracts: Markdown files an agent drafts from, by hand or on your daemon's schedule "
    "(`hz workflow new` starts one).",
)
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


def _prune_exit_code(report: PruneReport) -> int:
    return 1 if report.problems else 0


@module.operation(render=render_list)
def list_(service: WorkflowService) -> ContractList:
    """List the contracts in your and your team's workflows directories, with their schedule and problems."""
    return service.listing()


# A contract written through a web page could reach a local REST server; the CLI and MCP get it, like `run`. The
# starter has no schedule, so even then nothing would run on the daemon by itself.
@module.operation(read_only=False, render=render_new, heads={"cli", "mcp"})
def new(
    service: WorkflowService,
    name: Annotated[
        str, Field(description="The contract's name: lowercase words joined by dashes, e.g. weekly-digest.")
    ],
    team: Annotated[
        bool, Field(description="Write it into the team repository's workflows/ instead of your own directory.")
    ] = False,
) -> NewContract:
    """Write a starter contract, valid as written and commented, into your workflows directory; creates it."""
    return service.new(name, team=team)


@module.operation(render=render_show)
def show(service: WorkflowService, name: Name) -> CompiledContract:
    """Show a contract as the daemon runs it: schedule, requirements, adapters, readiness, next steps."""
    return service.show(name)


# Drafts hold data fetched from your systems; a web page can reach a local REST server, so the CLI and MCP list them.
@module.operation(render=render_drafts, heads={"cli", "mcp"})
def drafts(
    service: WorkflowService,
    name: Annotated[str | None, Field(description="Only this contract's drafts; every contract's if omitted.")] = None,
    latest: Annotated[bool, Field(description="Print the newest draft instead of the list.")] = False,
) -> DraftList:
    """List the drafts runs saved locally, newest first, with their paths and records, or show the newest."""
    return service.drafts(name, latest=latest)


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


# Pruning deletes local files, and a dry run lists drafts by name; a web page can reach a local REST server, so the
# CLI and MCP get it, like `drafts`.
@module.operation(read_only=False, render=render_prune, exit_code=_prune_exit_code, heads={"cli", "mcp"})
def prune(
    service: WorkflowService,
    dry_run: Annotated[bool, Field(description="Only list what would be deleted; delete nothing.")] = False,
) -> PruneReport:
    """Delete drafts and records older than `[workflow] retention_days` (default 90); receipts are kept."""
    return service.prune(dry_run=dry_run)


# Housekeeping is an ordinary job, so the daemon's tick runs it with a receipt and `hz system daemon jobs` shows it.
@module.job(schedule="@daily", name="prune", timeout="5m")
def prune_expired(service: WorkflowService) -> JsonValue:
    """Delete drafts and records older than `[workflow] retention_days`; the receipt counts them.

    Raises:
        HeinzelError: If something couldn't be pruned, after pruning the rest, so the daemon reports the job as failed.
    """
    report = service.prune()
    counts = report.model_dump(mode="json", include={"retention_days", "cutoff", "drafts", "records", "bytes"})
    if report.problems:
        msg = f"pruned {report.drafts} draft(s) and {report.records} record(s), but: {'; '.join(report.problems)}"
        raise HeinzelError(msg)
    return counts


@module.health
def health(service: WorkflowService) -> list[Check]:
    """Contracts with errors, and scheduled contracts whose capabilities no adapter serves."""
    return service.health()


__all__ = [
    "CheckReport",
    "CompiledContract",
    "ContractList",
    "ContractSummary",
    "DraftList",
    "Frontmatter",
    "NewContract",
    "PruneReport",
    "RunResult",
    "WorkflowService",
    "WorkflowSettings",
    "module",
]
