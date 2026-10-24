"""How the CLI head presents system results."""

import json
from typing import Any, cast

from rich.console import Group, RenderableType
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from heinzel.modules.system._models import ModuleInfo, ReleaseCheck, SystemInfo


def render_modules(modules: list[ModuleInfo]) -> RenderableType:
    """A table of modules and their lets."""
    table = Table(title="Modules")
    for column in ("Module", "Summary", "Distribution", "Lets"):
        table.add_column(column, overflow="fold")
    for info in modules:
        lets = "\n".join(f"{kind}: {', '.join(names)}" for kind, names in info.lets.items())
        distribution = "built-in" if info.builtin else f"{info.distribution} {info.version}"
        table.add_row(Text(info.qualname), Text(info.summary), Text(distribution), Text(lets))
    return table


def render_info(info: SystemInfo) -> RenderableType:
    """An overview panel, the modules, plugin errors and the module sections."""
    overview = Table(show_header=False, box=None)
    overview.add_column(style="bold cyan", overflow="fold")
    overview.add_column(overflow="fold")
    overview.add_row("Heinzelmännchen", info.version)
    overview.add_row("Python", f"{info.python_version} ({info.python_executable})")
    overview.add_row("Platform", info.platform)
    for key, value in info.settings.items():
        overview.add_row(f"HZ_{key.upper()}", Text(json.dumps(value) if not isinstance(value, str) else value))
    parts: list[RenderableType] = [Panel(overview, title="Heinzelmännchen", expand=False), render_modules(info.modules)]
    parts.extend(
        Panel(Text(error, style="red"), title=escape(f"Plugin failed to load: {name}"), expand=False)
        for name, error in info.plugin_errors.items()
    )
    for qualname, section in info.sections.items():
        table = Table(show_header=False, box=None)
        table.add_column(style="bold cyan", overflow="fold")
        table.add_column(overflow="fold")
        rows = cast("dict[str, Any]", section) if isinstance(section, dict) else {"value": section}
        for key, value in rows.items():
            table.add_row(Text(str(key)), Text(value if isinstance(value, str) else json.dumps(value)))
        parts.append(Panel(table, title=qualname, expand=False))
    return Group(*parts)


def render_release(check: ReleaseCheck) -> RenderableType:
    """One line: the update and where to read about it, that this is the latest, or why nobody could tell."""
    if check.latest is None:
        return Text("\n".join(f"Warning: {warning}" for warning in check.warnings), style="yellow")
    if check.update_available:
        notes = f"\n{check.url}" if check.url else ""
        return Text(f"v{check.latest} is available (installed: {check.current}): hz system update{notes}")
    return Text(f"hz {check.current} is up to date; the latest release is {check.latest}.")
