"""Settings, the files of a team repository, and the results of `hz team` (proposed onboarding ADR)."""

import re
from datetime import date
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from heinzel.kernel import ModuleSettings
from heinzel.modules.team._repo import check_url

ROLES = (
    "engineer",
    "principal",
    "engineering-manager",
    "engineering-leadership",
    "product-manager",
    "product-leadership",
    "program-manager",
)
"""The core role vocabulary; layers' plugins add roles later, never rename these."""
OWNER_ROLES = ("engineering-manager", "engineering-leadership", "product-leadership")
"""Roles that own the roster by default; the creator of a team is its first owner whatever their role."""
HANDLE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
"""A handle: lowercase letters and digits, single dashes between; it names a file, so nothing else."""
MAX_HANDLE = 39
LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
"""A GitHub login as GitHub allows it."""
NAME = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
"""A team or role name: lowercase words joined by single dashes."""
DISTRIBUTION = re.compile(r"^heinzel-[a-z0-9]+(?:-[a-z0-9]+)*$")
"""A plugin distribution's name (ADR 0025)."""
EMAIL = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9.-]{1,253}$")
MAX_NAME = 64
MANIFEST = "hz.yaml"
ROSTER = "team/roster.yaml"
PEOPLE = "people"
CODEOWNERS = "CODEOWNERS"


def check_handle(handle: str) -> str:
    """`handle` if it is one.

    Raises:
        ValueError: Saying what a handle is.
    """
    if len(handle) > MAX_HANDLE or not HANDLE.fullmatch(handle):
        msg = f"{handle!r} isn't a handle: lowercase letters and digits with single dashes, at most {MAX_HANDLE}"
        raise ValueError(msg)
    return handle


def check_login(login: str) -> str:
    """`login` if it is a GitHub login.

    Raises:
        ValueError: Saying what a login is.
    """
    if not LOGIN.fullmatch(login):
        msg = f"{login!r} isn't a GitHub login: letters, digits and single dashes, at most 39"
        raise ValueError(msg)
    return login


def check_name(name: str, what: str = "team name") -> str:
    """`name` if it is a team or role name.

    Raises:
        ValueError: Saying what a name is.
    """
    if len(name) > MAX_NAME or not NAME.fullmatch(name):
        msg = f"{name!r} isn't a {what}: lowercase words joined by single dashes, at most {MAX_NAME} characters"
        raise ValueError(msg)
    return name


def check_role(role: str) -> str:
    """`role` if it is one of the core roles.

    Raises:
        ValueError: Naming the roles there are.
    """
    if role not in ROLES:
        msg = f"{role!r} isn't a role; the roles are {', '.join(ROLES)}"
        raise ValueError(msg)
    return role


class TeamSettings(ModuleSettings):
    """Your team: the `[team]` table or `HZ_TEAM__*` variables; `hz team new`, `join` and `adopt` write them.

    A project `hz.toml` can't set them: a repository someone else controls must not decide which repository your
    daemon reads contracts from (ADR 0021 §10).
    """

    repository: str | None = Field(
        default=None,
        description="Your team repository's URL, the invite others join with (`hz team join <url>`); none for a team "
        "repository that exists only on this machine.",
    )
    checkout: Path | None = Field(
        default=None,
        description="The local checkout of your team repository; its `workflows/` holds the `team:` contracts.",
    )

    ignored_in_project: ClassVar[frozenset[str]] = frozenset({"repository", "checkout"})

    @field_validator("repository")
    @classmethod
    def _url(cls, url: str | None) -> str | None:
        return None if url is None else check_url(url)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Plugin(_Strict):
    """The team plugin the manifest names: shown to the person; installing it is the person's decision."""

    name: str = Field(description="Its distribution, `heinzel-<name>`.")
    source: str | None = Field(default=None, description="Where it installs from: an index, a repository or a path.")

    @field_validator("name")
    @classmethod
    def _name(cls, name: str) -> str:
        if not DISTRIBUTION.fullmatch(name):
            msg = f"{name!r} isn't a plugin distribution; plugins are named heinzel-<name>"
            raise ValueError(msg)
        return name


class Manifest(_Strict):
    """`hz.yaml` at the root of a team repository: what the team is. It configures nobody's machine."""

    kind: Literal["team"] = Field(default="team", description="What the repository is.")
    name: str = Field(description="The team's name, lowercase words joined by dashes.")
    owners: list[str] = Field(
        default_factory=lambda: list(OWNER_ROLES), description="Roles that own the roster (CODEOWNERS of team/)."
    )
    plugin: Plugin | None = Field(default=None, description="The team's plugin, if it has one.")

    @field_validator("name")
    @classmethod
    def _name(cls, name: str) -> str:
        return check_name(name)

    @field_validator("owners")
    @classmethod
    def _owners(cls, roles: list[str]) -> list[str]:
        return [check_name(role, "role") for role in roles]


