"""Models of the org module: your profile in `[org]` and what `hz org show` reports (ADR 0024)."""

import json
import re
from collections.abc import Callable
from typing import Annotated, Any, ClassVar, Literal, Self, get_args
from zoneinfo import available_timezones

from pydantic import BaseModel, Field, JsonValue, ValidationInfo, WithJsonSchema, field_validator
from pydantic_settings import NoDecode

from heinzel import ModuleSettings

ROLES = ("engineering-manager", "engineer", "product-manager", "program-manager")
"""The four roles hz offers: EM, IC, PM and TPM. A team's layers may add roles, never rename these.

A company's levels (such as IC1 to IC7) are a separate dimension, to come later and overridable by a company plugin;
they are not roles."""
RETIRED_ROLES = frozenset({"principal", "engineering-leadership", "product-leadership"})
"""Roles hz offered before: a profile that names one still loads, shows the role as not set, and doctor asks for one
of the four."""
ROLE_MEANINGS = {
    "engineering-manager": "leads a team of engineers",
    "engineer": "builds and runs the product",
    "product-manager": "decides what a team builds",
    "program-manager": "coordinates work across teams",
}

ROLE_LABELS: dict[str, JsonValue] = {
    "engineering-manager": "EM · Engineering Manager",
    "engineer": "IC · Engineer",
    "product-manager": "PM · Product Manager",
    "program-manager": "TPM · Technical Program Manager",
}
"""How Settings names each role."""
ROLE_PICK = "pick EM, IC, PM or TPM"

CodingAgent = Literal["claude-code", "command", "none"]
CODING_AGENTS: tuple[CodingAgent, ...] = get_args(CodingAgent)

HANDLE = re.compile(r"[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9])){0,38}")
"""Lowercase letters, digits and single dashes inside, at most 39 characters: what GitHub allows for a login."""
GITHUB_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}")
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
HOURS = re.compile(r"(\d{1,2})\s*[-,]\s*(\d{1,2})")  # 09-18, or 9,18 as a list in an answers file gives it
DEFAULT_HOURS = (9, 18)
Hour = Annotated[int, Field(ge=0, le=24)]
"""An hour of the day; 24 ends a working day at midnight."""
TimeZoneName = Annotated[str, Field(json_schema_extra={"format": "iana-timezone"})]
"""An IANA time zone name; the format tells a form to offer the known zones."""


def handle_problem(handle: str) -> str | None:
    """Why `handle` can't be a handle, or None when it can."""
    if HANDLE.fullmatch(handle):
        return None
    return (
        f"{handle!r} is not a handle: use lowercase letters, digits and single dashes inside, at most 39 characters, "
        "e.g. mira or mira-example"
    )


def github_problem(login: str) -> str | None:
    """Why `login` can't be a GitHub login, or None when it can."""
    if GITHUB_LOGIN.fullmatch(login):
        return None
    return f"{login!r} is not a GitHub login: letters, digits and single dashes inside, at most 39 characters"


def email_problem(email: str) -> str | None:
    """Why `email` can't be an email address, or None when it can."""
    return None if EMAIL.fullmatch(email) else f"{email!r} is not an email address, e.g. mira@example.com"


def role_problem(role: str, roles: tuple[str, ...] = ROLES) -> str | None:
    """Why `role` isn't one of `roles`, or None when it is."""
    return None if role in roles else f"{role!r} is not a role; one of: {', '.join(roles)}"


def timezone_problem(name: str) -> str | None:
    """Why `name` isn't an IANA time zone, or None when it is."""
    return None if name in available_timezones() else f"{name!r} is not an IANA time zone, e.g. Europe/Berlin"


def parse_hours(value: object) -> tuple[int, int]:
    """Working hours from `"08-17"` or `[8, 17]`.

    Raises:
        ValueError: If it is neither, or not a working day: 0 <= start < end <= 24.
    """
    pair = _pair(value)
    if pair is None:
        msg = f"{value!r} is not working hours; write them as start-end, e.g. 09-18"
        raise ValueError(msg)
    start, end = pair
    if not 0 <= start < end <= 24:  # noqa: PLR2004 - hours of a day
        msg = f"working hours must be start-end with 0 <= start < end <= 24, not {start:02d}-{end:02d}"
        raise ValueError(msg)
    return start, end


def _pair(value: object) -> tuple[int, int] | None:
    match value:
        case str() if value.strip().startswith("["):  # a JSON list, as HZ_ORG__WORKING_HOURS='[9, 18]' gives it
            try:
                return _pair(json.loads(value))
            except ValueError:
                return None
        case str():
            found = HOURS.fullmatch(value.strip())
            return (int(found[1]), int(found[2])) if found else None
        case [int() as start, int() as end] if not isinstance(start, bool) and not isinstance(end, bool):
            return start, end
        case _:
            return None


def format_hours(hours: tuple[int, int]) -> str:
    """Working hours as `09-18`."""
    return f"{hours[0]:02d}-{hours[1]:02d}"


