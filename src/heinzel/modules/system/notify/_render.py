"""How the CLI head presents notices."""

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from heinzel import Notice
from heinzel.modules.system.notify._models import Installed, NoticeList, Tested


def _state(notice: Notice) -> str:
    if notice.delivered_by is not None:
        return f"shown ({notice.delivered_by.value})"
    if not notice.popup:
        return f"in the list only: {notice.held}"
    if notice.due > notice.at:
        return f"waits until {notice.due:%a %H:%M}, the start of your working hours"
    return "waits to pop up"


def render_list(result: NoticeList) -> RenderableType:
    """One row per notice, newest first, and how notices reach you."""
    footer = Text(f"Outside the web app: {result.native}.", style="dim")
    if result.waiting:
        footer = Text(
            f"{result.waiting} waiting: the daemon pops them up at its next tick (`hz system daemon status`). "
            f"Outside the web app: {result.native}.",
            style="dim",
        )
    if not result.notices:
        return Group(Text("No notices yet. `hz system notify test` shows how one reaches you."), footer)
    table = Table(title="Notices")
    for column in ("Id", "When", "Kind", "What", "State", "Command"):
        table.add_column(column, overflow="fold")
    for notice in result.notices:
        # Text, not markup: titles quote contract names and errors.
        table.add_row(
            Text(notice.id),
            Text(f"{notice.at:%Y-%m-%d %H:%M}"),
            Text(f"{notice.kind.value}{' !' if notice.urgent else ''}"),
            Text(f"{notice.title}\n{notice.body}".strip()),
            Text(_state(notice)),
            Text(notice.command),
        )
    return Group(table, footer)


def render_installed(result: Installed) -> RenderableType:
    """What was installed or updated, whether the test went out, and what to do if no notification appeared."""
    lines: list[Text] = []
    if result.notifier is None:
        lines.append(Text(f"No native notifier: {result.problem}"))
    elif result.notifier != "heinzel-notifier":
        lines.append(Text(f"Using {result.notifier} for notifications."))
    elif result.state == "current":
        lines.append(Text("Heinzel Notifier is installed and up to date."))
    elif result.state == "installed":
        done = (
            "Updated Heinzel Notifier (new icon)." if result.previous == "outdated" else "Installed Heinzel Notifier."
        )
        lines.append(Text(done))
    else:
        lines.append(Text(f"Couldn't update Heinzel Notifier: {result.detail or 'it differs from the bundled app'}."))
    if result.notifier is not None:
        sent = "Sent a test notification \u2014 you should see it now."
        lines.append(Text(sent if result.shown else f"The test notification failed: {result.problem}"))
    lines.extend(Text(step) for step in result.next)
    return Group(*lines)


def render_tested(result: Tested) -> RenderableType:
    """What was recorded, and how the native pop-up went."""
    lines = [Text(f"Recorded {result.notice.id}.")]
    for channel in result.channels:
        mark = "✓" if channel.state in {"shown", "ready"} else "✗"
        line = Text(f"{mark} {CHANNELS[channel.name]}: {channel.state}, {channel.detail}")
        if channel.command:
            line.append(f" → {channel.command}", style="bold")
        lines.append(line)
    if result.native == "shown":
        lines.append(Text("A test notification is on its way."))
    elif not result.pops_up:
        lines.append(Text("Nothing will pop it up: fix one of the above, then try again.", style="bold red"))
    else:
        lines.append(Text("The open web app or the daemon's next tick pops it up."))
    return Group(*lines)


CHANNELS = {"web": "Web app", "native": "Native pop-ups", "daemon": "Daemon"}