class Member(_Strict):
    """One roster entry: only what ADR 0024 §6 allows in a repository, never a name, title or manager."""

    handle: str = Field(description="The person's hz handle, unique in the team and never reused.")
    github: str | None = Field(default=None, description="Their GitHub login; owners need one.")
    email: str | None = Field(default=None, description="Their office-suite e-mail, to match a Google login.")
    roles: list[str] = Field(min_length=1, description="Their hz roles.")
    owner: bool = Field(default=False, description="Whether they own the roster whatever their roles.")
    since: date = Field(description="When they joined.")
    until: date | None = Field(default=None, description="When they left; removal sets it, nothing is deleted.")

    @field_validator("handle")
    @classmethod
    def _handle(cls, handle: str) -> str:
        return check_handle(handle)

    @field_validator("github")
    @classmethod
    def _github(cls, login: str | None) -> str | None:
        return None if login is None else check_login(login)

    @field_validator("email")
    @classmethod
    def _email(cls, email: str | None) -> str | None:
        if email is not None and not EMAIL.fullmatch(email):
            msg = f"{email!r} isn't an e-mail address"
            raise ValueError(msg)
        return email

    @field_validator("roles")
    @classmethod
    def _roles(cls, roles: list[str]) -> list[str]:
        return [check_name(role, "role") for role in roles]

    def active(self, today: date) -> bool:
        """Whether they are still a member on `today`."""
        return self.until is None or self.until > today


class Roster(_Strict):
    """`team/roster.yaml`: everyone who is or was on the team. hz drafts it; the owners approve it."""

    members: list[Member] = Field(default_factory=list[Member], description="Members, in the order they joined.")

    @model_validator(mode="after")
    def _unique(self) -> "Roster":
        for what, values in (
            ("handles", [m.handle for m in self.members]),
            ("GitHub logins", [m.github.lower() for m in self.members if m.github]),
        ):
            if duplicates := sorted({value for value in values if values.count(value) > 1}):
                msg = f"{what} appear more than once: {', '.join(duplicates)}"
                raise ValueError(msg)
        return self

    def owners(self, manifest: Manifest, today: date) -> list[Member]:
        """The active members who own the roster: owners, and members in one of the manifest's owner roles."""
        return [m for m in self.members if m.active(today) and (m.owner or set(m.roles) & set(manifest.owners))]


class Action(BaseModel):
    """One thing `hz team new`, `join` or `add` does, shown before it happens."""

    what: str = Field(description="What it does, in words.")
    command: str | None = Field(default=None, description="The command it runs, if it runs one.")
    remote: bool = Field(description="Whether it changes something outside this machine (a push, a pull request).")
    confirm: bool = Field(
        default=False, description="Whether it waits for your yes although it is local, e.g. making a team yours."
    )
    done: bool = Field(default=False, description="Whether it has been done.")


class MemberView(BaseModel):
    """A member as `hz team` shows them."""

    handle: str = Field(description="Their handle.")
    github: str | None = Field(description="Their GitHub login.")
    roles: list[str] = Field(description="Their roles.")
    owner: bool = Field(description="Whether they own the roster.")
    since: date = Field(description="When they joined.")


class TeamPlan(BaseModel):
    """What `hz team new`, `join` or `add` does: without `--yes` the plan, with it what was done."""

    operation: Literal["new", "join", "add"] = Field(description="The operation.")
    team: str | None = Field(description="The team's name, once known.")
    repository: str | None = Field(description="The team repository's URL; none for one on this machine only.")
    checkout: Path | None = Field(description="Where the team repository is checked out.")
    handle: str = Field(description="The handle of the person created, found or added; empty until known.")
    actions: list[Action] = Field(default_factory=list[Action], description="Every step, in order, and if it was done.")
    done: bool = Field(default=False, description="Whether every step was carried out, or the rest only shown.")
    identities: list[str] = Field(default_factory=list[str], description="Who you are signed in as, `github:<login>`.")
    member: MemberView | None = Field(default=None, description="You, as the roster lists you, when `join` found you.")
    candidates: list[str] = Field(
        default_factory=list[str],
        description="Roster entries without a login that may be you, when `join` found no match: `--handle <entry>`.",
    )
    drafted: list[MemberView] = Field(
        default_factory=list[MemberView], description="The roster `new` drafted for you to review, beside you."
    )
    plugin: str | None = Field(default=None, description="The team plugin the manifest names; nothing installs it.")
    pull_request: str | None = Field(default=None, description="The pull request opened, if one was.")
    next: list[str] = Field(default_factory=list[str], description="What to do next.")


class TeamShow(BaseModel):
    """Your team as configured and as its repository says."""

    repository: str | None = Field(description="The team repository's URL.")
    checkout: Path | None = Field(description="Its local checkout.")
    exists: bool = Field(description="Whether the checkout exists.")
    name: str | None = Field(default=None, description="The team's name from `hz.yaml`.")
    plugin: str | None = Field(default=None, description="The team plugin the manifest names.")
    members: list[MemberView] = Field(default_factory=list[MemberView], description="Active members.")
    problems: list[str] = Field(default_factory=list[str], description="What is wrong with the checkout or its files.")
    next: list[str] = Field(default_factory=list[str], description="What to do next.")


class Adopted(BaseModel):
    """An existing checkout recorded as your team repository by `hz team adopt`."""

    repository: str | None = Field(description="Its `origin` URL, if it has one.")
    checkout: Path = Field(description="The checkout.")
    written: list[str] = Field(description="What was set and removed.")
    next: list[str] = Field(default_factory=list[str], description="What to do next.")
