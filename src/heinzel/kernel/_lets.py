"""Lets: the typed declarations a module advertises to the kernel.

Each head picks the kinds of lets it understands. The CLI head turns every `OperationLet` into a command; an MCP head
would turn the same operations into tools and a REST head into endpoints, without the modules changing.
"""

import inspect
import ipaddress
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, model_validator
from rich.console import RenderableType

from heinzel.kernel._capabilities import Mode, Requirement
from heinzel.kernel._config import ModuleSettings

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")
"""Names of modules, jobs and credentials: lowercase words joined by dashes."""
CRON_ALIASES = frozenset({"@yearly", "@annually", "@monthly", "@weekly", "@daily", "@midnight", "@hourly"})
_CRON_FIELD = re.compile(r"^[0-9A-Za-z*/,?LW#-]+$", re.ASCII)
_HOST_NAME = re.compile(
    r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$", re.ASCII
)


def schedule_problem(schedule: str) -> str | None:
    """Why `schedule` isn't the shape of a cron expression, or None: five ASCII fields, or an alias like `@daily`.

    The kernel has no cron library, so it checks only the shape; `hz system daemon` and the workflow module check the
    values. ASCII only: Python reads a fullwidth zero (U+FF10) as a digit, a cron library might too, and nobody means
    it.
    """
    fields = schedule.split()
    if schedule in CRON_ALIASES or (len(fields) == 5 and all(map(_CRON_FIELD.fullmatch, fields))):  # noqa: PLR2004 - minute hour day month weekday
        return None
    aliases = ", ".join(sorted(CRON_ALIASES))
    return f"schedule {schedule!r} must be five cron fields (minute hour day month weekday) or one of {aliases}"


def host_of(value: str) -> str | None:
    """The host name in `value`, a host name or a URL, lowercased; None if it names no valid host.

    Scheme, user information, port, path, query and fragment are dropped, so a URL with a token in it never reaches
    `hz system info`. Host names are ASCII (the punycode form of an international name); IP addresses are accepted.
    """
    try:
        host = urlsplit(value if "://" in value else f"//{value}").hostname
    except ValueError:  # malformed IPv6 brackets
        return None
    if not host:
        return None
    if ":" in host:
        try:
            return str(ipaddress.IPv6Address(host))
        except ValueError:
            return None
    return host if _HOST_NAME.fullmatch(host) else None


def settings_parameters(func: Callable[..., Any]) -> dict[str, type[ModuleSettings]]:
    """The parameters of a function that reads only settings, each with the settings class it is annotated with.

    Raises:
        ValueError: If `func` is a class, takes no parameter, or a parameter is variadic or not annotated with a
            `ModuleSettings` subclass.
    """
    name = getattr(func, "__qualname__", repr(func))
    if isinstance(func, type):
        msg = f"Hosts of an adapter come from a function of its settings, not a class ({name})"
        raise ValueError(msg)  # noqa: TRY004 - a declaration error, like the others raised here
    try:
        parameters = inspect.signature(func, eval_str=True).parameters
    except (NameError, TypeError, ValueError) as exc:
        msg = f"Can't read the parameters of {name}: {exc}"
        raise ValueError(msg) from exc
    found: dict[str, type[ModuleSettings]] = {}
    for parameter in parameters.values():
        annotation = parameter.annotation
        if parameter.kind in {parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD, parameter.POSITIONAL_ONLY} or not (
            isinstance(annotation, type) and issubclass(annotation, ModuleSettings)
        ):
            msg = (
                f"{name} may only take its module's settings, by type (`settings: MySettings`), so that reading "
                f"hosts stays cheap and offline; {parameter.name!r} is not one"
            )
            raise ValueError(msg)
        found[parameter.name] = annotation
    if not found:
        msg = f"{name} takes no settings; give a list of hosts instead"
        raise ValueError(msg)
    return found


@dataclass(frozen=True, kw_only=True, slots=True)
class Let:
    """Base class of everything a module can advertise."""

    name: str