class OrgSettings(ModuleSettings):
    """Your profile: who you are to hz. Set by `hz setup`, kept in your user configuration, never in a repository.

    A project `hz.toml` can't set any of it: a repository someone else controls must not decide who you are.
    """

    section_title: ClassVar[str | None] = "Your profile"  # the heading in Settings

    handle: str | None = Field(
        default=None,
        title="Handle",
        description="Your short name in teams, pull requests and contracts: lowercase letters, digits and dashes. "
        "Your GitHub login is a good choice.",
        examples=["mira"],
    )
    role: Annotated[str, WithJsonSchema({"type": "string", "enum": list(ROLES)})] | None = Field(
        default=None,
        title="Role",
        description="What you do on your team.",
        json_schema_extra={"x-labels": ROLE_LABELS},
    )
    timezone: TimeZoneName | None = Field(default=None, title="Time zone", description="Your time zone.")
    working_hours: Annotated[tuple[Hour, Hour], NoDecode] = Field(
        default=DEFAULT_HOURS,
        title="Working hours",
        description="When your working day starts and ends. In the environment, write them as 09-18.",
        json_schema_extra={"x-unit": "hour-of-day"},
    )
    github: str | None = Field(
        default=None,
        title="GitHub login",
        description="Your GitHub login, needed to approve and own contracts in a team.",
        examples=["mira"],
    )
    coding_agent: CodingAgent | None = Field(
        default=None,
        title="Coding agent",
        description="The coding agent you write drafts with.",
        json_schema_extra={"x-labels": {"claude-code": "Claude Code", "command": "Your own command", "none": "None"}},
    )
    name: str | None = Field(
        default=None,
        title="Name",
        description="Your name, from your Google or GitHub sign-in. It stays on this computer, never in a repository.",
    )
    email: str | None = Field(
        default=None,
        title="Work email",
        description="Your work email, from your Google sign-in. It stays on this computer, never in a repository.",
        json_schema_extra={"format": "email"},
        examples=["mira@example.com"],
    )

    ignored_in_project: ClassVar[frozenset[str]] = frozenset(
        {"handle", "role", "timezone", "working_hours", "github", "coding_agent", "name", "email"}
    )

    @field_validator("working_hours", mode="before")
    @classmethod
    def _hours(cls, value: Any) -> Any:  # noqa: ANN401 - whatever the configuration holds
        return parse_hours(value)

    @classmethod
    def refuse_written(cls, name: str, value: object) -> str | None:
        """A retired role loads from a file that names it, but is never written again."""
        if name == "role" and value in RETIRED_ROLES:
            return f"role {value} is no longer offered; {ROLE_PICK}"
        return None

    @field_validator("handle", "role", "timezone", "github", "email")
    @classmethod
    def _valid(cls, value: str | None, info: ValidationInfo) -> str | None:
        # One validator per field, not per model, so an error names the key that `hz system config unset` resets.
        check = FIELD_CHECKS[info.field_name or ""]
        if (
            value is not None
            and not (info.field_name == "role" and value in RETIRED_ROLES)
            and (problem := check(value))
        ):
            raise ValueError(problem)
        return value


FIELD_CHECKS: dict[str, Callable[[str], str | None]] = {
    "handle": handle_problem,
    "role": role_problem,
    "timezone": timezone_problem,
    "github": github_problem,
    "email": email_problem,
}

REQUIRED = ("handle", "role", "timezone")
"""What a profile needs before you can join a team."""


class Profile(BaseModel):
    """Your profile as hz knows it, and what is still missing."""

    handle: str | None = Field(description="Your handle, e.g. mira; none until you set it.")
    role: str | None = Field(description="Your role, e.g. engineering-manager.")
    timezone: str | None = Field(description="Your IANA time zone, e.g. Europe/Berlin.")
    working_hours: str = Field(description="Your working hours there, e.g. 09-18.")
    working_hours_set: bool = Field(description="Whether you chose the working hours; false while the default holds.")
    github: str | None = Field(description="Your GitHub login.")
    coding_agent: CodingAgent | None = Field(description="The coding agent you draft with.")
    name: str | None = Field(description="Your name; on this machine only.")
    email: str | None = Field(description="Your work email; on this machine only.")
    complete: bool = Field(description="Whether handle, role and time zone are set.")
    missing: list[str] = Field(description="Settings still missing, e.g. org.handle.")
    fix: str = Field(description="What to run to complete or change the profile.")
    retired_role: str | None = Field(
        default=None, description="A role the profile names that hz no longer offers; the role counts as not set."
    )

    @classmethod
    def of(cls, settings: OrgSettings) -> Self:
        """The profile in `settings`."""
        retired = settings.role if settings.role in RETIRED_ROLES else None
        role = None if retired else settings.role
        missing = [f"org.{name}" for name in REQUIRED if (role if name == "role" else getattr(settings, name)) is None]
        return cls(
            handle=settings.handle,
            role=role,
            retired_role=retired,
            timezone=settings.timezone,
            working_hours=format_hours(settings.working_hours),
            working_hours_set="working_hours" in settings.model_fields_set,
            github=settings.github,
            coding_agent=settings.coding_agent,
            name=settings.name,
            email=settings.email,
            complete=not missing,
            missing=missing,
            fix="hz setup",
        )
