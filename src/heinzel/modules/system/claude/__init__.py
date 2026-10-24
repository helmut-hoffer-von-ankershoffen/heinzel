"""The claude submodule of system: the MCP servers and skills you already gave Claude, only listed (ADR 0062).

It reads where Claude Code and Claude Desktop keep them (`~/.claude.json`, `~/.claude/settings.json`,
`~/.claude/skills`, a project's `.mcp.json` and `.claude/skills`, enabled plugins, Desktop's config and extensions)
and shows what it found. Slice 1 is read-only: nothing is imported, switched on or connected to, no secret value is
read into a result (environment variables and headers are listed by name), and a link that leads out of the directory
a file is expected in is not followed. Names can reveal employers and customers, so the full list is for the CLI and the
web app; the MCP and REST heads get counts.
"""

from heinzel import Check, Module
from heinzel.modules.system.claude._models import (
    ClaudeScan,
    ClaudeSummary,
    FoundServer,
    FoundSkill,
    Skipped,
)
from heinzel.modules.system.claude._render import render_scan, render_summary
from heinzel.modules.system.claude._service import ClaudeService

module = Module("claude", "See the MCP servers and skills you already set up for Claude, without using any of them.")
module.service(ClaudeService, ClaudeService.for_user)


@module.operation(name="list", render=render_scan, heads={"cli", "web"})
def list_(service: ClaudeService) -> ClaudeScan:
    """List the MCP servers and skills Claude Code and Claude Desktop have; nothing is switched on."""
    return service.scan()


@module.operation(render=render_summary)
def summary(service: ClaudeService) -> ClaudeSummary:
    """Count the MCP servers and skills found in Claude's configuration per source, without their names."""
    return service.summary()


@module.health
def files(service: ClaudeService) -> list[Check]:
    """Every Claude file that was found can be read, and no link leaves its directory."""
    return service.checks()


__all__ = ["ClaudeScan", "ClaudeService", "ClaudeSummary", "FoundServer", "FoundSkill", "Skipped", "module"]
