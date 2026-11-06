"""How the CLI head presents prerequisites; as text, never markup, since owners and plugins supply the words."""

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from heinzel import Homebrew, PrerequisiteDefinition, PrerequisiteReport


def _macos(definition: PrerequisiteDefinition) -> str:
    recipe = definition.macos
    if isinstance(recipe, Homebrew):
        return f"brew install {'--cask ' if recipe.cask else ''}{recipe.package}"
    return recipe.text if recipe is not None else "-"


def render_list(definitions: list[PrerequisiteDefinition]) -> RenderableType:
    """One row per declared tool: who needs it, when, and how it gets installed."""
    if not definitions:
        return Text("No enabled module declares an external tool.")
    table = Table(title="Prerequisites")
    for column in ("Owner", "Tool", "Needed", "macOS", "Linux"):
        table.add_column(column, overflow="fold")
    for item in definitions:
        needed = item.condition or ("always" if item.when != "feature" else "when asked for")
        table.add_row(
            Text(item.module),
            Text(f"{item.title} ({item.name})"),
            Text(needed),
            Text(_macos(item)),
            Text(item.linux.text),
        )
    return table


def render_report(report: PrerequisiteReport) -> RenderableType:
    """One row per tool with its state; installing or finding a tool never signs anyone in."""
    shown = [check for check in report.checks if check.status != "skipped"]
    skipped = [check for check in report.checks if check.status == "skipped"]
    note = Text(
        "Not needed for this selection: " + ", ".join(check.requirement.name for check in skipped) + ".", style="dim"
    )
    if not shown:
        return Group(Text("No tool is needed for this selection."), note) if skipped else Text("No tool is needed.")
    table = Table(title="Prerequisites" + ("" if report.ready else " (not ready)"))
    for column in ("Owner", "Tool", "Status", "Version", "Detail"):
        table.add_column(column, overflow="fold")
    for check in shown:
        requirement = check.requirement
        table.add_row(
            Text(requirement.module),
            Text(requirement.name),
            Text(check.status),
            Text(check.version or ""),
            Text(check.detail or check.path or ""),
        )
    return Group(table, note) if skipped else table
