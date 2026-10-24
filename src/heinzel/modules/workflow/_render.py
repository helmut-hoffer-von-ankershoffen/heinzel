"""How the CLI head presents workflow contracts.

Contract files are written by others (a team repository), so their text goes into tables escaped, never as markup.
"""

from rich.console import Group, RenderableType
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from heinzel.modules.workflow._measure import Measurements, Sent
from heinzel.modules.workflow._models import (
    GUIDE,
    CheckReport,
    CompiledContract,
    ContractList,
    Diagnostic,
    Directory,
    DraftList,
    NewContract,
    PruneReport,
    RunResult,
    Step,
)
from heinzel.modules.workflow._playbooks import Discovery, PlaybookList

WHAT_A_CONTRACT_IS = (
    "A contract is a <name>.prose.md file: YAML frontmatter says what it reads and when it runs, and a Markdown body "
    "says what the agent must draft.\nContracts with a schedule run on your daemon; drafts are saved locally and "
    "nothing is published by itself."
)


def render_list(listing: ContractList) -> RenderableType:
    """One row per contract: where it is from, its schedule and whether it is valid and scheduled."""
    if not listing.contracts:
        return Group(
            Text("No contracts yet.\n", style="bold"),
            Text(WHAT_A_CONTRACT_IS + "\n"),
            _directories("Searched", listing.directories),
            _next(listing.next),
            Text(f"Guide: {GUIDE}"),
        )
    table = Table(title="Workflow contracts")
    for column in ("Contract", "Title", "Schedule", "Daemon", "Problems"):
        table.add_column(column, overflow="fold")
    for contract in listing.contracts:
        problems = f"{contract.errors} error(s)" if contract.errors else ""
        if contract.warnings:
            problems = ", ".join(filter(None, [problems, f"{contract.warnings} warning(s)"]))
        table.add_row(
            escape(f"{contract.namespace}:{contract.name}"),
            escape(contract.title or "-"),
            escape(contract.schedule or "by hand"),
            "scheduled" if contract.scheduled else "-",
            f"[red]{problems}[/red]" if contract.errors else problems,
        )
    return Group(table, _directories("Directories", listing.directories), _next(listing.next))


def _directories(title: str, directories: list[Directory]) -> Table:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold")
    grid.add_column()
    grid.add_column(overflow="fold")
    for directory in directories:
        note = "" if directory.exists else "(does not exist yet; `hz workflow new <name>` creates it)"
        grid.add_row(
            title if directory is directories[0] else "", directory.namespace, Text(f"{directory.path} {note}")
        )
    return grid


def _next(steps: list[Step]) -> Text:
    """Commands to run next, one per line so each can be copied, with why below; text, never markup."""
    lines = Text("Next\n", style="bold")
    for step in steps:
        lines.append(f"  {step.command}\n")
        lines.append(f"      {step.why}\n", style="dim")
    return lines


def render_show(contract: CompiledContract) -> RenderableType:
    """The compiled view: schedule, requirements with their adapters, inputs, outputs, publishes and limits."""
    overview = Table.grid(padding=(0, 2))
    overview.add_column(style="bold")
    overview.add_column(overflow="fold")
    overview.add_row("Contract", escape(f"{contract.namespace}:{contract.name} ({contract.file})"))
    overview.add_row("Title", escape(contract.title or "-"))
    overview.add_row("Schedule", escape(contract.schedule or "by hand"))
    overview.add_row("Daemon job", escape(contract.job or f"none: {contract.why_not_scheduled}"))
    overview.add_row("Agent", escape(_agent(contract)))
    overview.add_row("Drafts", escape(f"{contract.drafts} ({contract.draft_count} so far)"))
    overview.add_row("Ready", _ready(contract))
    overview.add_row("SHA-256", contract.sha256)
    if contract.approve:
        overview.add_row("Approve", escape(f'[workflow] schedule_team = ["{contract.approve}"]'))
    parts: list[RenderableType] = [overview]
    if contract.requirements:
        parts.append(_requirements(contract))
    if contract.inputs or contract.outputs or contract.publishes:
        parts.append(_flows(contract))
    if contract.measurements is not None:
        parts.append(_measurements(contract.measurements, f"{contract.namespace}:{contract.name}"))
    if contract.diagnostics:
        parts.append(_diagnostics(contract.diagnostics))
    parts.append(_next(contract.next))
    return Group(*parts)


def _ready(contract: CompiledContract) -> Text:
    if contract.ready:
        return Text("yes: every requirement is served", style="green")
    if contract.unmet:
        return Text(f"no: unmet {', '.join(contract.unmet)} (see Requires)", style="yellow")
    return Text("no: it has errors (below)", style="red")


def _requirements(contract: CompiledContract) -> Table:
    requirements = Table(title="Requires")
    for column in ("Requirement", "Adapter", "Problem"):
        requirements.add_column(column, overflow="fold")
    for status in contract.requirements:
        problem = f"[yellow]{escape(status.problem)}[/yellow]. {escape(status.fix or '')}" if status.problem else ""
        requirements.add_row(escape(status.requirement), escape(status.adapter or "-"), problem)
    return requirements


