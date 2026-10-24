"""How the CLI head presents `hz team`.

Names, handles and commands come from a team repository someone else writes, so they are shown as text, never markup.
"""

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from heinzel.modules.team._models import Adopted, MemberView, TeamPlan, TeamShow


def render_plan(plan: TeamPlan) -> RenderableType:
    """Each step with a mark for done or still to do, the commands it runs, and what comes next."""
    parts: list[RenderableType] = []
    title = f"hz team {plan.operation}" + (f": {plan.team}" if plan.team else "")
    parts.append(Text(title, style="bold"))
    if plan.checkout is not None:
        parts.append(Text(f"Checkout: {plan.checkout}"))
    for action in plan.actions:
        mark = "done " if action.done else "to do"
        line = Text(f"  {mark}  {action.what}")
        if action.remote:
            line.append("  (remote)", style="yellow")
        elif action.confirm:
            line.append("  (waits for your yes)", style="yellow")
        parts.append(line)
        if action.command:
            parts.append(Text(f"         $ {action.command}", style="dim"))
    if plan.member is not None:
        parts.append(Text(f"You are {plan.member.handle} ({', '.join(plan.member.roles)})."))
    if plan.plugin:
        parts.append(Text(f"The team names the plugin {plan.plugin}; install it yourself if you want it."))
    if plan.drafted:
        parts.append(_members("Drafted roster, for you to review", plan.drafted))
    if plan.pull_request:
        parts.append(Text(f"Pull request: {plan.pull_request}"))
    parts += [Text(line) for line in plan.next]
    return Group(*parts)


def render_show(show: TeamShow) -> RenderableType:
    """The team, its checkout and its active members; without a team, only how to get one."""
    parts: list[RenderableType] = []
    if show.checkout is not None:
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold")
        grid.add_column(overflow="fold")
        grid.add_row("Team", Text(show.name or "-"))
        grid.add_row("Repository", Text(show.repository or "-"))
        missing = "" if show.exists else " (missing)"
        grid.add_row("Checkout", Text(f"{show.checkout}{missing}"))
        if show.plugin:
            grid.add_row("Plugin", Text(show.plugin))
        parts.append(grid)
    if show.members:
        parts.append(_members("Members", show.members))
    parts += [Text(problem, style="red") for problem in show.problems]
    parts += [Text(line) for line in show.next]
    return Group(*parts)


def render_adopted(adopted: Adopted) -> RenderableType:
    """What `adopt` recorded, and what is left to do."""
    lines = [
        f"Your team repository is the checkout at {adopted.checkout} ({adopted.repository or 'no remote'}).",
        "; ".join(adopted.written).capitalize() + ".",
        *adopted.next,
    ]
    return Text("\n".join(lines))


def _members(title: str, members: list[MemberView]) -> Table:
    table = Table(title=title)
    for column in ("Handle", "GitHub", "Roles", "Owner", "Since"):
        table.add_column(column, overflow="fold")
    for member in members:
        roles = ", ".join(member.roles)
        cells = (member.handle, member.github or "-", roles, "yes" if member.owner else "", str(member.since))
        table.add_row(*map(Text, cells))
    return table
