"""How the CLI head presents workflow contracts."""

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from heinzel.modules.workflow._models import CheckReport, CompiledContract, ContractSummary, Diagnostic, RunResult


def render_list(contracts: list[ContractSummary]) -> RenderableType:
    """One row per contract: where it is from, its schedule and whether it is valid and scheduled."""
    if not contracts:
        return Text("No contracts found. Put <name>.prose.md files in your workflows directory (`hz workflow check`).")
    table = Table(title="Workflow contracts")
    for column in ("Contract", "Title", "Schedule", "Daemon", "Problems"):
        table.add_column(column, overflow="fold")
    for contract in contracts:
        problems = f"{contract.errors} error(s)" if contract.errors else ""
        if contract.warnings:
            problems = ", ".join(filter(None, [problems, f"{contract.warnings} warning(s)"]))
        table.add_row(
            f"{contract.namespace}:{contract.name}",
            contract.title or "-",
            contract.schedule or "by hand",
            "scheduled" if contract.scheduled else "-",
            f"[red]{problems}[/red]" if contract.errors else problems,
        )
    return table


def render_show(contract: CompiledContract) -> RenderableType:
    """The compiled view: schedule, requirements with their adapters, inputs, outputs, publishes and limits."""
    overview = Table.grid(padding=(0, 2))
    overview.add_column(style="bold")
    overview.add_column(overflow="fold")
    overview.add_row("Contract", f"{contract.namespace}:{contract.name} ({contract.file})")
    overview.add_row("Title", contract.title or "-")
    overview.add_row("Schedule", contract.schedule or "by hand")
    overview.add_row("Daemon job", contract.job or f"none: {contract.why_not_scheduled}")
    overview.add_row("Agent", _agent(contract))
    parts: list[RenderableType] = [overview]
    if contract.requirements:
        requirements = Table(title="Requires")
        for column in ("Requirement", "Adapter", "Problem"):
            requirements.add_column(column, overflow="fold")
        for status in contract.requirements:
            problem = f"[yellow]{status.problem}[/yellow]. {status.fix}" if status.problem else ""
            requirements.add_row(status.requirement, status.adapter or "-", problem)
        parts.append(requirements)
    if contract.inputs or contract.outputs or contract.publishes:
        flows = Table(title="Data")
        for column in ("Kind", "Name", "What"):
            flows.add_column(column, overflow="fold")
        for name, read in contract.inputs.items():
            flows.add_row("input", name, read)
        for name, format_ in contract.outputs.items():
            flows.add_row("output", name, f"{format_} draft, saved locally")
        for publish in contract.publishes:
            state = "" if publish.provided else " (not provided by an enabled module)"
            flows.add_row("publishes", publish.output, f"{publish.action}{state}: {publish.note}")
        parts.append(flows)
    if contract.diagnostics:
        parts.append(_diagnostics(contract.diagnostics))
    return Group(*parts)


def _agent(contract: CompiledContract) -> str:
    tools = ", ".join(contract.tools) or "the runner's read-only default"
    limit = f", at most {contract.max_usd} USD" if contract.max_usd is not None else ""
    timeout = f"{contract.timeout_seconds:.0f}s" if contract.timeout_seconds is not None else "-"
    return f"tools: {tools}; timeout {timeout}{limit}"


def render_check(report: CheckReport) -> RenderableType:
    """Every diagnostic as `file:line  CODE  field  message`, then a summary line."""
    errors = report.errors
    summary = Text(
        f"{len(report.files)} contract(s) checked: {errors} error(s), {len(report.diagnostics) - errors} warning(s)",
        style="red" if errors else "green",
    )
    return Group(_diagnostics(report.diagnostics), summary) if report.diagnostics else summary


def _diagnostics(diagnostics: list[Diagnostic]) -> RenderableType:
    lines = Text()
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
        )
    rendered = result.rendered
    if rendered is None:  # pragma: no cover - a run result always has one or the other
        return Text("")
    agent = rendered.agent
    cost = f", {agent.usage.cost_usd} USD" if agent.usage.cost_usd is not None else ""
    awaiting = (
        f"\nAwaiting approval (hz inbox, not built yet): {', '.join(rendered.awaiting)}" if rendered.awaiting else ""
    )
    return Text(
        f"Drafted {rendered.contract}: {rendered.draft}\n"
        f"Agent: {agent.runner} {agent.model or ''} in {agent.duration_seconds:.0f}s{cost}{awaiting}"
    )
