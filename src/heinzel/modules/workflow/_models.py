"""Models of the workflow module: the contract frontmatter (ADR 0021), settings, and what operations return.

The frontmatter models are the source of truth for the format; `docs/schemas/contract-frontmatter-1.json` is generated
from them. They implement the 0.2 subset of ADR 0021: keys the ADR names but 0.2 doesn't build are refused with a
message saying so, never silently ignored.
"""

import inspect
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal, Self, cast, get_type_hints

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    PlainSerializer,
    StrictInt,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    WithJsonSchema,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from heinzel import AgentResult, ModuleSettings, Window
from heinzel.kernel import CAPABILITIES, Requirement, parse_duration, schedule_problem, user_config_dir
from heinzel.modules.workflow._measure import Measurements

SCHEMA_VERSION = 1
"""The version of the frontmatter format this Heinzelmännchen reads (`hz.schema`)."""
SUFFIX = ".prose.md"
"""Contract files are `<name>.prose.md`."""
RUNNER = "agent-runner"
"""Every contract renders with an agent session, so it always requires `agent-runner:read`."""
MAX_TIMEOUT = timedelta(hours=2)
RUNNER_IS_CONFIGURATION = (
    "the runner and model are configuration: `[capabilities] agent_runner`, `[system.runner] model`"
)
GUIDE = "https://github.com/helmut-hoffer-von-ankershoffen/heinzel/blob/main/docs/WORKFLOWS.md"
"""The guide every empty state and starter contract points at: the journey and the contract reference."""

NAME = r"^[a-z](?:-?[a-z0-9])*$"
"""Contract names: lowercase words joined by single dashes; a subset of the kernel's job names."""
APPROVAL = r"^[a-z](?:-?[a-z0-9])*(?:@sha256:[0-9a-f]{64})?$"
IDENT = r"^[a-z][a-z0-9_]*$"
Slug = Annotated[str, StringConstraints(pattern=NAME, max_length=64)]
Ident = Annotated[str, StringConstraints(pattern=IDENT, max_length=64)]


def _format_duration(value: timedelta) -> str:
    seconds = int(value.total_seconds())
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds % size == 0:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


Duration = Annotated[
    timedelta,
    BeforeValidator(parse_duration),
    PlainSerializer(_format_duration, return_type=str),
    WithJsonSchema({"type": "string", "pattern": r"^[1-9][0-9]*[smhd]$", "examples": ["7d", "10m"]}),
]
"""A duration as people write it: `30s`, `10m`, `2h`, `7d`."""


def deferred(key: str, why: str) -> PydanticCustomError:
    """The error for a key ADR 0021 names that this version doesn't build yet."""
    return PydanticCustomError("deferred", "`{key}` is not supported yet: {why}", {"key": key, "why": why})


def at(path: tuple[str | int, ...], message: str) -> PydanticCustomError:
    """An error about the value at `path` below the model raising it, so the diagnostic points at its line."""
    return PydanticCustomError("contract", "{message}", {"message": message, "path": list(path)})


