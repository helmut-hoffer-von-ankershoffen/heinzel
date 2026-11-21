"""How the CLI head presents system results."""

import json
from typing import TYPE_CHECKING, Any, cast

from rich.console import Group, RenderableType
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from heinzel.modules.system._models import ChannelReport, ModuleInfo, ReleaseCheck, SystemInfo, UpdateResult
from heinzel.modules.system._plugin_standing import standing

if TYPE_CHECKING:
    from heinzel.modules.system._models import ToolReference
else:
    try:
        from heinzel.modules.system._models import ToolReference
    except ImportError:  # an hz updated in place renders its update with its older, loaded models, which lack this one
        ToolReference = Any

if TYPE_CHECKING:  # read only through attributes: an hz updated in place renders with the old models (see below)
    from heinzel.kernel import HeadRefresh
    from heinzel.modules.system._models import AutomaticUpdates, PluginUpdate, UpdatePlan


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
    """What the update did as one checklist and a summary line, or the plan a preview shows.

    The hz that ran `hz system update` imports this file only after uv replaced it with the new release's, while it
    still holds its own `_models` in memory. So it names no model that is new, and reads a field that is new with a
    default: an older hz updating itself must still print its result.
    """
    plan = getattr(result, "plan", None)
    if getattr(result, "previewed", False):
        shown = [plan_text(plan)] if plan is not None else []
        return Text("\n".join([*shown, result.message, *(f"To update: {command}" for command in result.next)]))
    if not hasattr(result, "after"):  # a result of an hz too old to say what it changed: its message, its plugins
        plugins = [f"  {p.distribution}: {standing(p)}" for p in result.plugins]
        return Text("\n".join([result.message, *(["Plugins:", *plugins] if plugins else [])]))
    rows = update_rows(result)
    width = max([27, *(len(label) + 2 for _, label, _ in rows)])
    lines = [f" {mark} {label:<{width}}{detail}".rstrip() for mark, label, detail in rows]
    if not result.updated and getattr(result, "next", []):
        lines += result.message.splitlines()[1:]
    lines.append(_summary(result, warned=any(mark != CHECK for mark, _, _ in rows)))
    lines += [f"To update: {command}" for command in getattr(result, "next", [])]
    automatic = getattr(result, "automatic", None)
    if result.updated and automatic is not None:
        lines.append(_automatic(automatic))
    return Text("\n".join(lines))


CHECK, WARN, CROSS = "✓", "!", "✗"
SETTLED = frozenset({"verified", "not-installed", "self"})


def update_rows(result: UpdateResult) -> list[tuple[str, str, str]]:
    """The checklist as (mark, label, detail): hz, plugins that changed or need a word, the rest as one, the rest."""
    moved = result.updated and result.after != result.before
    rows: list[tuple[str, str, str]] = []
    heads = getattr(result, "heads", [])
    if not result.updated and getattr(result, "next", []):  # left pinned: the message says how to move it
        rows.append((WARN, f"hz {result.after}", result.message.splitlines()[0]))
    elif moved:
        rows.append((CHECK, "hz", f"{result.before} → {result.after}"))
    elif result.updated and result.message.startswith("Reinstalled"):
        rows.append((CHECK, f"hz {result.after}", "reinstalled"))
    else:
        rows.append((CHECK, f"hz {result.after}", "newest release"))
    quiet = 0
    for update in result.plugins:
        if update.status == "upgraded":
            rows.append((CHECK, update.distribution, f"{update.before or '?'} → {update.after or '?'}"))
        elif update.status == "unchanged" and getattr(update, "checked", True):
            if getattr(update, "pinned", None) is None:
                quiet += 1
            else:  # held where it is on purpose: a row of its own, no warning
                rows.append((CHECK, update.distribution, _plugin_word(update)))
        else:
            rows.append((WARN, update.distribution, _plugin_word(update)))
    if quiet:
        count = f"{quiet} plugin{'' if quiet == 1 else 's'}"
        rows.append((CHECK, count, "already newest" if len(rows) > 1 else "all newest"))
    services = [head for head in heads if head.head in {"daemon", "web"} and head.status != "not-installed"]
    app = next((head for head in heads if head.head == "desktop"), None)
    ends = [_services_row(services, result.after)] if services else []
    if app is not None:  # checked first when nothing was installed; after the services restarted when something was
        ends = [*ends, _app_row(app, result.after)] if result.updated else [_app_row(app, result.after), *ends]
    return [*rows, *ends]


