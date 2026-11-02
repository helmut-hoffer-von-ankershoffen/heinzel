"""The heinzel module: your Heinzelmännchen, one per contract, which draft by hand or on a schedule (ADR 0051).

What hz is, not a plugin: a Heinzelmännchen is a Markdown contract with YAML frontmatter (ADR 0021) that compiles to a
daemon job. A contract in your own directory, or in your team's state repository, names the capabilities it reads,
the inputs to fetch through them, the agent's limits and where the draft is meant to go. The daemon runs it on its
schedule: inputs are fetched before the agent starts, the agent gets no hz tools, and the draft is saved locally.
Nothing is published automatically; drafts wait for you (`hz heinzel drafts`).

Renamed from `workflow` (ADR 0051): `hz workflow <op>`, the `workflow_<op>` tools, `[workflow]`, `HZ_WORKFLOW__*` and
the `workflow:<job>` ids still work for one minor release, and name the new ones.

`hz system serve daemon` doesn't re-read declared jobs while it runs, but contracts are read afresh on every tick: the
kernel calls `CompiledJobs`' factory each time the daemon lists its jobs, so an edited contract counts at the next tick.
`wake` is a publish-style operation (`read_only=False`: it spends money and writes a local draft) that declares no
`write` capability.
"""

from pathlib import Path
from typing import Annotated

from pydantic import Field, JsonValue

from heinzel import Check, Config, ConfigEditor, HeinzelError, Module, Prompter
from heinzel.kernel import CompiledJobs, PlaybookLet, PlaybookSetup, TeamPromises
from heinzel.modules.heinzel._autonomous_schedule import AutonomousSchedules
from heinzel.modules.heinzel._measure import Measurements, Sent
from heinzel.modules.heinzel._models import (
    CheckReport,
    CompiledContract,
    ContractGraph,
    ContractSummary,
    DraftList,
    Frontmatter,
    HeinzelList,
    HeinzelSettings,
    HeinzelState,
    HeinzelSummary,
    NewContract,
    PruneReport,
    RunResult,
)
from heinzel.modules.heinzel._playbooks import Discovery, PlaybookList
from heinzel.modules.heinzel._promises import ContractPromises
from heinzel.modules.heinzel._render import (
    render_check,
    render_discovery,
    render_drafts,
    render_graph,
    render_list,
    render_new,
    render_playbooks,
    render_prune,
    render_run,
    render_sent,
    render_show,
)
from heinzel.modules.heinzel._service import HeinzelService
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_operations import register_session_operations

module = Module(
    "heinzel",
    "Your Heinzelmännchen: durable helpers that draft your team's recurring work for your approval.",
    renamed_from="workflow",
)
module.settings(HeinzelSettings)
module.service(HeinzelService, HeinzelService.for_user)


def _compiled(service: HeinzelService, sessions: SessionCrud, schedules: AutonomousSchedules) -> CompiledJobs:
    return service.compiled(sessions=sessions, schedules=schedules)


module.service(CompiledJobs, _compiled)
module.service(TeamPromises, ContractPromises)
register_session_operations(module)

Name = Annotated[
    str,
    Field(description="The contract's name, or `<namespace>:<name>` (`team:` or `me:`) when both have it."),
]


def _check_exit_code(report: CheckReport) -> int:
    return 1 if report.errors else 0


def _prune_exit_code(report: PruneReport) -> int:
    return 1 if report.problems else 0


# Your contracts, your systems' readiness and your drafts' times: the CLI, MCP and the web app behind its token get it,
# never the unauthenticated REST head; the HTTP API of this module exists only for the UI (ADR 0051).
@module.operation(render=render_list, heads={"cli", "mcp", "web"}, audience="everyday", aliases=["workflow list"])
def list_(
    service: HeinzelService,
    state: Annotated[
        HeinzelState | None,
        Field(description="Only Heinzelmännchen in this state: scheduled, manual, blocked, broken."),
    ] = None,
    contract: Annotated[
        str | None,
        Field(description="Only contracts whose name or `<namespace>:<name>` contains this, e.g. `team:` or `weekly`."),
    ] = None,
    artifact: Annotated[
        str | None,
        Field(description="Only those that maintain or read this artifact, e.g. `contract:team/weekly-report#report`."),
    ] = None,
) -> HeinzelList:
    """List your Heinzelmännchen, one per contract: owner, schedule, state, last and next run."""
    return service.listing(state=state, contract=contract, artifact=artifact)