class _Strict(BaseModel):
    """Frontmatter parts: unknown keys are errors, keys ADR 0021 names but 0.2 lacks say so."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)
    deferred_keys: ClassVar[dict[str, str]] = {}

    @model_validator(mode="before")
    @classmethod
    def _refuse_deferred(cls, data: Any) -> Any:  # noqa: ANN401 - raw YAML
        keys = list(cast("dict[str, Any]", data)) if isinstance(data, dict) else []
        for key in keys:
            if key in cls.deferred_keys:
                raise deferred(key, cls.deferred_keys[key])
        return data  # pyright: ignore[reportUnknownVariableType] - raw YAML, validated next


class Input(_Strict):
    """Data fetched through a capability's read method before the agent session starts (ADR 0029, option a)."""

    deferred_keys: ClassVar[dict[str, str]] = {
        "source": "inputs name a capability read method (`read`), never a vendor URI (ADR 0028)",
        "query": "pass the method's arguments in `args`",
        "facets": "input facets come with fingerprints and memo keys",
        "stage": "contract-to-contract inputs come with the contract graph",
        "required": "every input is required in this version",
    }

    read: str = Field(
        pattern=r"^[a-z][a-z0-9-]*\.[a-z][a-z0-9_]*$",
        description="The capability read method that fetches it, `<capability>.<method>`, e.g. "
        "`tracker.issues_updated`; `hz system capability list` shows the capabilities.",
    )
    args: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="Arguments of the method besides its time window, e.g. `{project: APP}`.",
    )
    window: Duration | None = Field(
        default=None,
        description="For methods that take a time window: how far back from the run, e.g. `7d`.",
    )

    @property
    def capability(self) -> str:
        """The capability it reads, e.g. `tracker`."""
        return self.read.partition(".")[0]

    @property
    def method(self) -> str:
        """The method it calls, e.g. `issues_updated`."""
        return self.read.partition(".")[2]

    @model_validator(mode="after")
    def _method_and_arguments(self) -> Self:
        spec = CAPABILITIES.get(self.capability)
        if spec is None or self.capability == RUNNER:
            known = ", ".join(sorted(name for name in CAPABILITIES if name != RUNNER))
            raise at(("read",), f"{self.capability!r} is not a capability an input can read; known: {known}")
        mode = spec.methods.get(self.method)
        if mode is None:
            methods = ", ".join(f"{self.capability}.{m}" for m, mo in spec.methods.items() if mo == "read")
            raise at(("read",), f"{self.capability} has no method {self.method!r}; read methods: {methods}")
        if mode != "read":
            raise at(("read",), f"{self.read} writes; inputs only read, and writes happen in publish operations")
        hints = get_type_hints(getattr(spec.protocol, self.method))
        parameters = [p for p in inspect.signature(getattr(spec.protocol, self.method)).parameters if p != "self"]
        windows = [p for p in parameters if hints.get(p) is Window]
        named = [p for p in parameters if p not in windows]
        if windows and self.window is None:
            raise at(("window",), f"{self.read} lists changes in a time window: set `window`, e.g. 7d")
        if not windows and self.window is not None:
            raise at(("window",), f"{self.read} takes no time window: remove `window`")
        if unknown := sorted(set(self.args) - set(named)):
            raise at(("args", unknown[0]), f"{self.read} has no argument {unknown[0]!r}; it takes {', '.join(named)}")
        if missing := [p for p in named if p not in self.args]:
            raise at(("args",), f"{self.read} needs `args: {{{missing[0]}: ...}}`")
        for name in named:
            try:
                TypeAdapter(hints[name]).validate_python(self.args[name])
            except ValidationError as exc:
                raise at(("args", name), f"{name}: {exc.errors()[0]['msg']}") from exc
        return self

    def arguments(self, until: datetime) -> dict[str, Any]:
        """The method's keyword arguments for a run at `until`: `args`, plus the window ending then."""
        spec = CAPABILITIES[self.capability]
        hints = get_type_hints(getattr(spec.protocol, self.method))
        arguments: dict[str, Any] = dict(self.args)
        for name, hint in hints.items():
            if hint is Window and self.window is not None:
                arguments[name] = Window(since=until - self.window, until=until)
        return arguments


class Output(_Strict):
    """What the agent drafts; in 0.2 the draft is saved locally and nothing is published automatically."""

    deferred_keys: ClassVar[dict[str, str]] = {
        "target": "drafts are saved locally until approvals and `hz inbox` exist (ADR 0020)",
        "facets": "output facets come with fingerprints and memo keys",
    }

    format: Literal["markdown", "json"] = Field(
        default="markdown", description="What the agent writes: `markdown` (the default) or `json`."
    )


