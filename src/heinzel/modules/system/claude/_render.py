"""How the CLI head presents what was found in Claude's configuration."""

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from heinzel.modules.system.claude._models import ClaudeScan, ClaudeSummary, FirstRunNotice, NoticeClosed, ServerSwitch


def _where(source: str, origin: str | None) -> str:
    return f"{source} {origin}" if origin else source


def _notice(notice: FirstRunNotice) -> str:
    count = len(notice.servers)
    scoped = [f"{n} from {kind}" for n, kind in ((notice.project, "projects"), (notice.plugin, "plugins")) if n]
    return (
        f"New: {count} MCP server{'' if count == 1 else 's'} found in Claude {'is' if count == 1 else 'are'} "
        f"switched on for your Heinzels{f' ({", ".join(scoped)})' if scoped else ''}. Nothing is started yet. "
        "Switch one off with `hz system claude disable <id>` (ids: `hz system claude list --json`); "
        "close this notice with `hz system claude acknowledge`."
    )


def render_scan(scan: ClaudeScan) -> RenderableType:
    """Servers and skills in two tables, then what was skipped; no value of any secret is ever in them."""
    parts: list[RenderableType] = []
    if scan.state_problem:
        parts.append(Text(scan.state_problem, style="yellow"))
    if scan.notice:
        parts.append(Text(_notice(scan.notice), style="yellow"))
    if scan.servers:
        table = Table(title="MCP servers found in Claude (none is started by hz)", box=None, pad_edge=False)
        for column in ("On", "Scope", "From", "Name", "Via", "Auth", "Names given to it"):
            table.add_column(column, overflow="fold")
        for s in scan.servers:
            via = s.host or f"{s.command or '?'} ({s.arguments} args)"
            given = ", ".join([*s.env_keys, *(f"header {k}" for k in s.header_keys)]) or "-"
            name = f"{s.name} (hz itself)" if s.status == "self" else s.name
            table.add_row(
                Text("-" if s.status == "self" else "on" if s.enabled else "off"),
                Text(s.scope),
                Text(_where(s.source, s.origin)),
                Text(name),
                Text(f"{s.transport}: {via}"),
                Text(s.auth),
                Text(given),
            )
        parts.append(table)
    if scan.skills:
        table = Table(title="Skills found in Claude (none is used by hz)", box=None, pad_edge=False)
        for column in ("From", "Name", "Bundled", "Description"):
            table.add_column(column, overflow="fold")
        for k in scan.skills:
            bundled = ", ".join(f"{kind} {count}" for kind, count in k.files.items()) or "-"
            table.add_row(
                _where(k.source, k.origin), k.name, bundled, k.description.splitlines()[0] if k.description else ""
            )
        parts.append(table)
    if scan.connectors:
        parts.append(
            Text(f"{scan.connectors} claude.ai connectors: reachable only through Claude Code, so not listed.")
        )
    parts += [Text(f"Left alone: {s.path}: {s.reason}", style="yellow") for s in scan.skipped]
    parts += [Text(step) for step in scan.next]
    return Group(*parts) if parts else Text("Nothing found.")


def render_summary(summary: ClaudeSummary) -> RenderableType:
    """The counts in two lines."""
    servers = ", ".join(f"{source} {n}" for source, n in sorted(summary.servers.items())) or "none"
    skills = ", ".join(f"{source} {n}" for source, n in sorted(summary.skills.items())) or "none"
    return Text(
        f"MCP servers: {servers} ({summary.enabled} switched on)\nSkills: {skills}\n"
        f"claude.ai connectors: {summary.connectors}\nLeft alone: {summary.skipped}"
    )


def render_switch(result: ServerSwitch) -> RenderableType:
    """One line: what the server is now; nothing was started or stopped."""
    state = "on" if result.enabled else "off"
    already = "" if result.changed else "already "
    return Text(f"{result.name} is {already}switched {state} for your Heinzels. Nothing is started or stopped by this.")


def render_closed(result: NoticeClosed) -> RenderableType:
    """One line: the notice is closed."""
    return Text(f"Notice closed for {result.servers} server(s). It comes back when a new server is found.")