# A contract written through a web page could reach a local REST server; the CLI and MCP get it, like `run`. Neither
# the starter nor a template has an active schedule (templates with one are refused), so nothing written here runs on
# the daemon by itself.
@module.operation(
    read_only=False, render=render_new, heads={"cli", "mcp"}, audience="everyday", aliases=["workflow new"]
)
def new(
    service: HeinzelService,
    name: Annotated[
        str, Field(description="The contract's name: lowercase words joined by dashes, e.g. weekly-digest.")
    ],
    team: Annotated[
        bool, Field(description="Write it into the team repository's workflows/ instead of your own directory.")
    ] = False,
    template: Annotated[
        str | None,
        Field(
            description="Start from a playbook's template contract, `<module>:<name>` (`hz heinzel playbooks` lists "
            "them), instead of the blank starter."
        ),
    ] = None,
    asset: Annotated[
        list[str] | None,
        Field(
            description="Fill a template's asset placeholder as type=reference, e.g. weekly-report=doc-123 "
            "(`hz heinzel discover` finds candidates); repeat for each."
        ),
    ] = None,
) -> NewContract:
    """Write a starter contract, or a playbook's template (--template), into your workflows directory."""
    return service.new(name, team=team, template=template, assets=_assets(asset or []))


def _assets(pairs: list[str]) -> dict[str, str]:
    """`type=reference` pairs as a mapping.

    Raises:
        HeinzelError: If a pair has no `=`.
    """
    found: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = (part.strip() for part in pair.partition("="))
        if not sep or not key or not value:
            msg = f"--asset {pair!r} must read type=reference, e.g. weekly-report=doc-123"
            raise HeinzelError(msg)
        if key in found:
            msg = f"--asset {key} is given twice; give each asset type once"
            raise HeinzelError(msg)
        found[key] = value
    return found


@module.operation(render=render_playbooks, heads={"cli", "mcp", "web"}, aliases=["workflow playbooks"])
def playbooks(service: HeinzelService) -> PlaybookList:
    """List the playbooks plugins contribute: asset types, template contracts and recommended settings."""
    return service.playbooks()


# Hints read your systems; like `drafts`, the CLI, MCP and the web head (behind its session token) get it, not a web
# page that can reach a local REST server.
@module.operation(render=render_discovery, heads={"cli", "mcp", "web"}, aliases=["workflow discover"])
def discover(
    service: HeinzelService,
    playbook: Annotated[
        str | None, Field(description="Only this module's playbook; every playbook's if omitted.")
    ] = None,
) -> Discovery:
    """Find candidates for each playbook's asset types through the capabilities you have bound; reads only."""
    return service.discover(playbook)


class _PlaybookSetup(PlaybookSetup):
    """Walks through a playbook for its module's setup step (ADR 0037)."""

    def __init__(self, service: HeinzelService) -> None:
        self._service = service

    def run(self, playbook: PlaybookLet, prompter: Prompter, editor: ConfigEditor) -> None:
        """Propose the playbook's settings, its assets and its templates; apply only what is accepted."""
        self._service.setup(playbook, prompter, editor)


module.service(PlaybookSetup, _PlaybookSetup)


@module.operation(render=render_show, heads={"cli", "mcp", "web"}, audience="everyday", aliases=["workflow show"])
def show(service: HeinzelService, name: Name) -> CompiledContract:
    """Show a contract as the daemon runs it: schedule, requirements, adapters, readiness, next steps."""
    return service.show(name)


