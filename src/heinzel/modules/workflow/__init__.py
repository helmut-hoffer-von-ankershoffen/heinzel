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

from heinzel import Check, ConfigEditor, HeinzelError, Module, Prompter
from heinzel.kernel import CompiledJobs, PlaybookLet, PlaybookSetup
from heinzel.modules.workflow._measure import Measurements, Sent
from heinzel.modules.workflow._models import (
    CheckReport,
    CompiledContract,
    ContractList,
    ContractSummary,
    DraftList,
    Frontmatter,
    NewContract,
    RunResult,
    WorkflowSettings,
)
from heinzel.modules.workflow._playbooks import Discovery, PlaybookList
from heinzel.modules.workflow._render import (
    render_check,
    render_discovery,
    render_drafts,
    render_list,
    render_new,
    render_playbooks,
    render_run,
    render_sent,
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
    template: Annotated[
        str | None,
        Field(
            description="Start from a playbook's template contract, `<module>:<name>` (`hz workflow playbooks` lists "
            "them), instead of the blank starter."
        ),
    ] = None,
    asset: Annotated[
        list[str] | None,
        Field(
            description="Fill a template's asset placeholder as type=reference, e.g. weekly-report=doc-123 "
            "(`hz workflow discover` finds candidates); repeat for each."
        ),
    ] = None,
) -> NewContract:
    """Write a starter contract, valid as written and commented, into your workflows directory; creates it."""
    return service.new(name, team=team, template=template, assets=_assets(asset or []))


def _assets(pairs: list[str]) -> dict[str, str]:
    """`type=reference` pairs as a mapping.

    Raises:
        HeinzelError: If a pair has no `=`.
    """
    found: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key or not value:
            msg = f"--asset {pair!r} must read type=reference, e.g. weekly-report=doc-123"
            raise HeinzelError(msg)
        found[key.strip()] = value.strip()
    return found


@module.operation(render=render_playbooks)
def playbooks(service: WorkflowService) -> PlaybookList:
    """List the playbooks plugins contribute: asset types, template contracts and recommended settings."""
    return service.playbooks()


# Hints read your systems; like `drafts`, the CLI and MCP get it, not a web page that can reach a local REST server.
@module.operation(render=render_discovery, heads={"cli", "mcp"})
def discover(
    service: WorkflowService,
    playbook: Annotated[
        str | None, Field(description="Only this module's playbook; every playbook's if omitted.")
    ] = None,
) -> Discovery:
    """Find candidates for each playbook's asset types through the capabilities you have bound; reads only."""
    return service.discover(playbook)


class _PlaybookSetup(PlaybookSetup):
    """Walks through a playbook for its module's setup step (ADR 0037)."""

    def __init__(self, service: WorkflowService) -> None:
        self._service = service

    def run(self, playbook: PlaybookLet, prompter: Prompter, editor: ConfigEditor) -> None:
        """Propose the playbook's settings, its assets and its templates; apply only what is accepted."""
        self._service.setup(playbook, prompter, editor)


module.service(PlaybookSetup, _PlaybookSetup)


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


# It reads a file you name and writes a small record next to the draft; like `drafts`, only the CLI and MCP get it.
@module.operation(read_only=False, render=render_sent, heads={"cli", "mcp"})
def sent(  # noqa: PLR0913, PLR0917 - one option per place the approved text can be, and the draft
    service: WorkflowService,
    name: Name,
    file: Annotated[
        Path | None, Field(description="What you finally sent, saved as a text or Markdown file; or give --ref.")
    ] = None,
    ref: Annotated[
        str | None,
        Field(description="The document the approved version lives in, read through the documents capability."),
    ] = None,
    draft: Annotated[
        Path | None, Field(description="The draft it was edited from; the contract's newest draft if omitted.")
    ] = None,
    edit_minutes: Annotated[
        float | None, Field(ge=0, description="Your own estimate of the minutes you spent editing; optional.")
    ] = None,
) -> Sent:
    """Record how much of a draft you changed before sending it; `hz workflow show` reports it."""
    return service.sent(name, file, ref=ref, draft=draft, edit_minutes=edit_minutes)


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
    "ContractList",
    "ContractSummary",
    "Discovery",
    "DraftList",
    "Frontmatter",
    "Measurements",
    "NewContract",
    "PlaybookList",
    "RunResult",
    "Sent",
    "WorkflowService",
    "WorkflowSettings",
    "module",
]