def _flows(contract: CompiledContract) -> Table:
    flows = Table(title="Data")
    for column in ("Kind", "Name", "What"):
        flows.add_column(column, overflow="fold")
    for name, read in contract.inputs.items():
        flows.add_row("input", escape(name), escape(read))
    for name, format_ in contract.outputs.items():
        flows.add_row("output", escape(name), f"{format_} draft, saved locally")
    for publish in contract.publishes:
        state = "" if publish.provided else " (not provided by an enabled module)"
        flows.add_row("publishes", escape(publish.output), escape(f"{publish.action}{state}: {publish.note}"))
    return flows


def _measurements(measurements: Measurements, qualified: str) -> RenderableType:
    """The success test: per draft, how long drafting took, what it cost and how much was changed before sending."""
    table = Table(title="Success test: drafting time and how much was changed")
    for column in ("Drafted (UTC)", "Drafting", "Cost", "Changed", "Editing"):
        table.add_column(column, overflow="fold")
    for run in measurements.recent:
        table.add_row(
            f"{run.created:%Y-%m-%d %H:%M}",
            f"{run.drafting_seconds:.0f}s" if run.drafting_seconds is not None else "-",
            f"{run.cost_usd:g} USD" if run.cost_usd is not None else "-",
            f"{run.changed:.0%}" if run.changed is not None else "not recorded",
            f"{run.edit_minutes:g} min" if run.edit_minutes is not None else "-",
        )
    median = measurements.median_drafting_seconds
    parts = [f"{measurements.drafts} draft(s)"]
    if median is not None:
        parts.append(f"median drafting {median:.0f}s")
    if measurements.mean_changed is not None:
        parts.append(f"{measurements.sent} sent, {measurements.mean_changed:.0%} of words changed on average")
    else:
        parts.append(f"none sent yet: `hz workflow sent {qualified} --file <what you sent>` records it")
    if measurements.total_edit_minutes is not None:
        parts.append(f"{measurements.total_edit_minutes:g} min editing in all")
    return Group(table, Text("; ".join(parts)))


def _agent(contract: CompiledContract) -> str:
    tools = ", ".join(contract.tools) or "the runner's read-only default"
    limit = f", at most {contract.max_usd:g} USD a run" if contract.max_usd is not None else ""
    timeout = f"{contract.timeout_seconds:.0f}s" if contract.timeout_seconds is not None else "-"
    return f"tools: {tools}; timeout {timeout}{limit}"


def render_check(report: CheckReport) -> RenderableType:
    """Every diagnostic as `file:line  CODE  field  message`, then a summary line."""
    errors = report.errors
    if not report.files and report.searched:
        return Group(
            Text("No contracts to check.", style="bold"),
            _directories("Searched", report.searched),
            _next([Step(command="hz workflow new my-first-workflow", why="write a starter contract, with comments")]),
        )
    summary = Text(
        f"{len(report.files)} contract(s) checked: {errors} error(s), {len(report.diagnostics) - errors} warning(s)",
        style="red" if errors else "green",
    )
    return Group(_diagnostics(report.diagnostics), summary) if report.diagnostics else summary


def render_new(result: NewContract) -> RenderableType:
    """Where the starter was written, and what to do with it."""
    if result.template is None:
        note = "Edit it: the comments say what each key and section is for. It is valid as written and has no schedule."
    elif result.unfilled:
        note = (
            f"From {result.template}. Fill the placeholders left ({', '.join(result.unfilled)}) where it says "
            "{{asset:...}}; `hz workflow check` lists them. It has no schedule until you add one."
        )
    else:
        note = f"From {result.template}, with every asset filled in. It has no schedule until you add one."
    return Group(
        Text(f"Wrote {result.file}", style="bold"),
        Text(note),
        _next(result.next),
        Text(f"Guide: {GUIDE}"),
    )


def render_sent(result: Sent) -> RenderableType:
    """How much of the draft changed before it was sent, and where that was recorded."""
    editing = f"; {result.edit_minutes:g} min editing" if result.edit_minutes is not None else ""
    return Group(
        Text(
            f"{result.changed:.0%} of the words changed: "
            f"{result.draft_words} drafted, {result.sent_words} sent{editing}",
            style="bold",
        ),
        Text(f"Draft: {result.draft}\nRecorded: {result.record} (the sent text isn't copied, only its hash)"),
        _next([Step(command=f"hz workflow show {result.contract}", why="drafting time and changes across the weeks")]),
    )