@dataclass(frozen=True, kw_only=True, slots=True)
class OperationLet(Let):
    """A typed function that every head exposes.

    Parameters are described with `Annotated[T, pydantic.Field(description=...)]`, so each head can derive its own
    interface: CLI arguments and options, an MCP tool schema, a REST request model. Parameters whose type the kernel
    provides (the kernel, settings, services) are injected and never exposed.
    """

    func: Callable[..., Any]
    summary: str
    read_only: bool = True
    render: Callable[[Any], RenderableType] | None = None
    """Presentation hint for human-facing heads. Machine-facing heads always use the JSON form of the result."""
    exit_code: Callable[[Any], int] | None = None
    """For heads with exit codes: the code for a result, e.g. non-zero when a health report is DOWN."""
    heads: frozenset[str] | None = None
    """Heads that expose the operation (`"cli"`, `"mcp"`, `"api"`); every head when None."""
    requires: tuple[Requirement, ...] = ()
    """Capabilities the operation asks for by type, besides its module's (ADR 0028); `write` only if not read-only.

    `write` doesn't imply `read`: a publish operation that also reads declares `documents:read,write`."""

    def __post_init__(self) -> None:
        """Refuse a read-only operation that declares a write.

        Raises:
            ValueError: If a read-only operation requires a `write` mode.
        """
        if self.read_only and (writes := [str(r) for r in self.requires if "write" in r.modes]):
            msg = (
                f"Operation {self.name!r} can't require {', '.join(writes)}: writes happen only in publish operations "
                f"(read_only=False)"
            )
            raise ValueError(msg)

    def signature(self) -> inspect.Signature:
        """The signature of `func`, with string annotations resolved."""
        return inspect.signature(self.func, eval_str=True)


@dataclass(frozen=True, kw_only=True, slots=True)
class ServiceLet(Let):
    """A service the kernel injects, by type, into operations, info providers and other services."""

    provides: type[Any]
    factory: Callable[..., Any]


@dataclass(frozen=True, kw_only=True, slots=True)
class SettingsLet(Let):
    """Settings a module declares: read from its TOML table and `HZ_<MODULE>__*` variables, injected by type."""

    model: type[ModuleSettings]


class Status(StrEnum):
    """Health of one check, or of everything: DOWN beats DEGRADED beats UP."""

    UP = "up"
    DEGRADED = "degraded"
    DOWN = "down"


class Check(BaseModel):
    """The outcome of one health check. Anything but UP must say what is wrong."""

    name: str = Field(description="What was checked.")
    status: Status = Field(description="Outcome: up, degraded or down.")
    detail: str = Field(default="", description="What was found; required unless UP.")
    fix: str = Field(default="", description="What to do about it, when not UP.")

    @model_validator(mode="after")
    def _explain_problems(self) -> Self:
        if self.status is not Status.UP and not self.detail:
            msg = f"Check {self.name!r} is {self.status.value} and needs a detail saying why"
            raise ValueError(msg)
        return self


@dataclass(frozen=True, kw_only=True, slots=True)
class HealthLet(Let):
    """Health checks for `hz system doctor`. The provider returns checks and may ask for injected services."""

    provider: Callable[..., list[Check]]


class CatchUp(StrEnum):
    """What a job does about schedule slots it missed while the machine slept or was off."""

    LATEST = "latest"
    """Run once, for the most recent missed slot."""
    ALL = "all"
    """Run once per missed slot, oldest first."""
    SKIP = "skip"
    """Run only for a slot that fell within the last tick interval; record the missed ones as skipped."""


@dataclass(frozen=True, kw_only=True, slots=True)
class JobLet(Let):
    """Work the daemon runs on a schedule (ADR 0020). `func` is injected like an operation and never prints.

    The kernel checks the shape of the cron expression (five fields, or an alias like `@daily`) when the job is
    declared; `hz system daemon` checks the values and computes the slots, since the kernel has no cron library.
    """

    func: Callable[..., Any]
    summary: str
    schedule: str
    """A five-field cron expression in local time, e.g. `0 9 * * MON`."""
    scope: Literal["personal"] = "personal"
    """`personal` jobs run on their owner's daemon; team jobs follow with the shared state repository."""
    timeout: timedelta = timedelta(minutes=15)
    retries: int = 0
    """How often a failing run is retried within the same tick."""
    catch_up: CatchUp = CatchUp.LATEST
    idempotency: Callable[[datetime], str] | None = None
    """The key of the run for a schedule slot; `<job>@<slot>` when None. A slot with a rendered key never reruns."""
    requires: tuple[Requirement, ...] = ()
    """Capabilities the job asks for by type, besides its module's (ADR 0028); always read-only."""

    def __post_init__(self) -> None:
        """Refuse a job the daemon could never run.

        Raises:
            ValueError: If the name, schedule, timeout or retries are invalid.
        """
        if not NAME_PATTERN.fullmatch(self.name):
            msg = f"Job name {self.name!r} must match {NAME_PATTERN.pattern}"
            raise ValueError(msg)
        if problem := schedule_problem(self.schedule):
            msg = f"Job {self.name!r}: {problem}"
            raise ValueError(msg)
        if self.timeout <= timedelta(0):
            msg = f"Job {self.name!r}: timeout must be positive, not {self.timeout}"
            raise ValueError(msg)
        if self.retries < 0:
            msg = f"Job {self.name!r}: retries must be 0 or more, not {self.retries}"
            raise ValueError(msg)
        if writes := [str(r) for r in self.requires if "write" in r.modes]:
            msg = f"Job {self.name!r} can't require {', '.join(writes)}: writes happen only in publish operations"
            raise ValueError(msg)