# Drafts hold data fetched from your systems; a web page can reach a local REST server, so the CLI and MCP list them.
# ADR 0051 moves this to a top-level `hz drafts` module; that waits for a home for `HeinzelService`'s type outside this
# module, since top-level modules don't import each other.
@module.operation(render=render_drafts, heads={"cli", "mcp"}, audience="everyday", aliases=["workflow drafts"])
def drafts(
    service: HeinzelService,
    name: Annotated[str | None, Field(description="Only this contract's drafts; every contract's if omitted.")] = None,
    latest: Annotated[bool, Field(description="Print the newest draft instead of the list.")] = False,
) -> DraftList:
    """List the drafts runs saved locally, newest first, with their paths and records, or show the newest."""
    return service.drafts(name, latest=latest)


# It reads a file you name and writes a small record next to the draft; like `drafts`, only the CLI and MCP get it.
@module.operation(read_only=False, render=render_sent, heads={"cli", "mcp"}, aliases=["workflow sent"])
def sent(  # noqa: PLR0913, PLR0917 - one option per place the approved text can be, and the draft
    service: HeinzelService,
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
    """Record how much of a draft you changed before sending it; `hz heinzel show` reports it."""
    return service.sent(name, file, ref=ref, draft=draft, edit_minutes=edit_minutes)


@module.operation(render=render_graph, heads={"cli", "mcp", "web"}, aliases=["workflow graph"])
def graph(
    service: HeinzelService,
    mermaid: Annotated[
        bool, Field(description="Print only the Mermaid flowchart, to paste into a document or a pull request.")
    ] = False,
) -> ContractGraph:
    """Show the workflow your contracts make: what each maintains and reads, cycles, unmaintained inputs."""
    return service.graph().model_copy(update={"mermaid_only": mermaid})


@module.operation(
    render=render_check, exit_code=_check_exit_code, heads={"cli", "mcp", "web"}, aliases=["workflow check"]
)
def check(
    service: HeinzelService,
    path: Annotated[
        Path | None,
        Field(description="A contract file, or a directory searched for *.prose.md; every contract found if none."),
    ] = None,
) -> CheckReport:
    """Check contracts and list every problem with file and line; exits with 1 on errors, so CI can gate."""
    return service.check(path)


# A render spends money; REST is reachable from a browser, so only the CLI and MCP get it.
@module.operation(
    read_only=False, render=render_run, heads={"cli", "mcp"}, audience="everyday", aliases=["workflow run"]
)
def wake(
    service: HeinzelService,
    name: Name,
    dry_run: Annotated[bool, Field(description="Only fetch the inputs and show the packet; run no agent.")] = False,
) -> RunResult:
    """Wake a Heinzelmännchen now: fetch its inputs, run the agent, save the draft; nothing is published."""
    return service.run(name, dry_run=dry_run)


# Pruning deletes local files, and a dry run lists drafts by name; a web page can reach a local REST server, so the
# CLI and MCP get it, like `drafts`.
@module.operation(
    read_only=False,
    render=render_prune,
    exit_code=_prune_exit_code,
    heads={"cli", "mcp"},
    aliases=["workflow prune"],
)
def prune(
    service: HeinzelService,
    dry_run: Annotated[bool, Field(description="Only list what would be deleted; delete nothing.")] = False,
) -> PruneReport:
    """Delete drafts and records older than `[heinzel] retention_days` (default 90); receipts are kept."""
    return service.prune(dry_run=dry_run)


# Housekeeping is an ordinary job, so the daemon's tick runs it with a receipt and `hz system daemon jobs` shows it.
@module.job(schedule="@daily", name="prune", timeout="5m")
def prune_expired(service: HeinzelService) -> JsonValue:
    """Delete drafts and records older than `[heinzel] retention_days`; the receipt counts them.

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
def health(service: HeinzelService, config: Config) -> list[Check]:
    """Contracts with errors, scheduled contracts whose capabilities no adapter serves, and settings still to move."""
    return [*service.health(), *service.renamed_settings(config)]


__all__ = [
    "CheckReport",
    "CompiledContract",
    "ContractGraph",
    "ContractPromises",
    "ContractSummary",
    "Discovery",
    "DraftList",
    "Frontmatter",
    "HeinzelList",
    "HeinzelService",
    "HeinzelSettings",
    "HeinzelSummary",
    "Measurements",
    "NewContract",
    "PlaybookList",
    "PruneReport",
    "RunResult",
    "Sent",
    "module",
]
