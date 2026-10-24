"""Lets: the typed declarations a module advertises to the kernel.

Each head picks the kinds of lets it understands. The CLI head turns every `OperationLet` into a command; an MCP head
would turn the same operations into tools and a REST head into endpoints, without the modules changing.
"""

import inspect
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator
from rich.console import RenderableType

from heinzel.kernel._capabilities import Mode, Requirement
from heinzel.kernel._config import ModuleSettings

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")
"""Names of modules, jobs and credentials: lowercase words joined by dashes."""
CRON_ALIASES = frozenset({"@yearly", "@annually", "@monthly", "@weekly", "@daily", "@midnight", "@hourly"})
_CRON_FIELD = re.compile(r"^[0-9A-Za-z*/,?LW#-]+$")


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
    """Capabilities the operation asks for by type, besides its module's (ADR 0028); `write` only if not read-only."""

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
        fields = self.schedule.split()
        if self.schedule not in CRON_ALIASES and (len(fields) != 5 or not all(map(_CRON_FIELD.fullmatch, fields))):  # noqa: PLR2004 - minute hour day month weekday
            aliases = ", ".join(sorted(CRON_ALIASES))
            msg = f"Job {self.name!r}: schedule {self.schedule!r} must be five cron fields or one of {aliases}"
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
    """Hosts it sends data to, e.g. `api.github.com`; empty when that depends on its configuration."""
    factory: Callable[..., Any]


@dataclass(frozen=True, kw_only=True, slots=True)
class RequirementLet(Let):
    """A capability a whole module needs, declared with `Module(..., requires=[...])` and named after it (ADR 0028).

    Every operation, job, service, info and health provider of the module may ask for it by type; a `write` mode
    reaches only the module's publish operations (`read_only=False`).
    """

    requirement: Requirement
