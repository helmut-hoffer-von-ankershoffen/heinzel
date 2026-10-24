"""Telemetry: what each Heinzelmännchen says it did, one entry per action, metadata only (ADR 0054).

A Heinzelmännchen reports what it read, drafted, had approved, published or fell back on, and that it is alive, through
`hz report`. Each one appends to its own log, a JSON Lines file only it writes, and the log is copied into the team's
repository so every member's hz can tell whether the team's promises are kept. That makes an entry team data: it holds
who (a Heinzelmännchen's id and its user's roster handle), when, what kind of action and on which artifact, never the
artifact's content, a message body, a token or anyone else's address. The model refuses what doesn't fit.

The kernel holds the entry's shape so that the module writing logs (`telemetry`, command `hz report`) and the module
reading them (`graph`) agree on it without importing each other; `TelemetryLog` is how a reader asks for the entries.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Self
from urllib.parse import parse_qsl, urlsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

Verb = Literal["read", "drafted", "approved", "published", "fell_back", "heartbeat"]
VERBS: tuple[Verb, ...] = ("read", "drafted", "approved", "published", "fell_back", "heartbeat")
HEINZEL_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
"""A Heinzelmännchen's id for now: a plain slug, which is also its log's file name; it becomes the durable id."""
HANDLE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
"""A roster handle, as the team module writes them: lowercase letters and digits with single dashes."""
TEAM_CONTRACT = re.compile(r"^team:[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_CONTRACT_REF = re.compile(r"^contract:team/[a-z][a-z0-9]*(?:-[a-z0-9]+)*#[a-z][a-z0-9_]*$")
_EMAIL = re.compile(r"^[A-Za-z0-9._%+'-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){0,8}\.[A-Za-z]{2,24}$")
_CHANNEL = re.compile(r"^chat:[#@]?[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}$")
_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_CONTROLS = re.compile(r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff]")
_ADDRESS = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]+")
_SECRETS = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|sk-[A-Za-z0-9_-]{20,}"
    r"|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}|-----BEGIN"
)
"""What looks like a credential wherever it is: well-known token prefixes, a JWT, a PEM block."""
_RUN = re.compile(r"[A-Za-z0-9+/_-]{32,}")
"""In free text, a long run of key alphabet mixing cases and digits is a key, not a word (a hex hash is fine)."""
_SECRET_PARAMETERS = re.compile(r"(?i)token|key|secret|sig|signature|password|passwd|auth|code|credential|session")
WEBHOOK_HOSTS = re.compile(
    r"(?i)^(?:hooks\.slack\.com|discord(?:app)?\.com|[a-z0-9-]+\.webhook\.office\.com|outlook\.office\.com"
    r"|chat\.googleapis\.com|hooks\.zapier\.com)$"
)
"""Hosts whose URLs are credentials themselves: whoever has one can post as you."""
MAX_DESCRIPTION = 280
MAX_TARGET = 2048
TARGET_FORMS = (
    "an https URL (no user name, password or credential in it), an email address published to, a team contract's "
    "output as "
    "`contract:team/<name>#<output>`, or a chat channel as `chat:<channel>`"
)


def _secret_problem(text: str, what: str, *, free_text: bool = False) -> None:
    runs = _RUN.findall(text) if free_text else []
    mixed = any(
        any(c.isupper() for c in r) and any(c.islower() for c in r) and any(c.isdigit() for c in r) for r in runs
    )
    if mixed or _SECRETS.search(text):
        msg = f"{what} looks like it holds a credential; report what happened, never a token or key"
        raise ValueError(msg)


def check_target(target: str) -> str:
    """`target` if it is a place a team member can name: a URL, an address, a contract output or a channel.

    Local paths are refused: a draft on your disk is yours, not the team's, and its path names your account.

    Raises:
        ValueError: Saying which forms a target takes.
    """
    if len(target) > MAX_TARGET or _CONTROLS.search(target) or any(c.isspace() for c in target):
        msg = f"target {target[:80]!r} must be one line without spaces, at most {MAX_TARGET} characters"
        raise ValueError(msg)
    if _EMAIL.fullmatch(target) or _CONTRACT_REF.fullmatch(target) or _CHANNEL.fullmatch(target):
        return target
    parts = urlsplit(target)
    if parts.scheme == "https" and parts.hostname and "@" not in parts.netloc:
        if WEBHOOK_HOSTS.fullmatch(parts.hostname):
            msg = f"target {parts.hostname} is a webhook: its URL is a credential; name the channel as chat:<channel>"
            raise ValueError(msg)
        parameters = [*parse_qsl(parts.query, keep_blank_values=True), *parse_qsl(parts.fragment)]
        if any(_SECRET_PARAMETERS.search(key) for key, _ in parameters):
            msg = f"target {target[:80]!r} carries a credential-like parameter; report the URL without it"
            raise ValueError(msg)
        _secret_problem(target, "target")
        return target
    msg = f"target {target[:80]!r} must be {TARGET_FORMS}"
    raise ValueError(msg)


class TelemetryEntry(BaseModel):
    """One action a Heinzelmännchen reports: who, when, which verb, on what; metadata only, never content.

    Verb-specific fields: `read` may carry the `version` read and `needed_by`, when the reader's contract needs it;
    `drafted` the `contract_version` it drafted from; `approved` who approved it (`by`); `published` the `version`
    published; `fell_back` the stale `input` it used. `heartbeat` has no target. `part` names the part of the target
    the action concerns, e.g. the row of a tracker one person keeps.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    v: Literal[1] = Field(default=1, description="The entry format's version.")
    heinzel_id: str = Field(
        pattern=HEINZEL_ID.pattern,
        description="The Heinzelmännchen's id: a slug, unique in your team, e.g. `costas-tracker`; it becomes the "
        "durable id of a Heinzelmännchen once those exist.",
    )
    user: str = Field(pattern=HANDLE.pattern, max_length=39, description="Its user's handle in the team's roster.")
    time: AwareDatetime = Field(description="When it happened, with its offset; stored in UTC.")
    verb: Verb = Field(description="What it did: read, drafted, approved, published, fell_back or heartbeat.")
    target: str | None = Field(
        default=None, description=f"What it acted on: {TARGET_FORMS}. None only for a heartbeat."
    )
    description: str = Field(
        default="",
        max_length=MAX_DESCRIPTION,
        description="What a reader of the graph should know, in one line; never content, names or addresses.",
    )
    contract: str | None = Field(
        default=None,
        pattern=TEAM_CONTRACT.pattern,
        description="The team contract the action keeps, `team:<name>`; personal contracts never appear here.",
    )
    part: str | None = Field(
        default=None, pattern=_SLUG.pattern, description="The part of the target it concerns, e.g. a tracker's row."
    )
    version: str | None = Field(
        default=None,
        pattern=_TOKEN.pattern,
        description="The artifact's version read or published, as its source has it.",
    )
    needed_by: AwareDatetime | None = Field(
        default=None, description="For `read`: by when the reader's contract needs the artifact."
    )
    contract_version: str | None = Field(
        default=None, pattern=_TOKEN.pattern, description="For `drafted`: the contract's version or `sha256:<hex>`."
    )
    by: str | None = Field(
        default=None, pattern=HANDLE.pattern, max_length=39, description="For `approved`: who approved, by handle."
    )
    input: str | None = Field(default=None, description="For `fell_back`: the input whose stale version it used.")

    @field_validator("time", "needed_by")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    @field_validator("target", "input")
    @classmethod
    def _place(cls, value: str | None) -> str | None:
        return check_target(value) if value is not None else None

    @field_validator("version", "contract_version")
    @classmethod
    def _plain_version(cls, value: str | None) -> str | None:
        if value is not None:
            _secret_problem(value, "version", free_text=True)
        return value

    @field_validator("description")
    @classmethod
    def _metadata(cls, value: str) -> str:
        if _CONTROLS.search(value):
            msg = "description must be one line of plain text"
            raise ValueError(msg)
        if _ADDRESS.search(value):
            msg = "description must not hold an email address; name people by their handle"
            raise ValueError(msg)
        _secret_problem(value, "description", free_text=True)
        return value.strip()

    @model_validator(mode="after")
    def _verb_fields(self) -> Self:
        allowed: dict[Verb, frozenset[str]] = {
            "read": frozenset({"version", "needed_by"}),
            "drafted": frozenset({"contract_version"}),
            "approved": frozenset({"by"}),
            "published": frozenset({"version"}),
            "fell_back": frozenset({"input"}),
            "heartbeat": frozenset(),
        }
        given = {name for name in ("version", "needed_by", "contract_version", "by", "input") if getattr(self, name)}
        if extra := sorted(given - allowed[self.verb]):
            msg = f"`{self.verb}` takes no {', '.join(extra)}"
            raise ValueError(msg)
        if self.verb == "heartbeat":
            if self.target is not None or self.part is not None:
                msg = "a heartbeat says only that the Heinzelmännchen is alive: remove target and part"
                raise ValueError(msg)
        elif self.target is None:
            msg = f"`{self.verb}` needs a target: {TARGET_FORMS}"
            raise ValueError(msg)
        if self.target is not None and _EMAIL.fullmatch(self.target) and self.verb != "published":
            msg = "an email address is a target only where something was published to it"
            raise ValueError(msg)
        if self.input is not None and _EMAIL.fullmatch(self.input):
            msg = "an input is an artifact, never an email address"
            raise ValueError(msg)
        if self.verb == "approved" and self.by is None:
            msg = "`approved` needs `by`: the handle of who approved"
            raise ValueError(msg)
        if self.verb == "fell_back" and self.input is None:
            msg = "`fell_back` needs `input`: the input whose stale version was used"
            raise ValueError(msg)
        return self


@dataclass(frozen=True, slots=True)
class TelemetryRead:
    """Every entry a reader may see, and what was skipped and why (a line someone else's hz wrote, or a broken one)."""

    entries: tuple[TelemetryEntry, ...] = ()
    problems: tuple[str, ...] = ()


class TelemetryLog:
    """Where the entries of a team's Heinzelmännchen are read: a module provides a subclass (`telemetry` in the core).

    A base class rather than a protocol, so that the kernel can inject it by type, like `OrgContext`.
    """

    def entries(self) -> TelemetryRead:
        """The team's entries: those synced to the team repository, and your own local ones, oldest first.

        Raises:
            NotImplementedError: Always; subclasses read the logs.
        """
        raise NotImplementedError
