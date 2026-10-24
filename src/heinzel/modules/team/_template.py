"""The files of a new team repository, and the entries `join` and `add` write into one.

What is written here lands in other people's repositories, so it names no ADR number and no internal detail: only
what the files are for and how to use them.
"""

from packaging.version import Version

from heinzel import __version__
from heinzel.modules.team._models import CODEOWNERS, MANIFEST, PEOPLE, ROSTER, Manifest, Member, Roster
from heinzel.modules.team._yaml import dump

SOURCE = "https://github.com/helmut-hoffer-von-ankershoffen/heinzel"
CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1"
SETUP_UV = "astral-sh/setup-uv@07a047e608db84b3d8c06cd9a7c7dcc9c355c18e # v10.3.2"
"""Pinned to commit SHAs like this repository's own workflows; keep them in step with `.github/workflows/ci.yml`."""
OWNED = ("/hz.yaml", "/CODEOWNERS", "/team/", "/policies/", "/.github/")
"""What the owners approve; each person owns their own file in people/."""

MANIFEST_HEADER = """\
What this repository is: the team's name, the roles that own its roster, and its plugin, if it has one.
It configures nobody's machine; owners approve changes to it (CODEOWNERS)."""
ROSTER_HEADER = """\
The team's roster: handle, GitHub login, roles and dates, nothing else: no names, titles, managers or locations.
hz drafts it; the owners approve every change (CODEOWNERS). Leaving sets `until`; handles are never reused."""
PERSON_HEADER = """\
{handle}'s preferences for this team. Only they edit this file (CODEOWNERS).
No name, e-mail or location here."""
README = """\
# {name}

The hz team repository of {name}: its roster, contracts, inventory and policies.

| Path | What |
| --- | --- |
| `hz.yaml` | what the team is; owners approve changes |
| `team/roster.yaml` | who is on the team: handle, GitHub login, roles, dates; owners approve changes |
| `people/<handle>.yaml` | each person's preferences; only they edit theirs |
| `workflows/` | team contracts, `<name>.prose.md`; `hz workflow check --path workflows` gates them |
| `inventory/` | declared artefacts and systems |
| `policies/` | owned policies |

Join with one line: `hz team join <this repository's URL>`. It clones the repository with your own access and finds
you in the roster by your GitHub login. Not on the roster yet? `hz team join <url> --propose` opens a pull request the
owners merge.
"""
FOLDER_READMES = {
    "workflows/README.md": "# Team contracts\n\n`<name>.prose.md` files. They run by hand, or on a member's daemon "
    "when that member names them in `[workflow] schedule_team`.\n",
    "inventory/README.md": "# Inventory\n\nDeclared artefacts and systems of the team, as YAML.\n",
    "policies/README.md": "# Policies\n\nPolicies the owners keep, such as auto-approvals.\n",
}


def _release() -> str:
    """The git ref the Actions workflow installs hz from: this release's tag, or `main` for a development build."""
    version = Version(__version__)
    return "main" if version.is_devrelease or version.local or version == Version("0.0.0") else f"v{version}"


ACTIONS = f"""\
# Checks every pull request to the team repository: the contracts in workflows/ must pass `hz workflow check`.
# Nothing here runs a contract, reads a secret or publishes.
name: hz team

on:
  pull_request:
  push:
    branches: [main]

permissions: {{}}

jobs:
  check:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    permissions:
      contents: read
    steps:
      - uses: {CHECKOUT}
        with:
          persist-credentials: false
      - uses: {SETUP_UV}
        with:
          enable-cache: false
      - name: Check the team's contracts
        run: uvx --from "git+{SOURCE}@{_release()}" hz workflow check --path workflows
"""


def codeowners(owners: list[str]) -> str:
    """CODEOWNERS of a new repository: `owners` (GitHub logins) approve the manifest, roster and policies."""
    who = " ".join(f"@{login}" for login in owners)
    lines = ["# Owners approve the manifest, the roster and the policies; each person owns their own file in people/."]
    lines += [f"{path} {who}" for path in OWNED]
    return "\n".join(lines) + "\n"


def with_owner(text: str, login: str) -> str:
    """CODEOWNERS `text` with `login` added to every line the owners approve."""
    lines: list[str] = []
    for line in text.splitlines():
        path, _, rest = line.partition(" ")
        owned = path in OWNED and f"@{login}".lower() not in rest.lower().split()
        lines.append(f"{line} @{login}" if owned else line)
    return "\n".join(lines) + "\n"


def person_line(handle: str, login: str) -> str:
    """The CODEOWNERS line that gives a person their own file."""
    return f"/{PEOPLE}/{handle}.yaml @{login}\n"


def person(handle: str) -> str:
    """`people/<handle>.yaml` for someone who just joined."""
    return dump({"handle": handle, "preferences": {}}, header=PERSON_HEADER.format(handle=handle))


def roster_file(roster: Roster) -> str:
    """`team/roster.yaml` for `roster`, leaving out what isn't set."""
    members = [m.model_dump(mode="json", exclude_none=True, exclude_defaults=True) for m in roster.members]
    return dump({"members": members}, header=ROSTER_HEADER)


def manifest_file(manifest: Manifest) -> str:
    """`hz.yaml` for `manifest`, without the defaults, so the core lists can grow."""
    return dump(manifest.model_dump(mode="json", exclude_defaults=True), header=MANIFEST_HEADER)


def repository(name: str, creator: Member, drafted: list[Member]) -> dict[str, str]:
    """Every file of a new team repository named `name`: `creator` its first owner, `drafted` the rest of the roster.

    Drafted members get no CODEOWNERS line yet: GitHub flags owners without access, and they get one when they join.
    """
    login = creator.github or ""
    files = {
        MANIFEST: manifest_file(Manifest(name=name)),
        ROSTER: roster_file(Roster(members=[creator, *drafted])),
        f"{PEOPLE}/{creator.handle}.yaml": person(creator.handle),
        CODEOWNERS: codeowners([login]) + person_line(creator.handle, login),
        "README.md": README.format(name=name),
        ".github/workflows/hz-team.yml": ACTIONS,
    }
    return files | FOLDER_READMES
