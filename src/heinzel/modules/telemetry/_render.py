"""How the CLI head presents `hz report`; targets and descriptions are shown as text, never markup."""

from rich.console import Group, RenderableType
from rich.text import Text

from heinzel.modules.telemetry._models import Reported, Synced


def render_reported(reported: Reported) -> RenderableType:
    """The entry in one line, the log it went to, and what comes next."""
    entry = reported.entry
    what = f"{entry.heinzel_id} {entry.verb}" + (f" {entry.target}" if entry.target else "")
    lines = [
        Text(f"Reported: {what}"),
        Text(f"  {entry.time.isoformat()} by {entry.user}, entry {reported.entries} of {reported.log}", style="dim"),
    ]
    return Group(*lines, *(Text(line) for line in reported.next))


def render_synced(synced: Synced) -> RenderableType:
    """Each log copied with its new entries, what couldn't be, and the command that shares them."""
    lines = [Text(f"Into {synced.checkout}:")]
    lines += [Text(f"  {log.heinzel_id}: {log.entries} entries, {log.new} new  ({log.path})") for log in synced.logs]
    if not synced.logs and not synced.problems:
        lines.append(Text("  nothing to copy yet; report first: hz report <id> heartbeat"))
    lines += [Text(f"  not copied: {problem}", style="yellow") for problem in synced.problems]
    return Group(*lines, *(Text(line) for line in synced.next))
