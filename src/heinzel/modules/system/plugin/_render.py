"""How the CLI head presents plugin results."""

from rich.console import Group, RenderableType
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from heinzel.modules.system.plugin._models import PluginChange, PluginInfo, PluginSearch

RIGHTS = (
    "Plugins run with your rights. Community plugins are not reviewed by the Heinzelmännchen maintainers; "
    "add only those you trust (SECURITY.md)."
)
RESTART = "Every new hz command sees it now; restart a running `hz ui` or MCP server to load it there."


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


def render_list(plugins: list[PluginInfo]) -> RenderableType:
    """A table of installed plugins: version, source, whether enabled, and what each brings."""
    if not plugins:
        return Text("No plugins installed. Find some with `hz system plugin search`.")
    table = Table(title="Installed plugins", expand=True)
    table.add_column("Plugin", overflow="fold")
    table.add_column("Version")
    table.add_column("Source", overflow="fold", ratio=2)
    table.add_column("Brings", overflow="fold", ratio=2)
    for plugin in plugins:
        state = "[green]enabled[/green]" if plugin.enabled else "[yellow]disabled[/yellow]"
        if plugin.error:
            state = "[red]fails to load[/red]"
        origin = "first-party" if plugin.first_party else (plugin.source_kind or "unknown")
        brings = [
            *plugin.modules,
            *plugin.capabilities,
            *(f"layer {layer.module}" for layer in plugin.layers),
            *(f"theme {theme}" for theme in plugin.themes),
        ]
        table.add_row(
            f"{escape(plugin.distribution)}\n{state}",
            plugin.version,
            Text(f"{origin}: {plugin.source_url}" if plugin.source_url else origin),
            Text(", ".join(brings)),
        )
    return table


def render_change(change: PluginChange) -> RenderableType:
    """For `add` without confirming: what would be installed and the command that confirms; otherwise what was done."""
    parts: list[RenderableType] = []
    if (candidate := change.candidate) is not None and not change.done:
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold")
        table.add_column(overflow="fold")
        rows = {
            "Plugin": candidate.distribution or "unknown until installed",
            "Version": candidate.version or "unknown",
            "Publisher": candidate.publisher or "unknown",
            "Source": f"{'first-party, ' if candidate.first_party else ''}{candidate.source}: {candidate.url}",
            "Installs": candidate.requirement,
            "Summary": candidate.summary,
        }
        if candidate.private:
            rows["Visibility"] = "private repository, read with your gh login"
        if candidate.installed_version:
            rows["Replaces"] = candidate.installed_version
        for label, value in rows.items():
            if value:
                table.add_row(label, Text(value))
        parts.append(table)
        parts.extend(Text(f"Warning: {warning}", style="yellow") for warning in candidate.warnings)
        parts.append(Text(RIGHTS, style="dim"))
        parts.extend(Text(f"To install it: {command}") for command in change.next)
        return Group(*parts)
    done = {"add": "Added", "develop": "Added", "remove": "Removed", "enable": "Enabled", "disable": "Disabled"}
    parts.append(Text(f"{done[change.action]} {change.distribution or 'the plugin'}."))
    if change.restart_required:
        parts.append(Text(RESTART, style="dim"))
    parts.extend(Text(f"Undo with: {command}", style="dim") for command in change.next)
    return Group(*parts)