def render_playbooks(result: PlaybookList) -> RenderableType:
    """Each playbook: its asset types with their hints, its templates and its recommended settings."""
    if not result.playbooks:
        return Group(
            Text("No playbooks yet.", style="bold"),
            Text("Plugins bring playbooks: asset types to look for, template contracts and recommended settings."),
            _next(result.next),
        )
    parts: list[RenderableType] = []
    for playbook in result.playbooks:
        table = Table(title=Text(f"Playbook {playbook.module}: {playbook.summary}"))
        for column in ("Kind", "Name", "What"):
            table.add_column(column, overflow="fold")
        for asset in playbook.assets:
            hints = "; ".join(h.summary or h.read for h in asset.hints) or "no hints: you name it"
            kept = f" Kept true by {asset.kept_by}." if asset.kept_by else ""
            table.add_row("asset type", Text(asset.name), Text(f"{asset.description} Looks in: {hints}.{kept}"))
        for template in playbook.templates:
            fills = f" (assets: {', '.join(template.assets)})" if template.assets else ""
            table.add_row("template", Text(template.template), Text(f"{template.command}{fills}"))
        for default in playbook.defaults:
            table.add_row("recommends", Text(default.key), Text(f"{default.command}  (only if you agree)"))
        parts.append(table)
    return Group(*parts, _next(result.next))


def render_discovery(result: Discovery) -> RenderableType:
    """The candidates for each asset type, and hints that couldn't run."""
    parts: list[RenderableType] = []
    if result.candidates:
        table = Table(title="Candidates: fill a template with --asset <type>=<reference>")
        for column in ("Playbook", "Type", "Reference", "Title"):
            table.add_column(column, overflow="fold")
        for candidate in result.candidates:
            table.add_row(Text(candidate.playbook), Text(candidate.asset), Text(candidate.ref), Text(candidate.title))
        parts.append(table)
    elif not result.playbooks:
        parts.append(Text("No playbooks yet: plugins bring them.", style="bold"))
    else:
        parts.append(Text("No candidates found.", style="bold"))
    if result.problems:
        lines = Text()
        for problem in result.problems:
            lines.append(f"{problem.playbook} {problem.asset} ({problem.hint}): {problem.problem}\n", style="yellow")
        parts.append(lines)
    return Group(*parts, _next(result.next))


def render_drafts(result: DraftList) -> RenderableType:
    """The drafts newest first with their paths, or the newest draft's text; where they would be when there are none."""
    if result.latest is not None:
        return Text(result.latest)  # agent output: text, never markup
    if not result.drafts:
        return Group(
            Text(f"No drafts yet under {result.directory}", style="bold"),
            Text("A run saves its draft and a .record.json (the packet, input hashes and the agent's report) there."),
            _next(result.next),
        )
    table = Table(title="Drafts")
    for column in ("Contract", "Created (UTC)", "Draft", "Record"):
        table.add_column(column, overflow="fold")
    for draft in result.drafts:
        table.add_row(
            Text(draft.contract),
            f"{draft.created:%Y-%m-%d %H:%M:%S}",
            Text(f"{draft.draft} ({draft.bytes} bytes)"),
            Text(str(draft.record) if draft.record else "missing"),
        )
    return Group(table, _next(result.next))


def _diagnostics(diagnostics: list[Diagnostic]) -> RenderableType:
    lines = Text()  # Text, not markup: diagnostics quote the contract
    for diagnostic in diagnostics:
        lines.append(str(diagnostic) + "\n", style="red" if diagnostic.severity == "error" else "yellow")
    return lines


def render_run(result: RunResult) -> RenderableType:
    """A dry run's packet as the agent would get it, or where the draft was saved."""
    if result.packet is not None:
        packet = result.packet
        return Group(
            Text(f"Packet of {packet.contract} (dry run: no agent ran)", style="bold"),
            Text("--- instructions (system prompt) ---", style="dim"),
            Text(packet.instructions),
            Text("--- prompt ---", style="dim"),
            Text(packet.prompt),
            Text(f"Dry run: {result.note}" if result.note else ""),
        )
    rendered = result.rendered
    if rendered is None:  # pragma: no cover - a run result always has one or the other
        return Text("")
    agent = rendered.agent
    cost = f", {agent.usage.cost_usd} USD" if agent.usage.cost_usd is not None else ""
    awaiting = (
        f"\nAwaiting approval (hz inbox, not built yet): {', '.join(rendered.awaiting)}" if rendered.awaiting else ""
    )
    note = f"\n{result.note}" if result.note else ""
    return Text(
        f"Drafted {rendered.contract}: {rendered.draft}\n"
        f"Agent: {agent.runner} {agent.model or ''} in {agent.duration_seconds:.0f}s{cost}{awaiting}{note}"
    )


def render_prune(report: PruneReport) -> RenderableType:
    """What was deleted, or would be in a dry run, with the cutoff; that nothing is when drafts are kept forever."""
    if report.cutoff is None:
        return Group(Text("Drafts and records are kept forever ([workflow] retention_days = 0).\n"), _next(report.next))
    verb = "Would delete" if report.dry_run else "Deleted"
    summary = Text(
        f"{verb} {report.drafts} draft(s) and {report.records} record(s), {report.bytes} bytes, rendered before "
        f"{report.cutoff:%Y-%m-%d %H:%M} UTC ({report.retention_days} days) under {report.directory}. "
        "Receipts are kept.\n"
    )
    files = Text("".join(f"  {path}\n" for path in report.files))  # paths name contracts: text, never markup
    problems = Text("".join(f"{problem}\n" for problem in report.problems), style="red")
    return Group(summary, files, problems, _next(report.next))
