"""The writes module of system: what agents changed, and which plugin writes may run directly (ADR 0162).

A Heinzel and an outside agent can run the writes whose operations say `agents="direct"`. This module is the
owner's side of that: `list` shows every write an agent asked for (per agent, and the ones of turns nobody
watched), `plugins` shows the plugin operations that ask to run directly, and `allow` is the owner's yes for one
plugin. Until then a plugin's direct write is proposed, also after an update turned an operation direct.
"""

from typing import Annotated

from pydantic import Field
from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from heinzel import Check, Module, Status
from heinzel.kernel import AgentWriteLog
from heinzel.modules.system.write._models import AgentWrite, AgentWrites, CallerActivity, PluginWrite, PluginWrites
from heinzel.modules.system.write._service import WriteLog

module = Module("write", "See what agents changed, and allow a plugin's writes to run without asking.")
module.service(WriteLog)
module.service(AgentWriteLog, WriteLog)  # the kernel's chat tools and the MCP head record and ask through it

# The record is the owner's: an agent that could read or thin it could hide its own writes.
OWNER = {"cli", "web"}


def render_writes(result: AgentWrites) -> RenderableType:
    """A line per agent, then the entries."""
    if not result.callers:
        return Text("No agent has asked for a write yet.")
    agents = Table(box=None, pad_edge=False)
    for column in ("Agent", "Ran", "Last 24 h", "Not run", "Last"):
        agents.add_column(column, overflow="fold")
    for line in result.callers:
        last = line.last.astimezone().strftime("%Y-%m-%d %H:%M") if line.last else ""
        agents.add_row(Text(line.caller), str(line.ran), str(line.ran_today), str(line.refused), last)
    entries = Table(box=None, pad_edge=False)
    for column in ("When", "Agent", "Operation", "Target", "Outcome"):
        entries.add_column(column, overflow="fold")
    for write in result.writes:
        entries.add_row(
            write.at.astimezone().strftime("%m-%d %H:%M"),
            Text(write.caller + ("" if write.attended else " (unwatched)")),
            Text(f"{write.module} {write.operation}"),
            Text(write.target or ""),
            write.outcome,
        )
    note = Text(f"{result.unattended_ran} ran in turns nobody watched, in the last 24 hours.")
    return Group(agents, Text(""), entries, note)


def render_plugins(result: PluginWrites) -> RenderableType:
    """The plugin operations that ask to run directly, and what to run next."""
    if not result.writes:
        return Text("No installed plugin asks to run a write directly.")
    table = Table(box=None, pad_edge=False)
    for column in ("Plugin", "Operation", "Does", "For agents"):
        table.add_column(column, overflow="fold")
    for write in result.writes:
        state = "asks you first (hz holds it)" if write.capped else "runs directly" if write.allowed else "proposed"
        table.add_row(write.plugin, Text(f"{write.module} {write.operation}"), Text(write.summary), state)
    parts: list[RenderableType] = [table]
    if result.next:
        parts.append(Text("Next:\n" + "\n".join(f"  {step}" for step in result.next)))
    return Group(*parts)


@module.health
def health(service: WriteLog) -> list[Check]:
    """The owner's digest: what ran with nobody watching, and which plugin writes wait for a yes. Never a fault."""
    checks: list[Check] = []
    if count := service.listing(None, unattended=True, limit=1).unattended_ran:
        checks.append(
            Check(
                name="unwatched writes",
                status=Status.UP,
                detail=f"{count} writes ran in the last 24 hours in turns nobody watched; "
                "see them with `hz system write list --unattended`.",
            )
        )
    for step in service.plugin_writes().next:
        plugin = step.rsplit(" ", 1)[-1]
        checks.append(
            Check(
                name=f"direct writes of {plugin}",
                status=Status.UP,
                detail=f"{plugin} asks to run some writes for agents without asking you. Until you allow it they "
                f"are proposed: `hz system write plugins`, then `{step}`.",
            )
        )
    return checks


@module.operation(name="list", render=render_writes, heads=OWNER)
def list_(
    service: WriteLog,
    caller: Annotated[
        str | None, Field(description="Only this agent, e.g. `token:k1a2b3` or `heinzel:<session>`.")
    ] = None,
    unattended: Annotated[bool, Field(description="Only writes of turns nobody watched.")] = False,
    limit: Annotated[int, Field(description="How many entries, latest first.", ge=1, le=500)] = 50,
) -> AgentWrites:
    """List the writes agents asked for: what ran, what waited for you, and a line per agent."""
    return service.listing(caller, unattended=unattended, limit=limit)


@module.operation(render=render_plugins, heads=OWNER)
def plugins(service: WriteLog) -> PluginWrites:
    """List the plugin operations that ask to run directly for agents, and whether you allowed them."""
    return service.plugin_writes()


@module.operation(read_only=False, render=render_plugins, heads=OWNER, agents="never")
def allow(
    service: WriteLog,
    plugin: Annotated[str, Field(description="The plugin, e.g. `crm` or `heinzel-crm`.")],
    revoke: Annotated[bool, Field(description="Take the allowance back: its writes are proposed again.")] = False,
) -> PluginWrites:
    """Allow the writes a plugin marks direct to run without asking, for its installed version."""
    return service.allow(plugin, revoke=revoke)


__all__ = [
    "AgentWrite",
    "AgentWrites",
    "CallerActivity",
    "PluginWrite",
    "PluginWrites",
    "WriteLog",
    "module",
]
