"""The claude submodule of system: the MCP servers and skills you already gave Claude, listed and switched (ADR 0062).

It reads where Claude Code and Claude Desktop keep them (`~/.claude.json`, `~/.claude/settings.json`,
`~/.claude/skills`, a project's `.mcp.json` and `.claude/skills`, enabled plugins, Desktop's config and extensions)
and shows what it found. Nothing is imported, started or connected to, no secret value is
read into a result (environment variables and headers are listed by name), and a link that leads out of the directory
a file is expected in is not followed. Names can reveal employers and customers, so the full list is for the CLI and the
web app; the MCP and REST heads get counts.

A found MCP server is switched on by default, also one from a project or a plugin, and `disable` switches it off for
good (ADR 0146). The switch only records what Heinzels may use once they can; it runs nothing. `enable`, `disable`
and `acknowledge` are for the CLI and the web app only, so an agent cannot switch a server on.
"""

from typing import Annotated

from pydantic import Field
from rich.console import RenderableType

from heinzel import Check, Module
from heinzel.modules.system.claude._models import (
    ClaudeScan,
    ClaudeSummary,
    FirstRunNotice,
    FoundServer,
    FoundSkill,
    NoticeClosed,
    ServerSwitch,
    Skipped,
)
from heinzel.modules.system.claude._service import ClaudeService


def render_scan(scan: ClaudeScan) -> RenderableType:
    """Servers and skills in two tables, then what was skipped; no value of any secret is ever in them."""
    from heinzel.modules.system.claude._render import render_scan  # noqa: PLC0415 - CLI presentation is loaded only when rendered

    return render_scan(scan)


def render_summary(summary: ClaudeSummary) -> RenderableType:
    """The counts in two lines."""
    from heinzel.modules.system.claude._render import render_summary  # noqa: PLC0415 - CLI presentation is loaded only when rendered

    return render_summary(summary)


def render_switch(result: ServerSwitch) -> RenderableType:
    """One line: what the server is now."""
    from heinzel.modules.system.claude._render import render_switch  # noqa: PLC0415 - CLI presentation is loaded only when rendered

    return render_switch(result)


def render_closed(result: NoticeClosed) -> RenderableType:
    """One line: the notice is closed."""
    from heinzel.modules.system.claude._render import render_closed  # noqa: PLC0415 - CLI presentation is loaded only when rendered

    return render_closed(result)


ServerId = Annotated[str, Field(description="The server's id, as `hz system claude list --json` shows it.")]

module = Module("claude", "See the MCP servers and skills you already set up for Claude, and switch servers off or on.")
module.service(ClaudeService, ClaudeService.for_user)


@module.operation(name="list", render=render_scan, heads={"cli", "web"})
def list_(service: ClaudeService) -> ClaudeScan:
    """List the MCP servers and skills Claude has here, and which servers are on; none is run."""
    return service.scan()


# Switching is for the person: the CLI, or the web app behind its token. Not for an agent over MCP, nor for the
# unauthenticated REST head, which any local web page can reach (ADR 0146). Private, so the web head takes the id
# in the request's body: an id can name a project directory, and a query string ends up in access logs.
@module.operation(read_only=False, private=True, render=render_switch, heads={"cli", "web"})
def enable(service: ClaudeService, server: ServerId) -> ServerSwitch:
    """Switch a found MCP server back on for Heinzels; nothing is started."""
    return service.switch(server, on=True)


@module.operation(read_only=False, private=True, render=render_switch, heads={"cli", "web"})
def disable(service: ClaudeService, server: ServerId) -> ServerSwitch:
    """Switch a found MCP server off for Heinzels; it stays off, also when its entry changes."""
    return service.switch(server, on=False)


@module.operation(read_only=False, private=True, render=render_closed, heads={"cli", "web"})
def acknowledge(
    service: ClaudeService,
    servers: Annotated[
        list[str] | None,
        Field(description="Ids of the servers the notice showed; every server found now when not given."),
    ] = None,
) -> NoticeClosed:
    """Close the notice about switched-on servers; it comes back when a new server is found."""
    return service.acknowledge(servers)


@module.operation(render=render_summary)
def summary(service: ClaudeService) -> ClaudeSummary:
    """Count the MCP servers and skills found in Claude's configuration per source, without their names."""
    return service.summary()


@module.health
def files(service: ClaudeService) -> list[Check]:
    """Every Claude file that was found can be read, and so can the file that remembers switched-off servers."""
    return service.checks()


__all__ = [
    "ClaudeScan",
    "ClaudeService",
    "ClaudeSummary",
    "FirstRunNotice",
    "FoundServer",
    "FoundSkill",
    "NoticeClosed",
    "ServerSwitch",
    "Skipped",
    "module",
]
