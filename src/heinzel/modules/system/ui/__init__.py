"""The ui module of system: what modules contribute to the web app that `hz ui` serves (ADR 0056).

Modules add status items (`module.status`), header actions (`module.action`) and Today widgets (`module.widget`) as
data: pure views over their own read-only operations, in closed shapes the app renders itself. These operations list
them with their values, so the CLI and agents see what the app shows; the app asks for each by `--item` at its own
refresh, so one slow or failing contribution never holds up the rest.
"""

from collections.abc import Callable
from typing import Annotated

from pydantic import Field
from rich.console import RenderableType
from rich.table import Table
from rich.text import Text

from heinzel import (
    CardWidget,
    Check,
    ListWidget,
    LiveClock,
    LiveTime,
    LiveUntil,
    MetricWidget,
    Module,
    Navigate,
    OpenUrl,
    ProgressWidget,
    RunCommand,
    RunOperation,
    Status,
    StatusItem,
)
from heinzel.kernel import Effect
from heinzel.modules.system.ui._models import (
    ActionEntry,
    ContributionInfo,
    Entry,
    StatusEntry,
    UiSettings,
    WidgetEntry,
)
from heinzel.modules.system.ui._service import UiService

module = Module("ui", "List what modules add to the web app: status items, header actions and Today widgets.")
module.settings(UiSettings)
module.service(UiService)

# What a contribution shows can be as private as its source (a meeting, a profile): the CLI, MCP and the web head,
# behind its token, get it; the unauthenticated REST head doesn't.
HEADS = {"cli", "mcp", "web"}
Item = Annotated[str | None, Field(description="Only this one, by id (`<module>:<name>`), e.g. `clock:primary-zone`.")]


def _status_text(item: StatusItem) -> str:
    live = item.live
    when = ""
    if isinstance(live, LiveClock):
        when = f"(now in {live.zone})"
    elif isinstance(live, LiveTime | LiveUntil):
        when = live.at.strftime("%a %H:%M")
    return " ".join(part for part in (item.text, when) if part)


def _table[E: Entry](entries: list[E], shows: Callable[[E], str]) -> RenderableType:
    if not entries:
        return Text("Nothing is contributed; plugins add items with module.status, module.action and module.widget.")
    table = Table(box=None, pad_edge=False)
    for column in ("Id", "Shows", "Every"):
        table.add_column(column, overflow="fold")
    for entry in entries:
        shown = Text(shows(entry)) if entry.problem is None else Text(entry.problem, style="yellow")
        table.add_row(entry.id, shown, f"{entry.refresh}s")
    return table


def render_status(entries: list[StatusEntry]) -> RenderableType:
    """Each status item as it reads, or its problem."""
    return _table(entries, lambda e: _status_text(e.item) if e.item else "(nothing now)")


def _does(effect: Effect | None) -> str:
    if isinstance(effect, Navigate):
        return effect.path
    if isinstance(effect, OpenUrl):
        return effect.url
    if isinstance(effect, RunCommand):
        return effect.command
    if isinstance(effect, RunOperation) and effect.call is not None:
        return effect.call.command
    return ""


def render_actions(entries: list[ActionEntry]) -> RenderableType:
    """Each action's label, what it does and its badge."""

    def shows(entry: ActionEntry) -> str:
        badge = f" [{entry.badge}]" if entry.badge not in {None, 0} else ""
        return f"{entry.label}{badge}: {_does(entry.effect)}"

    return _table(entries, shows)


def render_widgets(entries: list[WidgetEntry]) -> RenderableType:
    """Each widget's kind and title, or its problem."""

    def shows(entry: WidgetEntry) -> str:
        widget = entry.widget
        if isinstance(widget, MetricWidget):
            return f"{widget.label}: {widget.value}" + (f" ({widget.delta})" if widget.delta else "")
        if isinstance(widget, ProgressWidget):
            return f"{widget.label}: {widget.done}/{widget.total}"
        if isinstance(widget, CardWidget | ListWidget):
            return f"{widget.kind}: {widget.title}"
        return "(nothing now)"

    return _table(entries, shows)


@module.operation(render=render_status, heads=HEADS)
def status(service: UiService, item: Item = None) -> list[StatusEntry]:
    """List the status items modules add to the web app's status bar, with what each shows now.

    Raises:
        HeinzelError: If `item` names no status item.
    """
    return service.status(item)


@module.operation(render=render_actions, heads=HEADS)
def actions(service: UiService, item: Item = None) -> list[ActionEntry]:
    """List the buttons modules add to the web app's header, with what each does and its badge.

    Raises:
        HeinzelError: If `item` names no action.
    """
    return service.actions(item)


@module.operation(render=render_widgets, heads=HEADS)
def widgets(service: UiService, item: Item = None) -> list[WidgetEntry]:
    """List the widgets modules add to the web app's Today screen, with what each shows now.

    Raises:
        HeinzelError: If `item` names no widget.
    """
    return service.widgets(item)


def render_listing(found: list[ContributionInfo]) -> RenderableType:
    """Each contribution and where it shows."""
    if not found:
        return Text("Nothing is contributed; plugins add items with module.status, module.action and module.widget.")
    table = Table(box=None, pad_edge=False)
    for column in ("Id", "Kind", "Title", "Shows in"):
        table.add_column(column, overflow="fold")
    for info in found:
        where = ", ".join(f"{slot} {'on' if on else 'off'}" for slot, on in info.slots.items())
        table.add_row(info.id, info.kind, info.title, where)
    return table


Slot = Annotated[
    str | None,
    Field(description="Where: `footer`, `sidebar`, `header` or `today`; needed when it may show in several."),
]
ItemId = Annotated[str, Field(description="The contribution, by id (`<module>:<name>`), e.g. `clock:primary-zone`.")]


@module.operation(name="list", render=render_listing, heads=HEADS)
def list_(service: UiService) -> list[ContributionInfo]:
    """List every contribution to the web app, where it may show and whether it does; nothing runs."""
    return service.listing()


@module.operation(read_only=False, render=render_listing, heads=HEADS)
def show(service: UiService, item: ItemId, slot: Slot = None) -> list[ContributionInfo]:
    """Show a contribution in a slot of the web app, such as the clock in the sidebar.

    Raises:
        HeinzelError: If no contribution has that id, or it can't show in that slot.
    """
    return service.show(item, slot, shown=True)


@module.operation(read_only=False, render=render_listing, heads=HEADS)
def hide(service: UiService, item: ItemId, slot: Slot = None) -> list[ContributionInfo]:
    """Hide a contribution from a slot of the web app, such as the clock from the footer.

    Raises:
        HeinzelError: If no contribution has that id, or it can't show in that slot.
    """
    return service.show(item, slot, shown=False)


@module.health
def contributions(service: UiService) -> list[Check]:
    """Every contribution's source and effect can work; a declaration that can't is degraded, naming why."""
    problems = service.problems()
    if not problems:
        return [Check(name="contributions", status=Status.UP, detail="every contribution can work")]
    return [
        Check(
            name=f"contribution {key}",
            status=Status.DEGRADED,
            detail=problem,
            fix="Fix the declaration in its module, or turn the module off: `hz system plugin disable <plugin>`.",
        )
        for key, problem in problems
    ]


__all__ = ["ActionEntry", "ContributionInfo", "StatusEntry", "UiService", "UiSettings", "WidgetEntry", "module"]