@dataclass(frozen=True, kw_only=True, slots=True)
class CredentialLet(Let):
    """A secret a module needs (ADR 0030); `Credentials` hands out its value, `hz system credential` stores it."""

    purpose: str
    """What the credential is for and where to get one, shown by `hz system credential list` and doctor."""
    service: str | None = None
    """Where it is kept; the qualified name of the declaring module when None. Name another to share one."""


@dataclass(frozen=True, kw_only=True, slots=True)
class InfoLet(Let):
    """A section contributed to `hz system info`. The provider may ask for injected services."""

    provider: Callable[..., Mapping[str, Any] | BaseModel]


@dataclass(frozen=True, kw_only=True, slots=True)
class ProviderLet(Let):
    """An adapter: implements a capability's protocol, in some modes, reaching some hosts (ADR 0028).

    Its name is `<adapter>:<capability>`. Configuration binds a capability to an adapter by the adapter's name
    (`[capabilities] tracker = "github"`); the kernel builds one instance from `factory`, injecting its parameters.
    """

    adapter: str
    """The name bindings use, e.g. `github` or `claude-code`."""
    capability: str
    """The capability implemented, e.g. `tracker`."""
    modes: frozenset[Mode]
    """The modes it serves; a requester never gets a mode its adapter lacks."""
    hosts: tuple[str, ...] = ()
    """Hosts it sends data to, e.g. `api.github.com`; empty when its configuration decides them."""
    factory: Callable[..., Any]
    configured_hosts: Callable[..., Iterable[str]] | None = None
    """When its settings decide the hosts: a function of the adapter module's settings, injected by type, returning
    host names or URLs. The kernel calls it when it builds the capability matrix and shows only host names."""

    def __post_init__(self) -> None:
        """Refuse hosts that aren't host names, and a hosts function that reads more than settings.

        Raises:
            ValueError: If a host isn't a lowercase host name, both forms are given, or the function is invalid.
        """
        if bad := [host for host in self.hosts if host_of(host) != host]:
            msg = f"Adapter {self.name!r}: hosts must be host names such as api.example.com, not {bad}"
            raise ValueError(msg)
        if self.configured_hosts is not None:
            if self.hosts:
                msg = f"Adapter {self.name!r}: give hosts as a list or as a function of settings, not both"
                raise ValueError(msg)
            settings_parameters(self.configured_hosts)


@dataclass(frozen=True, kw_only=True, slots=True)
class RequirementLet(Let):
    """A capability a whole module reads, declared with `Module(..., requires=[...])` and named after it (ADR 0028).

    Every operation, job, service, info and health provider of the module may ask for it by type. Only read modes:
    `write` is declared on the publish operation (`read_only=False`) that writes.
    """

    requirement: Requirement


@dataclass(frozen=True, kw_only=True, slots=True)
class SetupLet(Let):
    """An interactive setup step (ADR 0032), named after its module: `hz <module> setup`, and part of `hz setup`.

    `func` is injected like an operation and asks its questions through `Prompter`; it writes settings through
    `ConfigEditor` and secrets through `Credentials`. Only interactive heads run it.
    """

    func: Callable[..., Any]
    summary: str
    required: bool = False
    """Whether `hz setup` runs it without offering to skip it."""
