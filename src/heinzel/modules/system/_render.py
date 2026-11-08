"""How the CLI head presents system results."""

import json
from typing import TYPE_CHECKING, Any, cast

from rich.console import Group, RenderableType
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from heinzel.modules.system._models import ChannelReport, ModuleInfo, ReleaseCheck, SystemInfo, UpdateResult

if TYPE_CHECKING:  # read only through attributes: an hz updated in place renders with the old models (see below)
    from heinzel.modules.system._models import AutomaticUpdates, PluginUpdate


def render_modules(modules: list[ModuleInfo]) -> RenderableType:
    """A table of modules and their lets."""
    table = Table(title="Modules")
    for column in ("Module", "Summary", "Distribution", "Lets"):
        table.add_column(column, overflow="fold")
    for info in modules:
        lets = "\n".join(f"{kind}: {', '.join(names)}" for kind, names in info.lets.items())
        distribution = "built-in" if info.builtin else f"{info.distribution} {info.version}"
        if info.repository and not info.builtin:
            distribution += f"\n{info.repository}"
        table.add_row(Text(info.qualname), Text(info.summary), Text(distribution), Text(lets))
    return table


def render_info(info: SystemInfo) -> RenderableType:
    """An overview panel, the modules, plugin errors and the module sections."""
    overview = Table(show_header=False, box=None)
    overview.add_column(style="bold cyan", overflow="fold")
    overview.add_column(overflow="fold")
    overview.add_row("Heinzelmännchen", info.version)
    if info.repository:
        overview.add_row("Repository", Text(info.repository))
    if info.latest_release:
        overview.add_row("Latest release", info.latest_release)
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
        return Text(
            f"hz {check.latest} is available (installed {check.current}): hz system update\n"
            f"What's new: hz system changelog --newer{notes}"
        )
    return Text(f"hz {check.current} is up to date; the latest release is {check.latest}.")


def render_channel(report: ChannelReport) -> RenderableType:
    """The channel and what a switch changes, with how to confirm one that hasn't happened."""
    done = f"Switched to {report.channel}." if report.changed else ""
    return Text("\n".join(filter(None, [report.statement, done])))


def render_update(result: UpdateResult) -> RenderableType:
    """What the update did, or what the installation is pinned to and the command that moves it.

    The hz that ran `hz system update` imports this file only after uv replaced it with the new release's, while it
    still holds its own `_models` in memory. So it names no model that is new, and reads a field that is new with a
    default: an older hz updating itself must still print its result.
    """
    lines = [result.message]
    unchanged = [p for p in result.plugins if p.status == "unchanged"]
    if unchanged:
        lines.append("Plugins:")
        lines += [f"  {p.distribution}: {standing(p)}" for p in unchanged]
    automatic = getattr(result, "automatic", None)
    if result.updated and automatic is not None:
        lines.append(_automatic(automatic))
    return Text("\n".join(lines))


def standing(update: "PluginUpdate") -> str:
    """Where an `unchanged` plugin stands, saying only what is known: newest only when nothing pins it."""
    version = update.after or "version unknown"
    if not getattr(update, "checked", True):
        return f"not updated ({version}): the plugin update stopped before it; newer releases not checked"
    pinned = getattr(update, "pinned", None)
    if pinned is not None:
        return f"kept at {version} (pinned to {pinned}; newer releases not checked)"
    return f"already newest ({version})"


def _automatic(automatic: "AutomaticUpdates") -> str:
    """What automatic updates install and when, whether they are on, and how to turn them on."""
    rule = (
        f"The web server checks about every {automatic.check_every_minutes} minutes and installs the newest "
        f"published stable release (not a prerelease) once it is {automatic.core_min_age_hours} h old (plugins "
        f"{automatic.plugin_min_age_hours} h). An update run by hand installs that same release now."
    )
    if automatic.enabled:
        return f"Automatic updates: on. {rule}"
    return f"Automatic updates: off. Turn them on with `hz system config set auto_update true`. {rule}"
