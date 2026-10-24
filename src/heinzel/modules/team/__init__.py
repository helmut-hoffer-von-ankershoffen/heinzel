"""The team module: create, join and add to the Git repository a team keeps its shared state in.

A team repository holds the manifest `hz.yaml`, the roster `team/roster.yaml` its owners approve, each person's
`people/<handle>.yaml`, the team's contracts in `workflows/`, `inventory/`, `policies/` and a GitHub Actions workflow
that checks them (proposed onboarding ADR). The invite is the repository's URL: `hz team join <url>` clones it with
your own access, finds you in the roster by the login you are signed in with, and makes it your team. The module
provides the `OrgContext` other modules read it through (`workflow` finds `team:` contracts there).

Steps that change something outside your machine (creating a repository, pushing, opening a pull request) and
making a team yours wait for `--yes`; until then they are shown. These operations are CLI and MCP only: a web page can
reach a local REST server (ADR 0019).
"""

from pathlib import Path
from typing import Annotated

from pydantic import Field

from heinzel import Check, Module, Status
from heinzel.kernel import OrgContext
from heinzel.modules.team._context import TeamContext
from heinzel.modules.team._models import ROLES, Adopted, Manifest, Member, Roster, TeamPlan, TeamSettings, TeamShow
from heinzel.modules.team._render import render_adopted, render_plan, render_show
from heinzel.modules.team._service import TeamService

module = Module(
    "team",
    "Your team's repository: create one (`hz team new`), join one by its URL (`hz team join`), add people to it.",
)
module.settings(TeamSettings)
module.service(TeamService, TeamService.for_user)
module.service(OrgContext, TeamContext)

ROLE_LIST = ", ".join(ROLES)
Yes = Annotated[
    bool,
    Field(description="Carry out the steps that wait for you: remote changes, and making the team yours."),
]
Login = Annotated[
    str | None, Field(description="Your GitHub login, when `gh` isn't signed in; asked of `gh api user` otherwise.")
]


@module.operation(read_only=False, render=render_plan, heads={"cli", "mcp"})
def new(  # noqa: PLR0913, PLR0917 - the options of one command
    service: TeamService,
    name: Annotated[str, Field(description="The team's name: lowercase words joined by dashes, e.g. payments.")],
    github: Annotated[
        str | None,
        Field(description="Create it on GitHub as this private repository, owner/name; only on this machine if none."),
    ] = None,
    roster: Annotated[
        str | None,
        Field(description="Draft the roster from a GitHub team you can read: github:<org>/<team>, or github:<org>."),
    ] = None,
    role: Annotated[str, Field(description=f"Your role: one of {ROLE_LIST}.")] = "engineering-manager",
    handle: Annotated[str | None, Field(description="Your handle in the team; defaults to your GitHub login.")] = None,
    login: Login = None,
    yes: Yes = False,
) -> TeamPlan:
    """Create a team repository from the template: you its first owner, its roster drafted for review."""
    return service.new(name, github=github, roster=roster, handle=handle, login=login, role=role, yes=yes)


@module.operation(read_only=False, render=render_plan, heads={"cli", "mcp"})
def join(  # noqa: PLR0913, PLR0917 - the options of one command
    service: TeamService,
    url: Annotated[str, Field(description="The team repository's URL, the invite: https, ssh or a local path.")],
    propose: Annotated[
        bool, Field(description="When the roster doesn't list you, open a pull request adding you; owners merge it.")
    ] = False,
    handle: Annotated[
        str | None,
        Field(description="Your handle: the roster entry that is you when it has no login yet, or the one to propose."),
    ] = None,
    role: Annotated[
        str, Field(description=f"Your role when you propose to be added: one of {ROLE_LIST}.")
    ] = "engineer",
    login: Login = None,
    yes: Yes = False,
) -> TeamPlan:
    """Clone a team repository with your own access, find you in its roster, and make it your team."""
    return service.join(url, propose=propose, handle=handle, login=login, role=role, yes=yes)


@module.operation(read_only=False, render=render_plan, heads={"cli", "mcp"})
def add(  # noqa: PLR0913, PLR0917 - the options of one command
    service: TeamService,
    handle: Annotated[str, Field(description="Their handle: lowercase letters, digits, dashes.")],
    login: Annotated[str | None, Field(description="Their GitHub login; an owner needs one.")] = None,
    role: Annotated[str, Field(description=f"Their role: one of {ROLE_LIST}.")] = "engineer",
    owner: Annotated[bool, Field(description="Make them an owner: they approve roster changes.")] = False,
    by: Annotated[str | None, Field(description="Your own GitHub login, when `gh` isn't signed in as you.")] = None,
    yes: Yes = False,
) -> TeamPlan:
    """Add someone to your team's roster, as one of its owners: a pull request the other owners see."""
    return service.add(handle, login=login, role=role, owner=owner, by=by, yes=yes)


@module.operation(render=render_show)
def show(service: TeamService) -> TeamShow:
    """Show your team: its repository, checkout and members, and what is wrong with them."""
    return service.show()


@module.operation(read_only=False, render=render_adopted, heads={"cli", "mcp"})
def adopt(
    service: TeamService,
    path: Annotated[Path, Field(description="A checkout of your team repository you already have.")],
) -> Adopted:
    """Make an existing checkout your team repository; replaces the deprecated `workflow.team_repository`."""
    return service.adopt(path)


@module.health
def health(service: TeamService) -> list[Check]:
    """The team repository's checkout exists and its manifest and roster are valid."""
    if service.checkout is None:
        return []
    shown = service.show()
    if shown.problems:
        fix = shown.next[0] if shown.next else "fix the file in a pull request to the team repository: hz team show"
        return [Check(name="checkout", status=Status.DOWN, detail=shown.problems[0], fix=fix)]
    return [Check(name="checkout", status=Status.UP, detail=f"{shown.name}, {len(shown.members)} member(s)")]


__all__ = [
    "ROLES",
    "Adopted",
    "Manifest",
    "Member",
    "Roster",
    "TeamContext",
    "TeamPlan",
    "TeamService",
    "TeamSettings",
    "TeamShow",
    "module",
]