class Publish(_Strict):
    """A write the draft is meant for, through a publish operation; recorded, never run automatically in 0.2."""

    deferred_keys: ClassVar[dict[str, str]] = {
        "auto_approve": "approval policies come with `hz inbox` (ADR 0020)",
        "target": "the publish operation's own arguments say where it writes",
    }

    action: str = Field(
        pattern=r"^[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*)+$",
        description="The publish operation (`read_only=False`) that would write it, `<module>.<operation>`, e.g. "
        "`notes.publish`; it declares `write` itself, the contract never does (ADR 0028).",
    )
    output: Ident = Field(description="The output it publishes, a key of `hz.outputs`.")


class Agent(_Strict):
    """Limits of the agent session. Which runner and model run it is configuration, never the contract's (ADR 0029)."""

    deferred_keys: ClassVar[dict[str, str]] = {
        "runner": RUNNER_IS_CONFIGURATION,
        "model": RUNNER_IS_CONFIGURATION,
        "effort": "agent runners take no effort setting yet (ADR 0029)",
        "max_turns": "Claude Code has no turn limit flag yet (ADR 0029)",
    }

    tools: list[str] = Field(
        default_factory=list,
        description="Read-only tools the agent may use, in the runner's naming (e.g. `Read`, `Grep`); empty for the "
        "runner's read-only default. The agent gets no hz tools: inputs are fetched before it starts.",
    )
    max_usd: float | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        description="Most one run may cost in US dollars; your `[workflow] max_usd` caps it whatever it says.",
    )


class Hz(_Strict):
    """Everything Heinzelmännchen reads from a contract, under one `hz:` key (ADR 0021)."""

    deferred_keys: ClassVar[dict[str, str]] = {
        key: why
        for keys, why in (
            (
                ("owner", "maintainers"),
                "owners and maintainers come with the team state repository and people (ADR 0024)",
            ),
            (("tags",), "tags come with `hz workflow list --tag`"),
            (("freshness", "on", "idempotency"), "memo keys and input wakes come with fingerprints (ADR 0020)"),
            (("tools",), "the agent gets no hz tools in this version; inputs are fetched before it starts (ADR 0029)"),
            (("concurrency",), "concurrency comes with leases in the team state repository (ADR 0020)"),
            (("cost",), "set a per-run cap with `agent.max_usd`; daily ceilings come later"),
            (("overrides",), "contracts don't override each other yet; names must be unique"),
        )
        for key in keys
    }

    schema_: Literal[1] = Field(alias="schema", description="Version of the frontmatter format: 1.")
    title: str = Field(min_length=1, max_length=80, description="Display name, shown in lists and the daemon's jobs.")
    scope: Literal["personal"] = Field(
        default="personal",
        description="Who runs it: `personal`, on your own daemon. Team scope comes with the state repository.",
    )
    schedule: str | None = Field(
        default=None,
        description="When the daemon runs it: five cron fields in local time, e.g. `0 7 * * MON`. Without one it runs "
        "only with `hz workflow run`.",
    )
    requires: list[str] = Field(
        default_factory=list,
        description="Capabilities it reads, `<capability>:read`, e.g. `tracker:read`; `agent-runner:read` is implied. "
        "Configuration decides which adapter serves each; a contract never pins one nor writes.",
    )
    inputs: dict[Ident, Input] = Field(
        default_factory=dict, description="Data fetched before the agent starts, by name; each needs its capability."
    )
    outputs: dict[Ident, Output] = Field(
        description="What the agent drafts, by name: exactly one in this version.",
        json_schema_extra={"minProperties": 1, "maxProperties": 1},
    )
    publishes: list[Publish] = Field(
        default_factory=list[Publish],
        description="Where the draft is meant to go, through publish operations; recorded, never run automatically.",
    )
    agent: Agent = Field(default_factory=Agent, description="Limits of the agent session.")
    timeout: Duration = Field(
        default=timedelta(minutes=10),
        description="How long the agent session may take.",
        json_schema_extra={"default": "10m"},
    )
    retries: StrictInt = Field(
        default=0,
        ge=0,
        le=3,
        description="Retries within a daemon tick after a failed run; the cost cap covers all attempts together.",
    )
    catch_up: Literal["latest", "skip"] = Field(
        default="latest", description="Missed schedule slots: `latest` runs once for the newest, `skip` none."
    )

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.schedule is not None and (problem := cron_problem(self.schedule)):
            raise at(("schedule",), problem)
        if not timedelta(0) < self.timeout <= MAX_TIMEOUT:
            raise at(("timeout",), f"timeout must be at most {_format_duration(MAX_TIMEOUT)}")
        granted = self.requirements()
        for name, spec in self.inputs.items():
            if spec.capability not in granted:
                raise at(("inputs", name, "read"), f"{spec.read} needs `{spec.capability}:read` in `hz.requires`")
        if len(self.outputs) != 1:
            raise at(("outputs",), "name exactly one output; several outputs come with output facets")
        for index, publish in enumerate(self.publishes):
            if publish.output not in self.outputs:
                raise at(("publishes", index, "output"), f"no output named {publish.output!r} in `hz.outputs`")
        return self

    def requirements(self) -> dict[str, Requirement]:
        """What it requires, by capability, `agent-runner:read` included; checked when the model is validated."""
        return self._parse_requirements()

    def _parse_requirements(self) -> dict[str, Requirement]:
        found: dict[str, Requirement] = {}
        for index, text in enumerate(self.requires):
            try:
                requirement = Requirement.parse(text)
            except ValueError as exc:
                raise at(("requires", index), str(exc)) from exc
            if requirement.adapter is not None:
                raise at(
                    ("requires", index),
                    f"{text!r} pins the adapter {requirement.adapter!r}: a contract names capabilities; "
                    "configuration decides the adapter ([capabilities] in your user file)",
                )
            if "write" in requirement.modes:
                raise at(
                    ("requires", index),
                    f"{text!r} writes: a contract only reads; writes go through `publishes`, whose operation declares "
                    "write itself",
                )
            if requirement.capability in found:
                raise at(("requires", index), f"{requirement.capability} is required twice")
            found[requirement.capability] = requirement
        found.setdefault(RUNNER, Requirement(RUNNER, frozenset({"read"})))
        return found


