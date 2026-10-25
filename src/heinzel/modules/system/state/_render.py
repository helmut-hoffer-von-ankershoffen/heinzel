"""How the CLI head presents `hz system state`; repository URLs and errors are shown as text, never markup."""

from rich.console import Group, RenderableType
from rich.text import Text

from heinzel.modules.system.state._models import Pushed, StateStatus


def render_status(status: StateStatus) -> RenderableType:
    """Where state goes, each stream's lines and what is pending, the last push, and what comes next."""
    where = f"{status.branch or 'hz/<handle>/' + status.machine} in {status.repository}" if status.repository else None
    lines = [Text(f"Pushes to: {where}" if where else "Pushes to: nowhere; nothing leaves this machine")]
    lines += [Text(f"  {s.name}: {s.lines} line(s), {s.pending} not pushed yet") for s in status.streams]
    if status.last_push is not None:
        lines.append(Text(f"  last push: {status.last_push:%Y-%m-%d %H:%M}", style="dim"))
    if status.error:
        label = "stopped" if status.refused else f"failing ({status.failures} attempt(s))"
        lines.append(Text(f"  {label}: {status.error}", style="yellow"))
    return Group(*lines, *(Text(line) for line in status.next))


def render_pushed(pushed: Pushed) -> RenderableType:
    """What went to which branch, or that the branch was current."""
    if not pushed.pushed:
        return Text(f"{pushed.branch} in {pushed.repository} is current; nothing to push")
    return Text(
        f"Pushed {pushed.lines} new line(s) in {pushed.segments} segment(s) to {pushed.branch} in {pushed.repository}"
    )