def _plugin_word(update: "PluginUpdate") -> str:
    """Why a plugin has a row of its own although nothing moved: held back, taken out, failed or not asked."""
    if update.status == "unchanged":
        return standing(update).removesuffix(".")
    word = (
        "taken out: it does not allow this hz yet"
        if update.disabled
        else f"held back at {update.before or 'its version'}: it does not allow the new hz yet"
    )
    return f"{word}; `{update.next[0]}`" if update.next else word


def _services_row(services: "list[HeadRefresh]", after: str) -> tuple[str, str, str]:
    """The daemon and web server as one row: restarted or already running the new version, or what finishes it."""
    label = "Daemon, web server"
    bad = next((head for head in services if head.status not in SETTLED), None)
    if bad is not None:
        return (WARN if bad.status == "pending" else CROSS), label, bad.detail.removesuffix(".")
    ran = f"run {services[0].version or after}"
    restarted = any(getattr(head, "restarted", None) for head in services)
    return CHECK, label, f"restarted, {ran}" if restarted else ran


def _app_row(head: "HeadRefresh", after: str) -> tuple[str, str, str]:
    """The desktop app's row: its versions and whether it was quit and reopened, or the warning that finishes it."""
    detail = head.detail.removesuffix(".")
    before, running = getattr(head, "before", None), getattr(head, "was_running", None)
    if head.status == "failed":
        return CROSS, "Desktop app", detail
    if head.status != "verified":
        return WARN, "Desktop app" + (f" {before}" if before else ""), detail
    if not detail.startswith("Updated") and (before is None or before == after):
        return CHECK, f"Desktop app {after}", "newest, running" if running else "newest"
    words = (f"{before} → {after}" if before else f"updated to {after}") + (
        ", quit and reopened" if getattr(head, "reopened", None) else ""
    )
    return CHECK, "Desktop app", words


def _summary(result: UpdateResult, *, warned: bool) -> str:
    """The one line under the checklist."""
    if warned:
        return "Finished with warnings: the lines marked ! say what finishes the job."
    if not result.updated:
        return "Everything is up to date."
    seconds = getattr(result, "seconds", None)
    took = f" in {seconds:.0f} s" if seconds is not None else ""
    return f"Updated to hz {result.after}{took}. What's new: hz system changelog --newer"


def plan_text(plan: "UpdatePlan") -> str:
    """The plan in plain lines: hz, each plugin with where it goes or why it stays, the desktop app."""
    lines = [plan.core]
    if plan.plugins:
        lines.append("Plugins:")
    for plugin in plan.plugins:
        version = plugin.installed or "version unknown"
        head = {
            "update": f"{version} → {plugin.target}",
            "current": f"already newest ({version})",
            "refresh": f"asked again ({version})",
            "kept": f"kept at {version}",
        }[plugin.action]
        lines.append(f"  {plugin.distribution}: {head}{f'. {plugin.detail}' if plugin.detail else ''}")
    if plan.desktop:
        lines.append(
            "Desktop app: updated with `hz app install`"
            + (", quit and reopened because it is open." if getattr(plan, "desktop_running", False) else ".")
        )
    if getattr(plan, "daemon", False):
        lines.append("Daemon, web server: restarted when something changed or the daemon runs older code.")
    return "\n".join(lines)


def render_plan(plan: "UpdatePlan") -> RenderableType:
    """What the update would change, before the person is asked."""
    return Text(plan_text(plan))


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


def render_tools(reference: ToolReference) -> RenderableType:
    """The reference as plain text, unwrapped, so it pipes into a file."""
    return Text(reference.text.rstrip("\n"), no_wrap=True, overflow="ignore")
