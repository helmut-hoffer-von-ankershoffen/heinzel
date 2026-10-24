"""How the CLI head presents plugin results."""

from rich.console import Group, RenderableType
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from heinzel.modules.system.plugin._models import PluginSearch

RIGHTS = (
    "Plugins run with your rights. Community plugins are not reviewed by the Heinzelmännchen maintainers; "
    "add only those you trust (SECURITY.md)."
)


def render_search(result: PluginSearch) -> RenderableType:
    """A table of installable plugins, then any warnings and a reminder that plugins run with your rights."""
    table = Table(title="Installable plugins", expand=True)
    table.add_column("Plugin", overflow="fold")
    table.add_column("Stars", justify="right")
    table.add_column("Description", overflow="fold", ratio=2)
    table.add_column("hz system plugin add …", overflow="fold", ratio=3)
    for plugin in result.plugins:
        source = "[yellow]community, GitHub[/yellow]" if plugin.community else "[green]first-party[/green]"
        install = "(installed)" if plugin.installed else plugin.install_spec
        stars = "" if plugin.stars is None else str(plugin.stars)
        table.add_row(f"{escape(plugin.name)}\n{source}", stars, Text(plugin.description), Text(install))
    parts: list[RenderableType] = [table]
    parts.extend(Text(f"Warning: {warning}", style="yellow") for warning in result.warnings)
    parts.append(Text(RIGHTS, style="dim"))
    return Group(*parts)
