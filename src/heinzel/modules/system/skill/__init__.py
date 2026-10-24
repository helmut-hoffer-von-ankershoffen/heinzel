"""The skill module of system: the skills the core, plugins, layers and you offer, for every AI path (ADR 0060).

A skill is a `SKILL.md` directory a module declares with `module.skill(...)`. This module decides which one wins each
name along the layer stack, serves them read-only through `Skills` (Pydantic AI turns, MCP prompts and resources),
and exports the ones you name for Claude Code. It never runs a skill's scripts.
"""

from pathlib import Path
from typing import Annotated

from pydantic import Field
from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

from heinzel import Check, Module, Skills, Status
from heinzel.modules.system.skill._models import (
    ExportItem,
    ExportResult,
    SkillDetail,
    SkillList,
    SkillSettings,
    SkillView,
)
from heinzel.modules.system.skill._service import SkillService

module = Module("skill", "List the skills plugins ship for AI, and hand chosen ones to Claude Code.")
module.settings(SkillSettings)
module.service(SkillService)
module.service(Skills, SkillService)  # turns and the MCP head read skills through it (ADR 0060)
module.skill(Path(__file__).parent / "skills")

LOCAL = {"cli", "web"}
"""Heads that may list skills: a company plugin's skill can be internal, and neither the REST head nor MCP over HTTP
asks who is calling. MCP clients get skills as prompts and resources instead, on stdio and outside a chat only, so a
chat's agent reads no skill beyond `[system.skill] chat` (ADR 0060)."""


def render_list(result: SkillList) -> RenderableType:
    """The skills in effect, where each comes from, and what to run next."""
    table = Table(box=None, pad_edge=False)
    for column in ("Skill", "From", "Description"):
        table.add_column(column, overflow="fold")
    for view in result.skills:
        origin = view.plugin or view.module or "your skills"
        marks = [m for m, on in (("chat", view.chat), ("exported", bool(view.exported))) if on]
        table.add_row(
            Text(view.name, style="bold"),
            Text(f"{origin} ({view.source})" + (f", {', '.join(marks)}" if marks else "")),
            Text(view.description),
        )
    parts: list[RenderableType] = [table if result.skills else Text("No skills are installed.")]
    if shadowed := [f"{v.name} hides the one in {', '.join(v.shadows)}" for v in result.skills if v.shadows]:
        parts.append(Text("Same name from several sources, the more specific wins: " + "; ".join(shadowed)))
    parts += [Text(f"Left out: {conflict}", style="yellow") for conflict in result.conflicts]
    parts += [Text(f"Not loaded: {problem}", style="yellow") for problem in result.problems]
    if result.next:
        parts.append(Text("Next:\n" + "\n".join(f"  {step}" for step in result.next)))
    return Group(*parts)


def render_show(result: SkillDetail) -> RenderableType:
    """A skill's origin, files and hints, then its body."""
    view = result.skill
    lines = [f"{view.name} — {view.plugin or view.module or 'your skills'} ({view.source})", view.description]
    if view.files:
        lines.append(
            "Files: " + ", ".join(view.files) + (f" ({view.scripts} scripts, never run by hz)" if view.scripts else "")
        )
    if view.hints:
        lines.append("Hints, not applied by hz: " + "; ".join(f"{k}: {v}" for k, v in view.hints.items()))
    return Group(Text("\n".join(lines)), Markdown(result.body))


def render_export(result: ExportResult) -> RenderableType:
    """One line per skill and directory, then what to do next."""
    lines = [
        f"{item.action:9} {item.name} → {item.target}" + (f": {item.detail}" if item.detail else "")
        for item in result.items
    ]
    if not lines:
        lines = ["Nothing to do."]
    if result.next:
        lines += ["Next:", *(f"  {step}" for step in result.next)]
    return Text("\n".join(lines))


def _conflicts(result: ExportResult) -> int:
    return 1 if any(item.action == "conflict" for item in result.items) else 0


@module.operation(name="list", render=render_list, heads=LOCAL)
def list_(service: SkillService) -> SkillList:
    """List the skills in effect: which module offers each, what it hides, and where it is exported."""
    return service.listing()


@module.operation(render=render_show, heads=LOCAL)
def show(
    service: SkillService,
    name: Annotated[str, Field(description="The skill, one `hz system skill list` shows, e.g. `explain-heinzel`.")],
) -> SkillDetail:
    """Show a skill: where it comes from, its files and hints, and its body."""
    return service.show(name)


@module.operation(read_only=False, render=render_export, exit_code=_conflicts, heads={"cli"})
def export(
    service: SkillService,
    name: Annotated[
        list[str] | None, Field(description="A skill to export, e.g. explain-heinzel; repeat for each.")
    ] = None,
    all_skills: Annotated[bool, Field(description="Export every skill in effect but your own.")] = False,
    sync: Annotated[
        bool, Field(description="Only remove exports whose skill is gone or changed; never add one.")
    ] = False,
) -> ExportResult:
    """Export the skills you name for Claude Code, as `heinzel-<name>` in each export directory."""
    return service.export(name or (), everything=all_skills, sync=sync)


@module.operation(read_only=False, render=render_export, exit_code=_conflicts, heads={"cli"})
def unexport(
    service: SkillService,
    name: Annotated[list[str] | None, Field(description="A skill to remove from the export; repeat for each.")] = None,
    all_skills: Annotated[bool, Field(description="Remove every skill hz exported.")] = False,
) -> ExportResult:
    """Remove skills hz exported for Claude Code; directories hz didn't write are left alone."""
    return service.unexport(name or (), everything=all_skills)


@module.health
def health(service: SkillService) -> list[Check]:
    """Skills nobody decided between and your own skills that fail their checks; names only, never their text."""
    catalog = service.catalog()
    problems = catalog.conflicts + catalog.problems
    if not problems:
        return [Check(name="skills", status=Status.UP, detail=f"{len(catalog.skills)} in effect")]
    return [
        Check(
            name="skills",
            status=Status.DEGRADED,
            detail="; ".join(problems),
            fix="hz system skill list",
        )
    ]


__all__ = [
    "ExportItem",
    "ExportResult",
    "SkillDetail",
    "SkillList",
    "SkillService",
    "SkillSettings",
    "SkillView",
    "module",
]