class Frontmatter(_Strict):
    """The YAML frontmatter of a contract: OpenProse-compatible identity on top, Heinzelmännchen's keys under `hz`."""

    model_config = ConfigDict(
        title="Heinzelmännchen contract frontmatter (schema 1)", extra="forbid", frozen=True, populate_by_name=True
    )

    name: Slug = Field(description="The contract's name: lowercase words joined by dashes; equals the file stem.")
    kind: Literal["responsibility"] = Field(description="The OpenProse kind: `responsibility`.")
    description: str = Field(min_length=1, max_length=1024, description="What the workflow keeps true, and for whom.")
    id: str | None = Field(
        default=None, pattern=r"^[0-9A-HJKMNP-TV-Z]{26}$", description="OpenProse's minted id (Crockford base32)."
    )
    version: str | None = Field(
        default=None,
        pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?$",
        description="The author's own semantic version.",
    )
    hz: Hz = Field(description="Heinzelmännchen's keys.")


def cron_problem(schedule: str) -> str | None:
    """Why a cron expression can't schedule a job, or None if it can.

    The shape is the kernel's own check, the one `JobLet` applies (five ASCII fields or an alias such as `@daily`), so
    a contract that passes here always compiles; croniter then checks the values.
    """
    from croniter import CroniterBadDateError, croniter  # noqa: PLC0415 - only needed when a contract has a schedule

    if problem := schedule_problem(schedule):
        return f"invalid {problem}; e.g. 0 7 * * MON"
    if not schedule.startswith("@") and not croniter.is_valid(schedule):
        return f"invalid schedule {schedule!r}: a field is out of range, e.g. minute 0-59, hour 0-23, weekday MON-SUN"
    # The daemon's own never-fires check (`system.daemon`'s schedule module), repeated because modules don't import
    # each other's private files; keep the two in step.
    try:
        croniter(schedule, datetime(2000, 1, 1)).get_next(datetime)  # noqa: DTZ001 - wall-clock arithmetic is naive
    except CroniterBadDateError:
        return f"invalid schedule {schedule!r}: it never fires, e.g. 30 February"
    return None


