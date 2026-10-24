"""Settings, the files of a team repository, and the results of `hz team` (proposed onboarding ADR)."""

import re
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from heinzel.kernel import CAPABILITIES, ModuleSettings, RecommendationKind
from heinzel.modules.team._repo import check_url

if TYPE_CHECKING:
    from collections.abc import Callable

ROLES = ("engineering-manager", "engineer", "product-manager", "program-manager")
"""The core role vocabulary, as in [org] (EM, IC, PM, TPM); layers' plugins add roles later, never rename these."""
OWNER_ROLES = ("engineering-manager", "engineering-leadership", "product-leadership")
"""Roles that own the roster by default; the creator of a team is its first owner whatever their role. The retired
leadership roles stay here, so rosters that name them keep their owners."""
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
RECOMMEND_KEYS = frozenset({"plugins", "bindings", "credentials", "contracts"})
CREDENTIAL = re.compile(r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)*/[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
"""A credential as `service/name`, the way doctor names it; only names, never values."""
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

    section_title: ClassVar[str | None] = "Team"  # the heading in Settings

    repository: str | None = Field(
        default=None,
        title="Team repository",
        description="Your team repository's address, the invite others join with; empty for a "
        "team repository that exists only on this computer.",
        examples=["https://github.com/example/team.git"],
    )
    checkout: Path | None = Field(
        default=None,
        title="Team checkout",
        description="The folder your team repository is checked out in; its workflows folder "
        "holds the team's contracts.",
    )

    layer: str | None = Field(
        default=None,
        title="Team layer",
        description="The layer plugin your team uses, by module name, when your team repository "
        "names none or you want another.",
        json_schema_extra={"x-expert": True},
    )

    ignored_in_project: ClassVar[frozenset[str]] = frozenset({"repository", "checkout", "layer"})

    @field_validator("repository")
    @classmethod
    def _url(cls, url: str | None) -> str | None:
        return None if url is None else check_url(url)

    @field_validator("layer")
    @classmethod
    def _layer(cls, layer: str | None) -> str | None:
        return None if layer is None else check_name(layer, "layer name")


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


class Recommend(BaseModel):
    """What a team recommends its members use: shown with its origin, applied only when a member accepts it.

    Read leniently: an entry hz can't check (a capability a later version added, a misspelt name) is dropped with a
    problem `hz team show` reports, and never invalidates the manifest the rest of the team depends on.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    unreadable: list[str] = Field(
        default_factory=list[str], exclude=True, description="Problems with the block's shape, for `hz team show`."
    )
    plugins: object = Field(default_factory=list[object], description="Plugin distributions, `heinzel-<name>`.")
    bindings: object = Field(
        default_factory=dict[str, object], description="Capability to adapter, e.g. `documents: google`."
    )
    credentials: object = Field(
        default_factory=list[object],
        description="Credentials members store themselves, as `service/name`; never values.",
    )
    contracts: object = Field(
        default_factory=list[object], description="Team contracts in workflows/ members may let their daemon run."
    )

    @model_validator(mode="before")
    @classmethod
    def _shape(cls, value: object) -> object:
        """Anything but a mapping is no recommendation; unknown keys are reported, not fatal."""
        if value is None:
            return {}
        mapping = _mapping(value)
        if mapping is None:
            return {"unreadable": ["recommend: must map plugins, bindings, credentials and contracts to their entries"]}
        unknown = sorted(str(key) for key in mapping if key not in RECOMMEND_KEYS)
        return {
            **{str(k): v for k, v in mapping.items()},
            "unreadable": [f"recommend: unknown key {k!r}" for k in unknown],
        }

    def entries(self) -> tuple[list[tuple[RecommendationKind, str, str | None]], list[str]]:
        """The entries hz can check, as (kind, name, value), and a problem for each it can't."""
        found: list[tuple[RecommendationKind, str, str | None]] = []
        problems: list[str] = list(self.unreadable)
        named: tuple[tuple[RecommendationKind, object, Callable[[str], str | None]], ...] = (
            ("plugin", self.plugins, _distribution_problem),
            ("credential", self.credentials, _credential_problem),
            ("contract", self.contracts, _contract_problem),
        )
        for kind, values, check in named:
            for value in cast("list[object]", values) if isinstance(values, list) else [values]:
                if not isinstance(value, str):
                    problems.append(f"recommend: {value!r} isn't a name")
                elif (problem := check(value)) is not None:
                    problems.append(f"recommend: {problem}")
                else:
                    found.append((kind, value, None))
        bindings = _mapping(self.bindings)
        if bindings is None:
            bindings = {}
            problems.append("recommend: bindings must map capabilities to adapters, e.g. documents: google")
        for capability, adapter in bindings.items():
            if not isinstance(capability, str) or capability not in CAPABILITIES:
                problems.append(f"recommend: {capability!r} isn't a capability this hz knows")
            elif not isinstance(adapter, str) or not NAME.fullmatch(adapter):
                problems.append(f"recommend: {adapter!r} isn't the name of an adapter")
            else:
                found.append(("binding", capability, adapter))
        order = {"plugin": 0, "binding": 1, "credential": 2, "contract": 3}
        return sorted(found, key=lambda entry: order[entry[0]]), problems


def _mapping(value: object) -> dict[object, object] | None:
    """`value` as a mapping of anything, when YAML gave one."""
    return cast("dict[object, object]", value) if isinstance(value, dict) else None


def _distribution_problem(name: str) -> str | None:
    return None if DISTRIBUTION.fullmatch(name) else f"{name!r} isn't a plugin distribution (heinzel-<name>)"


def _credential_problem(name: str) -> str | None:
    return None if CREDENTIAL.fullmatch(name) else f"{name!r} isn't a credential name (service/name)"


def _contract_problem(name: str) -> str | None:
    return None if len(name) <= MAX_NAME and NAME.fullmatch(name) else f"{name!r} isn't a contract name"


class Manifest(_Strict):
    """`hz.yaml` at the root of a team repository: what the team is. It configures nobody's machine."""

    kind: Literal["team"] = Field(default="team", description="What the repository is.")
    name: str = Field(description="The team's name, lowercase words joined by dashes.")
    owners: list[str] = Field(
        default_factory=lambda: list(OWNER_ROLES), description="Roles that own the roster (CODEOWNERS of team/)."
    )
    plugin: Plugin | None = Field(default=None, description="The team's plugin, if it has one.")
    recommend: Recommend = Field(
        default_factory=Recommend, description="What the team recommends its members use; nothing is applied by it."
    )

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

    operation: Literal["new", "join", "add", "draft"] = Field(description="The operation.")
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
    guesses: list[str] = Field(
        default_factory=list[str], description="Drafted members the directory only guessed, with what each rests on."
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
