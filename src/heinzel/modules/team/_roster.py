"""Who someone is, and who is on a team, from sources a person can already read (proposed onboarding ADR).

Identity comes from the person's own logins: the GitHub login `gh` is signed in as. A roster is drafted from what a
manager can read, such as a GitHub team's members, and becomes `team/roster.yaml` only when they approve it. Both are
seams: a Google login (through `gws`), an org chart or a tracker's team are further sources, added as adapters later.
Vendor CLIs are the transport and own authentication; hz never sees a token.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

from heinzel.kernel import HeinzelError
from heinzel.modules.team._models import check_login
from heinzel.modules.team._repo import CommandFailed, Run

_GITHUB_TEAM = re.compile(
    r"^github:(?P<org>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))(?:/(?P<team>[a-z0-9][a-z0-9_-]{0,99}))?$"
)
MAX_ROSTER = 100
"""More people than this aren't drafted into one team: a team is small, and a whole org is not a roster."""


@dataclass(frozen=True, slots=True)
class Identity:
    """Who the person at the keyboard is, as one source says."""

    source: str
    login: str


def github_identity(run: Run) -> Identity | None:
    """The GitHub login `gh` is signed in as; None when `gh` is missing, signed out or answers something else."""
    try:
        login = run(["gh", "api", "user", "--jq", ".login"], None).strip()
        return Identity(source="github", login=check_login(login))
    except (CommandFailed, HeinzelError, ValueError):  # missing, signed out or a strange answer: no identity
        return None


IDENTITY_SOURCES: tuple[Callable[[Run], Identity | None], ...] = (github_identity,)
"""Where identities come from, in order; a Google login through `gws` joins when its adapter exists."""


def identities(run: Run) -> list[Identity]:
    """Every identity the sources know for the person, first source first."""
    return [found for source in IDENTITY_SOURCES if (found := source(run)) is not None]


def check_source(source: str) -> str:
    """`source` if it names a roster source hz can draft from.

    Raises:
        ValueError: Naming the sources there are.
    """
    if _GITHUB_TEAM.fullmatch(source) is None:
        msg = (
            f"{source!r} isn't a roster source; use github:<org>/<team-slug> for a GitHub team's members or "
            "github:<org> for an organisation's"
        )
        raise ValueError(msg)
    return source


def roster_command(source: str) -> list[str]:
    """The `gh` command that lists the logins of `source`, as shown before it runs.

    Raises:
        ValueError: If `source` isn't one (`check_source`).
    """
    match = _GITHUB_TEAM.fullmatch(check_source(source))
    assert match is not None  # noqa: S101 - check_source just matched it
    path = f"orgs/{match['org']}/teams/{match['team']}/members" if match["team"] else f"orgs/{match['org']}/members"
    return ["gh", "api", "--paginate", path, "--jq", ".[].login"]


def draft_roster(run: Run, source: str) -> list[str]:
    """The GitHub logins of `source`, in the order GitHub lists them, each once.

    Raises:
        CommandFailed: If `gh` can't read it, e.g. without access to the team.
        ValueError: If `source` isn't one, an answer isn't a login, or there are more than `MAX_ROSTER`.
    """
    output = run(roster_command(source), None)
    logins: list[str] = []
    for line in output.splitlines():
        if not (text := line.strip()):
            continue
        login = check_login(text)
        if login.lower() not in {known.lower() for known in logins}:
            logins.append(login)
    if len(logins) > MAX_ROSTER:
        msg = f"{source} has {len(logins)} members, more than a team's {MAX_ROSTER}; draft from a smaller team"
        raise ValueError(msg)
    return logins