class WorkflowSettings(ModuleSettings):
    """Where contracts are found: the `[workflow]` table or `HZ_WORKFLOW__*` variables.

    A project `hz.toml` can't set any of them: a repository someone else controls must not decide which prompts your
    daemon runs with your agent and your capabilities.
    """

    user_dir: Path = Field(
        default_factory=lambda: user_config_dir() / "workflows",
        description="Your own contracts, `<name>.prose.md`; they run on your daemon when they have a schedule.",
    )
    team_repository: Path | None = Field(
        default=None,
        description="A checkout of your team's state repository; its contracts are in `workflows/`. They run only "
        "with `hz workflow run`, or on your daemon when named in `schedule_team`.",
    )
    schedule_team: list[str] = Field(
        default_factory=list,
        description="Team contracts your daemon runs on their schedule, by name, or `name@sha256:<hex>` to run only "
        "the content you approved (`hz workflow show` prints it); the others run only by hand, so a team's contracts "
        "don't run on every member's daemon.",
    )

    max_usd: float = Field(
        default=1.0,
        gt=0,
        allow_inf_nan=False,
        description="Most one render may cost in US dollars, whatever a contract asks for: scheduled and manual runs, "
        "in every head. A scheduled slot's retries share it.",
    )

    ignored_in_project: ClassVar[frozenset[str]] = frozenset(
        {"user_dir", "team_repository", "schedule_team", "max_usd"}
    )

    @field_validator("schedule_team")
    @classmethod
    def _approvals(cls, entries: list[str]) -> list[str]:
        for entry in entries:
            if not re.fullmatch(APPROVAL, entry):
                msg = f"{entry!r} must be a contract name, or `name@sha256:<64 hex digits>` to pin its content"
                raise ValueError(msg)
        return entries

    def approval(self, name: str) -> tuple[bool, str | None]:
        """Whether `schedule_team` names the team contract `name`, and the SHA-256 it pins, if any."""
        for entry in self.schedule_team:
            entry_name, _, digest = entry.partition("@")
            if entry_name == name:
                return True, digest or None
        return False, None


Severity = Literal["error", "warning"]
Namespace = Literal["me", "team", "path"]


class Diagnostic(BaseModel):
    """A problem in a contract file, with where it is and what to do."""

    file: Path = Field(description="The contract file.")
    line: int = Field(description="Line in the file, from 1.")
    code: str = Field(description="Stable code, `HZC…` (ADR 0021).")
    severity: Severity = Field(description="`error` (the contract isn't compiled) or `warning`.")
    field: str = Field(description="The frontmatter key path or body section concerned.")
    message: str = Field(description="What is wrong.")
    hint: str = Field(default="", description="What to do about it.")

    def __str__(self) -> str:
        """The diagnostic as compilers print it: `file:line  CODE  field  message`."""
        text = f"{self.file}:{self.line}  {self.code}  {self.field}  {self.message}"
        return f"{text}\n  hint: {self.hint}" if self.hint else text


class Directory(BaseModel):
    """A directory contracts are read from."""

    namespace: Namespace = Field(description="`me` (your workflows directory) or `team` (the team repository's).")
    path: Path = Field(description="The directory.")
    exists: bool = Field(description="Whether it exists; `hz workflow new` creates it.")


class Step(BaseModel):
    """Something to do next: a command that works, and why."""

    command: str = Field(description="The command line to run.")
    why: str = Field(description="What it does for you.")


class ContractSummary(BaseModel):
    """One contract in `hz workflow list`."""

    name: str = Field(description="The contract's name.")
    namespace: Namespace = Field(description="Where it was found: `me` (your directory) or `team`.")
    title: str | None = Field(description="Its display name; none if it doesn't parse.")
    file: Path = Field(description="The contract file.")
    schedule: str | None = Field(description="Its cron expression; none if it runs only by hand.")
    scheduled: bool = Field(description="Whether your daemon runs it on its schedule.")
    valid: bool = Field(description="Whether it has no errors, so it is compiled.")
    errors: int = Field(description="How many errors it has.")
    warnings: int = Field(description="How many warnings it has.")


class ContractList(BaseModel):
    """`hz workflow list`: the directories searched and every contract found in them."""

    directories: list[Directory] = Field(description="Where contracts are read from, team first.")
    contracts: list[ContractSummary] = Field(description="Every contract found, team ones first.")
    next: list[Step] = Field(description="What to do next, as commands.")


class NewContract(BaseModel):
    """`hz workflow new`: the starter contract written, and what to do with it."""

    file: Path = Field(description="The contract file written.")
    namespace: Namespace = Field(description="Where it was written: `me` or `team`.")
    name: str = Field(description="The contract's name.")
    next: list[Step] = Field(description="What to do next, as commands.")


class Draft(BaseModel):
    """A draft a render saved locally."""

    contract: str = Field(description="The qualified contract, `<namespace>:<name>`.")
    created: datetime = Field(description="When the render fetched its inputs (from the file name).")
    draft: Path = Field(description="The draft file.")
    record: Path | None = Field(description="Its record: packet, input hashes and the agent's report; none if lost.")
    bytes: int = Field(description="The draft's size.")


class DraftList(BaseModel):
    """`hz workflow drafts`: the drafts saved so far, newest first."""

    directory: Path = Field(description="Where drafts are saved, one directory per contract.")
    drafts: list[Draft] = Field(description="The drafts, newest first.")
    latest: str | None = Field(default=None, description="The newest draft's text, when asked for.")
    next: list[Step] = Field(description="What to do next, as commands.")


class RequirementStatus(BaseModel):
    """A capability a contract requires, and whether configuration serves it."""

    requirement: str = Field(description="As required, e.g. `tracker:read`.")
    adapter: str | None = Field(description="The bound adapter that serves it; none if none can.")
    problem: str | None = Field(description="Why it is unmet; none when met.")
    fix: str | None = Field(default=None, description="What to do when unmet.")


class PublishStatus(BaseModel):
    """A publish operation a contract names, and whether an installed module provides it."""

    action: str = Field(description="The operation, `<module>.<operation>`.")
    output: str = Field(description="The output it would publish.")
    provided: bool = Field(description="Whether an enabled module provides it as a publish operation.")
    note: str = Field(description="What happens: drafts await `hz inbox`, which isn't built yet; nothing is published.")


class CompiledContract(BaseModel):
    """`hz workflow show`: a contract as the daemon runs it."""

    name: str = Field(description="The contract's name.")
    namespace: Namespace = Field(description="Where it was found: `me` or `team`.")
    file: Path = Field(description="The contract file.")
    title: str | None = Field(description="Its display name.")
    description: str | None = Field(description="What it keeps true, and for whom.")
    job: str | None = Field(description="Its daemon job, `workflow:<namespace>-<name>`; none when not scheduled.")
    schedule: str | None = Field(description="Its cron expression in local time.")
    scheduled: bool = Field(description="Whether your daemon runs it on its schedule.")
    why_not_scheduled: str | None = Field(description="Why your daemon doesn't run it, when it doesn't.")
    requirements: list[RequirementStatus] = Field(description="Capabilities it reads, with the adapters bound.")
    unmet: list[str] = Field(description="Requirements no bound adapter serves; a run fails until they are met.")
    inputs: dict[str, str] = Field(description="Its inputs, by name: the read method it calls.")
    outputs: dict[str, str] = Field(description="Its outputs, by name: their format.")
    publishes: list[PublishStatus] = Field(description="Where its draft is meant to go; never published by itself.")
    tools: list[str] = Field(description="Read-only tools the agent may use; empty: the runner's default.")
    timeout_seconds: float | None = Field(description="How long the agent session may take.")
    max_usd: float | None = Field(description="Most a manual run may cost: the lower of your cap and the contract's.")
    diagnostics: list[Diagnostic] = Field(description="Its errors and warnings.")
    sha256: str = Field(description="SHA-256 of the file; `[workflow] schedule_team` can pin it.")
    approve: str | None = Field(
        description="For team contracts: the `schedule_team` entry that runs exactly this content on your daemon."
    )
    drafts: Path = Field(description="Where its drafts are saved.")
    draft_count: int = Field(description="How many drafts it has so far.")
    ready: bool = Field(description="Whether a run can start: no errors and every requirement served.")
    measurements: Measurements | None = Field(
        default=None,
        description="Its success test: how long drafting took, and how much was changed before sending "
        "(`hz workflow sent`); none without drafts.",
    )
    next: list[Step] = Field(description="What to do next, as commands: fixes while not ready, then how to run it.")


class CheckReport(BaseModel):
    """`hz workflow check`: every contract checked and every problem found."""

    files: list[Path] = Field(description="The contract files checked.")
    diagnostics: list[Diagnostic] = Field(description="Every error and warning, by file and line.")
    searched: list[Directory] = Field(
        default_factory=list[Directory], description="The directories searched, when no path was given."
    )

    @property
    def errors(self) -> int:
        """How many errors were found."""
        return sum(d.severity == "error" for d in self.diagnostics)


class FetchedInput(BaseModel):
    """One input as the agent receives it."""

    read: str = Field(description="The read method that fetched it.")
    arguments: dict[str, JsonValue] = Field(description="The arguments it was called with.")
    data: JsonValue = Field(description="What it returned, as JSON.")
    sha256: str = Field(description="SHA-256 of the data's canonical JSON.")


class Packet(BaseModel):
    """What an agent session gets: the contract as its instructions, the inputs as data, and its limits."""

    contract: str = Field(description="The qualified contract, `<namespace>:<name>`.")
    as_of: datetime = Field(description="When the inputs were fetched; time windows end here.")
    instructions: str = Field(description="The contract's body, the agent's system prompt.")
    prompt: str = Field(description="The task and the inputs, framed as untrusted data.")
    inputs: dict[str, FetchedInput] = Field(description="The inputs, by name.")
    output: str = Field(description="The output to draft.")
    format: Literal["markdown", "json"] = Field(description="Its format.")
    tools: list[str] = Field(description="Read-only tools the agent may use; empty: the runner's default.")
    timeout_seconds: float = Field(description="How long the session may take.")
    max_usd: float | None = Field(description="Most it may cost.")


class Rendered(BaseModel):
    """A finished render: where the draft is, what produced it and what it cost."""

    contract: str = Field(description="The qualified contract, `<namespace>:<name>`.")
    as_of: datetime = Field(description="When the inputs were fetched.")
    draft: Path = Field(description="The draft, saved locally.")
    record: Path = Field(description="The JSON record next to it: packet, input hashes and the agent's report.")
    sha256: str = Field(description="SHA-256 of the draft.")
    inputs: dict[str, str] = Field(description="SHA-256 of each input's data, by name.")
    agent: AgentResult = Field(description="What the agent session reported.")
    seconds: float | None = Field(
        default=None, description="Wall-clock seconds from fetching the inputs to saving the draft: drafting time."
    )
    awaiting: list[str] = Field(
        description="Publish operations the draft is meant for; it waits for `hz inbox`, nothing was published."
    )


class RunResult(BaseModel):
    """`hz workflow run`: the packet of a dry run, or the finished render."""

    dry_run: bool = Field(description="Whether only the packet was built and no agent ran.")
    packet: Packet | None = Field(default=None, description="The packet, in a dry run.")
    rendered: Rendered | None = Field(default=None, description="The render, when the agent ran.")
    note: str = Field(default="", description="What to know about the result, e.g. that a manual run has no receipt.")
