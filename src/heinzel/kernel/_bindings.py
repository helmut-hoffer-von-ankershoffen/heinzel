"""Binding capabilities to adapters, and handing requesters handles limited to what they declared (ADR 0028).

Configuration binds each capability to an adapter by name, in the `[capabilities]` table of the site, user or
`HZ_CONFIG` file or `HZ_CAPABILITIES__<CAPABILITY>` variables; a project `hz.toml` can't, because a repository someone
else controls must not decide where data goes. A requester (an operation, a job, or a module for its services, info
and health providers) gets a capability only if it declared it, and only in the modes it declared and the bound
adapter serves; `write` only in publish operations (`read_only=False`) that declared it themselves.

Not an isolation boundary: every module runs in the same process with the user's rights, and Python code can reach
an adapter's instance through introspection or import the adapter's module. Handles make intent explicit and misuse
visible in review, as `Credentials` does (ADR 0030); they don't stop a malicious plugin.
"""

import contextvars
import inspect
import itertools
import logging
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from functools import partial
from typing import Any, ClassVar, Literal, cast

from pydantic import BaseModel, Field, field_validator, model_validator

from heinzel.kernel._capabilities import (
    CAPABILITIES,
    MODES,
    NO_ADAPTER,
    Capability,
    Mode,
    Requirement,
    declared_unsupported,
)
from heinzel.kernel._config import ModuleSettings
from heinzel.kernel._errors import HeinzelError, NotSupportedError
from heinzel.kernel._lets import ProviderLet, host_of, settings_parameters
from heinzel.kernel._logging import log_event
from heinzel.kernel._protocols import Gathered, SourceFailure
from heinzel.kernel._text import clean_text

logger = logging.getLogger(__name__)

CAPABILITIES_SECTION = "capabilities"
_ISSUED = 4096
"""How many ids given out by adapters are remembered, to route a later call naming one without a link."""
_SHOWN = 80
"""Characters of a reference repeated in a routing error."""
_REASON = 300
"""Characters of an adapter's error repeated as the reason it failed in a read asked of several."""
_GAP = object()

RequesterKind = Literal["module", "operation", "job"]
How = Literal["configured", "automatic", "default", "off", "unbound"]
"""How a capability got its adapter (ADR 0055): a configured binding; the only installed adapter, chosen
automatically while nothing is configured; the core's default; `off`, configured to `none`; or none at all."""
HOWS: tuple[How, ...] = ("configured", "automatic", "default", "off", "unbound")


def _options(capability: str, *, multiple: bool = False) -> dict[str, Any]:
    """The JSON Schema extra of a binding: its values are the installed adapters of `capability`, or `none`.

    The web head resolves the source into a list to choose from, so no binding is ever typed (ADR 0055); a capability
    several adapters may serve at once (ADR 0161) is a list of switches instead.
    """
    options: dict[str, Any] = {"source": "adapters", "capability": capability, "none": NO_ADAPTER}
    if multiple:
        options["multiple"] = True
    return {"x-options": options}


def _names(value: object) -> object:
    """A binding to several adapters as a list of names: from a list, or a comma-separated string (`a,b`).

    Raises:
        ValueError: If `none` is listed with adapters.
    """
    if isinstance(value, str) and "," in value:
        value = value.split(",")
    if isinstance(value, (list, tuple)):
        names = [str(item).strip() for item in cast("Iterable[object]", value)]
        names = list(dict.fromkeys(name for name in names if name))
        if NO_ADAPTER in names and len(names) > 1:
            msg = f"{NO_ADAPTER!r} binds nothing; it can't be listed with adapters ({', '.join(names)})"
            raise ValueError(msg)
        return names
    return value


class CapabilitySettings(ModuleSettings):
    """Which adapter serves each capability: the `[capabilities]` table or `HZ_CAPABILITIES__<CAPABILITY>` variables.

    Keys are capability names with `_` or `-` (`code_host` or `code-host`); values are adapter names, as
    `hz system capability list` shows them, or `none` for no adapter at all. While a capability has no binding, the
    only installed adapter that provides it serves it automatically, else the core's default if it has one, else
    nobody (ADR 0055). A project `hz.toml` can't bind: only the site, user and `HZ_CONFIG` files and the environment
    can.
    """

    section_title: ClassVar[str | None] = "Which connector serves what"  # the heading in Settings

    agent_runner: str | None = Field(
        default="claude-code",
        title="Agent runner",
        description=(
            "What runs drafts and chats: Claude Code (the default), your own command, or a runner a plugin brings."
        ),
        json_schema_extra=_options("agent-runner"),
    )
    ai_access: str | None = Field(
        default="claude-code",
        title="AI access",
        description="How Heinzelmännchen reaches AI: Claude Code (the default), your company's AI "
        "gateway, Pydantic AI, or a way a plugin brings.",
        json_schema_extra=_options("ai-access"),
    )
    calendar: str | None = Field(
        default=None,
        title="Calendar",
        description="The connector that reads your calendar, such as Google.",
        json_schema_extra=_options("calendar"),
    )
    chat: str | None = Field(
        default=None,
        title="Chat",
        description="The connector for team chat, such as Slack.",
        json_schema_extra=_options("chat"),
    )
    code_host: str | None = Field(
        default=None,
        title="Code host",
        description="The connector for pull requests and code, such as GitHub.",
        json_schema_extra=_options("code-host"),
    )
    directory: str | None = Field(
        default="one-on-ones",
        title="Directory",
        description=(
            "Who reports to whom: guessed from recurring one-on-ones in your calendar (the default, each "
            "guess for you to confirm), or read from a directory a plugin connects."
        ),
        json_schema_extra=_options("directory"),
    )
    documents: str | list[str] | None = Field(
        default=None,
        title="Documents",
        description=(
            "The connectors for documents and pages, such as Google Docs and Confluence. Switch on several: a link "
            "opens in the one it belongs to, a search asks them all, and the first is where new documents go."
        ),
        json_schema_extra=_options("documents", multiple=True),
    )
    mail: str | None = Field(
        default=None,
        title="Mail",
        description="The connector that reads your mail, such as Google.",
        json_schema_extra=_options("mail"),
    )
    meetings: str | None = Field(
        default=None,
        title="Meetings",
        description="The connector for meeting summaries, such as Zoom.",
        json_schema_extra=_options("meetings"),
    )
    tracker: str | None = Field(
        default=None,
        title="Tracker",
        description="The connector for issues, such as GitHub or Jira.",
        json_schema_extra=_options("tracker"),
    )
    team_graph: str | None = Field(
        default="hz-graph",
        title="Team graph",
        description=(
            "What draws your team's graph: the built-in graph of your team's contracts and telemetry (the "
            "default), or one a plugin brings."
        ),
        json_schema_extra=_options("team-graph"),
    )

    ignored_in_project: ClassVar[frozenset[str]] = frozenset(
        {
            "agent_runner",
            "ai_access",
            "calendar",
            "chat",
            "code_host",
            "directory",
            "documents",
            "mail",
            "meetings",
            "team_graph",
            "tracker",
        }
    )

    @model_validator(mode="before")
    @classmethod
    def _dashes(cls, data: Any) -> Any:  # noqa: ANN401 - raw settings input
        if isinstance(data, Mapping):
            return {str(key).replace("-", "_"): value for key, value in cast("Mapping[str, Any]", data).items()}
        return data  # pragma: no cover - pydantic-settings always passes a mapping

    @field_validator("documents", mode="before")
    @classmethod
    def _several(cls, value: object) -> object:
        return _names(value)

    def bound(self, capability: str) -> str | None:
        """The adapter configured or defaulted for `capability`, or None; `none` and an empty value are None.

        Of several adapters (ADR 0161), the first: the default, where a call that names no item goes. Doesn't choose
        automatically: that needs the installed adapters, which `Capabilities.binding` knows.
        """
        names = self.bound_all(capability)
        return names[0] if names else None

    def bound_all(self, capability: str) -> tuple[str, ...]:
        """Every adapter configured or defaulted for `capability`, the default first; empty for none (ADR 0161)."""
        value = cast("str | list[str] | None", getattr(self, capability.replace("-", "_"), None))
        values = value if isinstance(value, list) else [value]
        return tuple(name for name in values if name is not None and name not in {"", NO_ADAPTER})

    def configured(self, capability: str) -> bool:
        """Whether a configuration file or variable sets `capability`, to an adapter or to `none`."""
        return capability.replace("-", "_") in self.model_fields_set

    def defaulted(self, capability: str) -> bool:
        """Whether `capability` would be bound by the core's default, because no configuration or variable sets it."""
        field = capability.replace("-", "_")
        default = type(self).model_fields[field].default if field in type(self).model_fields else None
        return default is not None and not self.configured(capability)


@dataclass(frozen=True, slots=True)
class Requester:
    """Who asks for capabilities: an operation, a job, or a module for its services, info and health providers."""

    module: str
    """Qualified name of the module."""
    kind: RequesterKind
    name: str
    """The operation or job name; the module's own name for module-level requirements."""
    requires: tuple[Requirement, ...]
    """What it declared itself."""
    inherited: tuple[Requirement, ...] = ()
    """What its module declared, which it may use too (read modes only)."""
    publishes: bool = False
    """Whether it is a publish operation (`read_only=False`), the only kind that gets `write`."""
    optional: bool = False
    """Whether it does without its own requirements when nothing serves them (an optional operation): an unmet one
    is no doctor finding, and its injected handle refuses only when called."""
    serves: tuple[tuple[str, str], ...] = ()
    """For a module that only provides adapters, the (capability, adapter) pairs it provides: its requirements are
    needed only while one of them serves someone."""

    health_on_demand: bool = False
    """Health-only metadata; never relaxes the requester's execution authority."""

    inspects: tuple[Requirement, ...] = ()
    """Separate inventory authority, consulted only by adapter enumeration, never bound handle injection."""

    @property
    def label(self) -> str:
        """How messages and the matrix name it: `digest` for the module, `digest weekly` for an operation or job."""
        return self.module if self.kind == "module" else f"{self.module} {self.name}"

    def grant(self, capability: str) -> tuple[Requirement | None, frozenset[Mode]]:
        """The requirement that applies to `capability` and the modes this requester gets from it."""
        found = [r for r in (*self.requires, *self.inherited) if r.capability == capability]
        if not found:
            return None, frozenset()
        declared = frozenset[Mode]().union(*(r.modes for r in found))
        modes = declared if self.publishes else declared - {"write"}
        pin = next((r.adapter for r in found if r.adapter), None)
        return Requirement(capability, declared, pin), modes


@dataclass(frozen=True, slots=True)
class Adapter:
    """A registered adapter: the module advertising it, its distribution and its declaration."""

    module: str
    distribution: str
    let: ProviderLet


@dataclass(frozen=True, slots=True)
class CapabilityUse:
    """One row of the matrix: who needs which capability in which modes, through which adapter and hosts."""

    requester: str
    """Who declared it: a module, or `<module> <operation or job>`."""
    kind: RequesterKind
    requirement: str
    """As declared, e.g. `documents:read` or `github:tracker:read`."""
    capability: str
    modes: tuple[Mode, ...]
    adapter: str | None
    """The adapter that serves it: the pinned or bound one; None if none can."""
    hosts: tuple[str, ...]
    """Hosts that adapter reaches, declared or read from its settings; empty if unknown, or none serves."""
    problem: str | None
    """Why the requirement is unmet; None when it is met."""
    optional: bool = False
    """Whether the requester does without it when it is unmet, so that is no doctor finding."""
    health_on_demand: bool = False
    """Whether this own unpinned requirement permits health-only explicit deferral."""


class Adapters[P](Mapping[str, P]):
    """Handles on every installed adapter of a capability, by adapter name; ask for `Adapters[AiAccess]` (ADR 0057).

    For the module that owns choosing among a capability's adapters, which lists them all with their status, not just
    the bound one. Injected only into a function whose operation or module declared the capability; read modes only.
    """

    __slots__ = ("_handles",)

    def __init__(self, handles: Mapping[str, object]) -> None:
        """Hold the handles, by adapter name."""
        self._handles = dict(handles)

    def __getitem__(self, name: str) -> P:
        """The handle on the adapter called `name`.

        Raises:
            KeyError: If no installed adapter of the capability has that name.
        """
        return cast("P", self._handles[name])

    def __iter__(self) -> Iterator[str]:
        """The adapter names, sorted."""
        return iter(self._handles)

    def __len__(self) -> int:
        """How many adapters there are."""
        return len(self._handles)

    def __repr__(self) -> str:
        return f"<Adapters {', '.join(self._handles)}>"


class _Handle:
    """A requester's view of an adapter: wrappers around the capability's methods in the granted modes, nothing else.

    It holds no reference to the adapter as an attribute, and its methods are plain functions, not bound methods.
    Their closures still reach the adapter, and for a reader's handle the kernel's `Capabilities`, which builds it on
    the first call: introspection reaches both, which is why this is no isolation boundary (module docstring).
    """

    __slots__ = ("_allowed", "_denied", "_label")

    def __init__(self, allowed: dict[str, Callable[..., Any]], denied: dict[str, str], label: str) -> None:
        self._allowed = allowed
        self._denied = denied
        self._label = label

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401 - a protocol method
        if name in self._allowed:
            return self._allowed[name]
        if name in self._denied:
            raise HeinzelError(self._denied[name])
        msg = f"{self._label} has no method {name!r}"
        raise AttributeError(msg)

    def __repr__(self) -> str:
        return f"<{self._label}>"


def _labelled(value: object, source: str) -> object:
    """`value` with `source` set, if it is a model with a `source` field such as `Document`; else `value` itself."""
    if isinstance(value, BaseModel) and "source" in type(value).model_fields:
        return value.model_copy(update={"source": source})
    return value


def _gather(calls: Mapping[str, Callable[[], object]], timeout: float) -> list[tuple[str, object | BaseException]]:
    """Run `calls` at once, each in a daemon thread with the caller's context; what each returned or raised, in order.

    One that hasn't finished after `timeout` seconds yields a `TimeoutError`; its thread is left behind, as a daemon,
    so a hanging adapter neither holds the caller nor keeps the process alive.
    """
    outcomes: dict[str, object | BaseException] = {}
    done = threading.Condition()

    def run(name: str, call: Callable[[], object]) -> None:
        try:
            outcome: object | BaseException = call()
        except BaseException as exc:  # noqa: BLE001 - reported per adapter, never raised into the caller
            outcome = exc
        with done:
            outcomes[name] = outcome
            done.notify_all()

    for name, call in calls.items():
        context = contextvars.copy_context()
        threading.Thread(target=context.run, args=(run, name, call), name=f"fan-out-{name}", daemon=True).start()
    deadline = time.monotonic() + timeout
    with done:
        while len(outcomes) < len(calls) and (left := deadline - time.monotonic()) > 0:
            done.wait(left)
        return [(name, outcomes.get(name, TimeoutError())) for name in calls]


def _reason(name: str, outcome: BaseException, method: str, timeout: float) -> str:
    """Why an adapter gave nothing, for people: its own message only for a `HeinzelError`, which is written for them."""
    if isinstance(outcome, NotSupportedError):
        return f"can't {method.replace('_', ' ')}"
    if isinstance(outcome, TimeoutError):
        return f"didn't answer within {timeout:g} s"
    if isinstance(outcome, HeinzelError):
        return clean_text(str(outcome))[:_REASON]
    # Never the message of an unexpected error: it may repeat a URL with a token in it, as `hosts_of` says.
    log_event(logger, "kernel.fan_out.failed", level=logging.WARNING, adapter=name, error=type(outcome).__name__)
    return f"failed ({type(outcome).__name__})"


def _interleaved(results: list[tuple[str, list[object]]]) -> list[object]:
    """The adapters' results taken in turns, first of each, then second of each, in binding order: stable and fair."""
    merged: list[object] = []
    for row in itertools.zip_longest(*(items for _, items in results), fillvalue=_GAP):
        merged.extend(item for item in row if item is not _GAP)
    return merged


class _RoutedHandle:
    """A requester's view of several adapters of one capability (ADR 0161).

    A method on one item goes to the adapter it belongs to (`Capabilities.pick`); a list read is asked of all of them
    at once and merged (`Gathered`); any other method goes to the default, the first bound. Each adapter is reached
    through its own `_Handle`, so modes and refusals are those of one adapter.
    """

    __slots__ = ("_capabilities", "_capability", "_handles", "_modes", "_refusals")

    def __init__(
        self,
        capabilities: "Capabilities",
        capability: Capability,
        handles: Mapping[str, tuple[Adapter, "_Handle"]],
        refusals: "_Handle",
        modes: frozenset[Mode],
    ) -> None:
        self._capabilities = capabilities
        self._capability = capability
        self._handles = dict(handles)
        self._refusals = refusals
        self._modes = modes

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401 - a protocol method
        mode = self._capability.methods.get(name)
        if mode is None or mode not in self._modes:
            return getattr(self._refusals, name)
        if name in self._capability.fan_out:
            return partial(self._fan_out, name)
        if name in self._capability.routes:
            return partial(self._routed, name)
        return getattr(next(iter(self._handles.values()))[1], name)

    def __repr__(self) -> str:
        return f"<{self._capability.name} through {', '.join(self._handles)}>"

    def _routed(self, method: str, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401 - forwards the protocol method
        capability = self._capability
        parameter = capability.routes[method]
        bound = inspect.signature(getattr(capability.protocol, method)).bind(None, *args, **kwargs)
        ref = bound.arguments.get(parameter)
        if not isinstance(ref, str):
            msg = f"{capability.name}.{method} needs {parameter} as text, to know which connector holds it"
            raise HeinzelError(msg)
        name, local = self._capabilities.pick(capability.name, ref, self._handles)
        adapter, handle = self._handles[name]
        mode = capability.methods[method]
        if mode not in adapter.let.modes:
            msg = f"{name} serves {capability.name} only to {', '.join(sorted(adapter.let.modes))}; it can't {method}."
            raise HeinzelError(msg)
        bound.arguments[parameter] = local
        result = getattr(handle, method)(*bound.args[1:], **bound.kwargs)
        self._capabilities.remember(capability.name, (local, *_refs(result)), name)
        return _labelled(result, name)

    def _fan_out(self, method: str, *args: object, **kwargs: object) -> Gathered[object]:
        capability = self._capability
        timeout = self._capabilities.fan_out_timeout
        calls = {name: partial(getattr(handle, method), *args, **kwargs) for name, (_, handle) in self._handles.items()}
        results: list[tuple[str, list[object]]] = []
        failures: list[SourceFailure] = []
        for name, outcome in _gather(calls, timeout):
            if isinstance(outcome, BaseException):
                failures.append(SourceFailure(source=name, reason=_reason(name, outcome, method, timeout)))
                continue
            items = list(cast("Iterable[object]", outcome))
            self._capabilities.remember(capability.name, (ref for item in items for ref in _refs(item)), name)
            results.append((name, items))
        if not results:
            reasons = "; ".join(f"{failure.source}: {failure.reason}" for failure in failures)
            msg = f"No {capability.name} connector could {method.replace('_', ' ')}: {reasons}."
            raise HeinzelError(msg)
        merged = _interleaved(results)
        bound = inspect.signature(getattr(capability.protocol, method)).bind(None, *args, **kwargs)
        limit = bound.arguments.get("limit")
        if isinstance(limit, int) and not isinstance(limit, bool) and limit >= 0:
            merged = merged[:limit]
        return Gathered(merged, failures)


def _refs(value: object) -> tuple[str, ...]:
    """The references a result carries, its `ref` and `url`, to route a later call naming one of them."""
    return tuple(ref for field in ("ref", "url") if isinstance(ref := getattr(value, field, None), str))


def _wrap(
    instance: Callable[[], object], capability: Capability, name: str, source: str | None = None
) -> Callable[..., Any]:
    def call(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401 - forwards the protocol method
        adapter = instance()
        method = getattr(adapter, name, None)
        if name in declared_unsupported(adapter, capability) or not callable(method):
            # Declared unsupported (ADR 0159), or an optional method it leaves out (ADR 0161).
            raise NotSupportedError(capability.name, name)
        result = method(*args, **kwargs)
        if source is not None and name in capability.fan_out:
            return Gathered(_labelled(item, source) for item in cast("Iterable[object]", result))
        return result

    # Not functools.wraps: it would set __wrapped__ to the bound method and with it the adapter.
    call.__name__ = name
    call.__qualname__ = f"{capability.protocol.__name__}.{name}"
    call.__doc__ = getattr(capability.protocol, name).__doc__
    return call


def _idle(rows: list[tuple[Requester, CapabilityUse]]) -> set[Requester]:
    """Requesters of modules that only provide adapters, none of which serves a requester that is needed itself."""
    idle = {requester for requester, _ in rows if requester.serves}
    while True:
        used = {(use.capability, use.adapter) for requester, use in rows if requester not in idle}
        still = {requester for requester in idle if not used.intersection(requester.serves)}
        if still == idle:
            return idle
        idle = still


class Capabilities:
    """The adapters, the requirements and the bindings; resolves a capability for a requester (ADR 0028).

    Kernel-internal: modules get a `CapabilityView`, which reports but hands out nothing.
    """

    fan_out_timeout: float = 20.0
    """Seconds each adapter has to answer a read asked of several at once; one that takes longer is reported."""

    def __init__(  # noqa: PLR0913, PLR0917 - everything the kernel registered
        self,
        adapters: Iterable[Adapter],
        requesters: Iterable[Requester],
        owners: Mapping[Callable[..., Any], Requester],
        settings: CapabilitySettings,
        build: Callable[[Callable[..., Any]], Any],
        unknown_keys: Iterable[str] = (),
        compiled: Callable[[], Iterable[Requester]] = list,
        settings_of: Callable[[type[ModuleSettings]], ModuleSettings] | None = None,
    ) -> None:
        """Hold what the kernel registered.

        Args:
            adapters: Every registered adapter; two with one adapter name for one capability are ambiguous.
            requesters: Every module, operation and job, in registration order.
            owners: Every function that may ask for a capability, with the requester it acts for.
            settings: The bindings.
            build: Calls an adapter factory with injection, e.g. `Kernel.call`.
            unknown_keys: Keys of the `[capabilities]` table that name no capability, e.g. typos.
            compiled: Requesters of jobs compiled from data (ADR 0021), asked afresh for every matrix.
            settings_of: The effective settings of a class, e.g. `Kernel.resolve`, for hosts that settings decide;
                such hosts are unknown without it.
        """
        self.adapters: dict[tuple[str, str], list[Adapter]] = {}
        for adapter in adapters:
            self.adapters.setdefault((adapter.let.capability, adapter.let.adapter), []).append(adapter)
        self.requesters = [r for r in requesters if r.requires]
        self._owners = dict(owners)
        self.settings = settings
        self.unknown_keys = tuple(unknown_keys)
        self._compiled = compiled
        self._build = build
        self._settings_of = settings_of
        self._instances: dict[tuple[str, str], object] = {}
        # By adapter, not by name: two adapters may share a name (an ambiguous binding) and differ in hosts.
        self._hosts: dict[Adapter, tuple[tuple[str, ...], str | None]] = {}
        self._links: dict[tuple[Adapter, str], tuple[str, ...]] = {}
        # Which adapter gave out an id, newest last, so an id without a link goes back to it (ADR 0161).
        self._issued: dict[tuple[str, str], str] = {}

    def adapter_for(self, requirement: Requirement) -> tuple[Adapter | None, str | None]:
        """The adapter that serves `requirement` (pinned or bound), and why it can't, if it can't."""
        name = requirement.adapter or self.bound(requirement.capability)
        if name is None:
            return None, "no adapter bound"
        candidates = self.adapters.get((requirement.capability, name), [])
        if not candidates:
            return None, f"no installed module provides {requirement.capability} as {name!r}"
        if len(candidates) > 1:
            providers = " and ".join(f"{a.module} ({a.distribution})" for a in candidates)
            return None, f"the adapter name {name!r} is ambiguous: {providers} both provide {requirement.capability}"
        adapter = candidates[0]
        if missing := requirement.modes - adapter.let.modes:
            serves = ",".join(m for m in MODES if m in adapter.let.modes)
            return adapter, f"{name} serves only {serves}, not {','.join(m for m in MODES if m in missing)}"
        return adapter, None

    def matrix(self) -> list[CapabilityUse]:
        """Who needs what, in which modes, through which adapter and hosts, and what is unmet.

        Leaves out the requirements of a module that only provides adapters while none of them serves anyone: such
        a module reads nothing then, so its needs are neither a use nor a doctor finding.
        """
        rows: list[tuple[Requester, CapabilityUse]] = []
        for requester in (*self.requesters, *(r for r in self._compiled() if r.requires)):
            for requirement in requester.requires:
                adapter, problem = self.adapter_for(requirement)
                use = CapabilityUse(
                    requester=requester.label,
                    kind=requester.kind,
                    requirement=str(requirement),
                    capability=requirement.capability,
                    modes=tuple(m for m in MODES if m in requirement.modes),
                    adapter=adapter.let.adapter if adapter else None,
                    hosts=self.hosts_of(adapter)[0] if adapter else (),
                    problem=problem,
                    optional=requester.optional,
                    health_on_demand=(
                        requester.health_on_demand and requirement in requester.requires and requirement.adapter is None
                    ),
                )
                rows.append((requester, use))
        idle = _idle(rows)
        return [use for requester, use in rows if requester not in idle]

    def hosts_of(self, adapter: Adapter) -> tuple[tuple[str, ...], str | None]:
        """The host names `adapter` reaches, and why they are unknown if its settings decide them and it failed.

        Never raises and never repeats a setting's value: a URL may carry a token, and so may an exception's message.
        """
        let = adapter.let
        if let.configured_hosts is None:
            return let.hosts, None
        if adapter not in self._hosts:
            self._hosts[adapter] = self._configured_hosts(let)
        return self._hosts[adapter]

    def _configured_hosts(
        self, let: ProviderLet, source: Callable[..., Iterable[str]] | None = None, what: str = "hosts"
    ) -> tuple[tuple[str, ...], str | None]:
        function = cast("Callable[..., Iterable[str]]", source or let.configured_hosts)
        if self._settings_of is None:
            return (), f"the {what} of {let.name} come from its settings, which nothing provides"
        try:
            settings = {name: self._settings_of(model) for name, model in settings_parameters(function).items()}
            result: object = function(**settings)
            # A plugin may return anything; a lone string is one host, not a sequence of letters.
            values = [result] if isinstance(result, str) else list(cast("Iterable[object]", result))
        except Exception as exc:  # noqa: BLE001 - isolate plugins: their settings or hosts never break info or doctor
            # No traceback and no message: pydantic's and a plugin's messages may repeat a URL with a token in it,
            # and warnings reach the terminal.
            name = getattr(function, "__qualname__", repr(function))
            log_event(
                logger,
                "kernel.hosts.read_failed",
                level=logging.WARNING,
                adapter=let.name,
                source=name,
                error=type(exc).__name__,
            )
            return (), f"the {what} of {let.name} come from its settings, which couldn't be read ({type(exc).__name__})"
        hosts = [host_of(value) if isinstance(value, str) else None for value in values]
        if invalid := [str(number) for number, host in enumerate(hosts, 1) if host is None]:
            return (), (
                f"the {what} of {let.name} come from its settings, but value {', '.join(invalid)} of {len(hosts)} is "
                f"not a host name or URL"
            )
        if not hosts:
            return (), f"the {what} of {let.name} come from its settings, which name no host"
        return tuple(dict.fromkeys(cast("list[str]", hosts))), None

    def unknown_bindings(self) -> list[tuple[str, str]]:
        """Bindings that name an adapter no installed module provides, as (capability, adapter)."""
        return [
            (name, bound)
            for name in CAPABILITIES
            for bound in self.settings.bound_all(name)
            if (name, bound) not in self.adapters
        ]

    def links_of(self, adapter: Adapter) -> tuple[str, ...]:
        """The hosts of the links `adapter` opens (ADR 0161): its declared links, else the hosts it reaches."""
        let = adapter.let
        if let.links:
            return let.links
        if let.configured_links is not None:
            key = (adapter, "links")
            if key not in self._links:
                self._links[key] = self._configured_hosts(let, let.configured_links, "links")[0]
            return self._links[key]
        return self.hosts_of(adapter)[0]

    def bindings(self, capability: str) -> tuple[tuple[str, ...], How]:
        """Every adapter that serves `capability`, the default first, and how they were chosen (ADR 0161).

        Several only for a capability that allows them (`Capability.multiple`) and a configured list; otherwise the
        one of `binding`, or none.
        """
        spec = CAPABILITIES.get(capability)
        if spec is not None and spec.multiple and self.settings.configured(capability):
            names = self.settings.bound_all(capability)
            if len(names) > 1:
                return names, "configured"
        bound, how = self.binding(capability)
        return ((bound,) if bound is not None else ()), how

    def _serving(self, capability: str, modes: frozenset[Mode]) -> list[Adapter]:
        """The bound adapters of `capability` that are installed once and serve some of `modes`, in binding order."""
        serving: list[Adapter] = []
        for name in self.bindings(capability)[0]:
            candidates = self.adapters.get((capability, name), [])
            if len(candidates) == 1 and modes & candidates[0].let.modes:
                serving.append(candidates[0])
        return serving

    def remember(self, capability: str, refs: Iterable[str | None], source: str) -> None:
        """Note that `source` gave out `refs`, so a later call naming one without a link goes back to it."""
        for ref in refs:
            if ref:
                self._issued.pop((capability, ref), None)
                self._issued[capability, ref] = source
        while len(self._issued) > _ISSUED:
            del self._issued[next(iter(self._issued))]

    def pick(self, capability: str, ref: str, names: Iterable[str]) -> tuple[str, str]:
        """The adapter of `names` that `ref` belongs to, and `ref` as that adapter takes it (ADR 0161).

        `<adapter>:<id>` names the adapter; a link goes to the one whose links match its host; an id another call
        returned goes back to the adapter that returned it.

        Raises:
            HeinzelError: If none or several of `names` could hold it; the message names them and what to pass.
        """
        names = tuple(names)
        head, colon, rest = ref.partition(":")
        if colon and head in names and rest and not rest.startswith("//"):
            return head, rest
        shown = clean_text(ref)[:_SHOWN]
        if "://" in ref:
            host = host_of(ref)
            matches = [
                adapter.let.adapter
                for name in names
                for adapter in self.adapters.get((capability, name), [])[:1]
                if host is not None and host in self.links_of(adapter)
            ]
            if len(matches) == 1:
                return matches[0], ref
            if matches:
                msg = (
                    f"Links on {host} could belong to {' or '.join(matches)}: their {capability} connectors both open "
                    f"them. Name one, e.g. {matches[0]}:<id>."
                )
                raise HeinzelError(msg)
            known = "; ".join(
                f"{adapter.let.adapter} opens {', '.join(self.links_of(adapter)) or 'no declared hosts'}"
                for name in names
                for adapter in self.adapters.get((capability, name), [])[:1]
            )
            msg = f"No {capability} connector that is on opens links on {host or 'that address'} ({known})."
            raise HeinzelError(msg)
        owner = self._issued.get((capability, ref))
        if owner is not None and owner in names:
            return owner, ref
        msg = (
            f"{shown!r} could be in {' or '.join(names)}: pass its link, or name the connector, "
            f"e.g. {names[0]}:{shown}."
        )
        raise HeinzelError(msg)

    def route(self, capability: str, method: str, arguments: Mapping[str, object]) -> str | None:
        """The adapter a call of `method` with `arguments` goes to while several serve `capability` (ADR 0161).

        None while one serves it, or for a method that names no item: then the default serves it, as `bound` says.

        Raises:
            HeinzelError: If the item names none or several of the adapters.
        """
        spec = CAPABILITIES.get(capability)
        names = self.bindings(capability)[0]
        if spec is None or len(names) < 2 or method not in spec.routes:  # noqa: PLR2004 - several
            return None
        ref = arguments.get(spec.routes[method])
        if not isinstance(ref, str):
            return None
        return self.pick(capability, ref, names)[0]

    def binding(self, capability: str) -> tuple[str | None, How]:
        """The adapter that serves `capability` unless a requirement pins another, and how it was chosen (ADR 0055).

        A configured binding wins, `none` included. Otherwise the only installed adapter of the capability serves
        it automatically: installing it was the choice, and there is nothing to choose between; except for a
        capability that is never automatic (`mail`), which waits for the person's binding. With several, the
        core's default serves, if the capability has one; else nothing until the person picks one.
        """
        if self.settings.configured(capability):
            bound = self.settings.bound(capability)
            return (bound, "configured") if bound is not None else (None, "off")
        installed = self.installed(capability)
        default = self.settings.bound(capability)
        spec = CAPABILITIES.get(capability)
        if spec is not None and spec.automatic and len(installed) == 1 and installed[0] != default:
            return installed[0], "automatic"
        if default is not None:
            return default, "default"
        return None, "unbound"

    def bound(self, capability: str) -> str | None:
        """The adapter that serves `capability` unless a requirement pins another: `binding` without the how."""
        return self.binding(capability)[0]

    def handle(self, capability: Capability, func: Callable[..., Any]) -> object:
        """The handle `func` gets for `capability`, for the injector.

        An optional operation (`optional=True`) whose own requirement isn't served gets a handle whose every method
        raises why; anything else `handle_for` refuses is refused here.

        Raises:
            HeinzelError: If `func` acts for no requester, or `handle_for` refuses.
        """
        requester = self._owners.get(func)
        if requester is None:
            name = getattr(func, "__qualname__", repr(func))
            msg = (
                f"{name} asks for {capability.protocol.__name__}, but only operations, jobs, services, info and "
                f"health providers of a registered module can declare capabilities."
            )
            raise HeinzelError(msg)
        requirement, modes = requester.grant(capability.name)
        own = any(r.capability == capability.name for r in requester.requires)
        if requester.optional and own and requirement is not None and modes:
            wanted = Requirement(capability.name, modes, requirement.adapter)
            adapter, problem = self.adapter_for(wanted)
            if adapter is None or problem is not None:
                # An optional operation learns that nothing serves it on its first call, not when it is injected:
                # it asks `CapabilityView.serves` first and degrades instead of failing (`hz org agenda`).
                unmet = self._unmet(requester, wanted, problem)
                label = f"{capability.name}:unserved for {requester.label}"
                return _Handle({}, dict.fromkeys(capability.methods, unmet), label)
        return self.handle_for(requester, capability)

    def handle_for(self, requester: Requester, capability: Capability) -> object:
        """The handle `requester` gets for `capability`: limited to the modes declared, granted and served.

        The seam for code that runs on a requester's behalf without being its function, such as the compiler of
        workflow contracts (ADR 0021): it builds a `Requester` from the contract's declarations and asks here.

        Raises:
            HeinzelError: If the requester didn't declare the capability, has no usable mode, or no adapter serves it.
        """
        requirement, modes = requester.grant(capability.name)
        if requirement is None:
            msg = (
                f"{requester.label} asks for {capability.protocol.__name__} without declaring it; add "
                f'`requires=["{capability.name}:read"]` to its operation, job or module.'
            )
            raise HeinzelError(msg)
        if not modes:
            msg = (
                f"{requester.label} declared {requirement} but may not use it: only publish operations may write; "
                f"declare `{capability.name}:read`."
            )
            raise HeinzelError(msg)
        wanted = Requirement(capability.name, modes, requirement.adapter)
        if wanted.adapter is None and len(self.bindings(capability.name)[0]) > 1:
            return self._routed(requester, capability, requirement, modes)
        adapter, problem = self.adapter_for(wanted)
        if adapter is None or problem is not None:
            raise HeinzelError(self._unmet(requester, wanted, problem))
        # A reader's adapter is built on its first call, not here: a requester that never calls it, such as a setup
        # step that drafts only when asked, doesn't fail because what the adapter itself needs is unbound. A publish
        # operation's adapters are built here, so that one whose factory fails stops it before anything is written.
        if requester.publishes:
            built = self._instance(adapter, capability)
            instance: Callable[[], object] = lambda: built  # noqa: E731 - one object, built once
        else:
            instance = partial(self._instance, adapter, capability)
        return self._handle(requester, capability, requirement, modes, instance, source=adapter.let.adapter)

    def handles(self, capability: Capability, func: Callable[..., Any]) -> "Adapters[Any]":
        """Read handles on every installed adapter of `capability` for `func`, by adapter name (ADR 0057).

        For the module that owns choosing among them, such as `ai.access`: it lists them all, not just the bound one.
        Read modes only, whatever the requester publishes; each adapter is built on its first call. An adapter name two
        modules provide is left out, as binding to it would be refused.

        Raises:
            HeinzelError: If `func` acts for no requester, or its requester didn't declare `capability` for reading.
        """
        requester = self._owners.get(func)
        if requester is None:
            name = getattr(func, "__qualname__", repr(func))
            msg = f"{name} asks for every adapter of {capability.name}, but only a registered module's functions can."
            raise HeinzelError(msg)
        requirement, _ = requester.grant(capability.name)
        inspection = requirement is None
        if inspection:
            requirement = next((r for r in requester.inspects if r.capability == capability.name), None)
        modes = (requirement.modes if requirement else frozenset[Mode]()) - {"write"}
        if requirement is None or not modes:
            msg = (
                f"{requester.label} asks for every adapter of {capability.name} without declaring it; add "
                f'`requires=["{capability.name}:read"]` to its operation or module.'
            )
            raise HeinzelError(msg)
        handles: dict[str, object] = {}
        for name in self.installed(capability.name):
            adapter, problem = self.adapter_for(Requirement(capability.name, modes, name))
            if adapter is None or problem is not None:
                continue
            instance = partial(self._instance, adapter, capability)
            handles[name] = self._handle(
                requester, capability, requirement, modes, instance, inspection=inspection, source=name
            )
        return Adapters(handles)

    def _routed(
        self, requester: Requester, capability: Capability, requirement: Requirement, modes: frozenset[Mode]
    ) -> "_RoutedHandle":
        """A handle on every bound adapter of `capability` that serves some of `modes` (ADR 0161).

        Raises:
            HeinzelError: If none of them is installed once and serves any of `modes`.
        """
        serving = self._serving(capability.name, modes)
        if not serving:
            wanted = Requirement(capability.name, modes)
            problem = self.adapter_for(wanted)[1] or "no bound adapter serves these modes"
            raise HeinzelError(self._unmet(requester, wanted, problem))
        handles = {
            adapter.let.adapter: (
                adapter,
                self._handle(
                    requester,
                    capability,
                    requirement,
                    modes & adapter.let.modes,
                    partial(self._instance, adapter, capability),
                    source=adapter.let.adapter,
                ),
            )
            for adapter in serving
        }
        # Only for its refusals: a method `requester` may not call at all says why, as with one adapter.
        refusals = self._handle(requester, capability, requirement, modes, lambda: None)
        return _RoutedHandle(self, capability, handles, refusals, modes)

    @staticmethod
    def _handle(  # noqa: PLR0913 - explicit inspection restriction in addition to existing mode authority
        requester: Requester,
        capability: Capability,
        requirement: Requirement,
        modes: frozenset[Mode],
        instance: Callable[[], object],
        *,
        inspection: bool = False,
        source: str | None = None,
    ) -> "_Handle":
        """A handle with the methods of `modes`; every other method raises why `requester` can't call it."""
        allowed: dict[str, Callable[..., Any]] = {}
        denied: dict[str, str] = {}
        for name, mode in capability.methods.items():
            if name in capability.core_only:
                denied[name] = f"Only hz itself calls {capability.name}.{name} (ADR 0160); {requester.label} can't."
                continue
            if inspection and (name not in capability.inspection_methods or mode != "read"):
                denied[name] = f"{requester.label} may only inspect {capability.name}; {name} needs an operation grant."
                continue
            if mode in modes:
                allowed[name] = _wrap(instance, capability, name, source)
                continue
            if mode in requirement.modes:
                why = "only publish operations (read_only=False) may write"
            else:
                why = f"declare `{capability.name}:{','.join(m for m in MODES if m in requirement.modes | {mode})}`"
            denied[name] = f"{requester.label} can't call {capability.name}.{name}, which needs {mode}: {why}."
        label = f"{capability.name}:{','.join(m for m in MODES if m in modes)} for {requester.label}"
        return _Handle(allowed, denied, label)

    def _unmet(self, requester: Requester, wanted: Requirement, problem: str | None) -> str:
        """Why no adapter serves `wanted` for `requester`, ending with what to do."""
        return f"{requester.label} needs {wanted}: {problem}. {self.fix(wanted.capability)}"

    def _instance(self, adapter: Adapter, capability: Capability) -> object:
        key = (adapter.let.capability, adapter.let.adapter)
        if key not in self._instances:
            instance = self._build(adapter.let.factory)
            unsupported = declared_unsupported(instance, capability)
            served = [m for m, mode in capability.methods.items() if mode in adapter.let.modes and m not in unsupported]
            if missing := [m for m in served if not callable(getattr(instance, m, None))]:
                msg = f"Adapter {adapter.let.name} of {adapter.module} lacks {', '.join(missing)} of {capability.name}."
                raise HeinzelError(msg)
            self._instances[key] = instance
        return self._instances[key]

    def instance(self, capability: str, name: str) -> tuple[Adapter, object]:
        """The adapter `name` of `capability` itself, built once, for the kernel's own trusted callers (ADR 0159).

        Not a requester's handle: no mode is checked. Only the kernel calls it, for hz's own AI engine.

        Raises:
            HeinzelError: If no adapter or two adapters of `capability` have that name, or it can't be built.
        """
        candidates = self.adapters.get((capability, name), [])
        if len(candidates) != 1:
            why = "is installed" if not candidates else "is unambiguous"
            msg = f"No {capability} adapter called {name!r} {why}."
            raise HeinzelError(msg)
        return candidates[0], self._instance(candidates[0], CAPABILITIES[capability])

    def installed(self, capability: str) -> list[str]:
        """The names of the installed adapters of `capability`, sorted."""
        return sorted({adapter for (name, adapter) in self.adapters if name == capability})

    def command(self, capability: str) -> str:
        """The command line that serves `capability`: bind the first installed adapter, or find a plugin with one."""
        installed = self.installed(capability)
        if not installed:
            return f"hz system plugin search --text {capability}"
        return f"hz system config set {CAPABILITIES_SECTION}.{capability.replace('-', '_')} {installed[0]}"

    def fix(self, capability: str) -> str:
        """What to do so that `capability` is served, ending with the command line that does it."""
        installed = self.installed(capability)
        if not installed:
            return f"Find a plugin that provides {capability}: {self.command(capability)}"
        return f"Bind an installed adapter ({', '.join(installed)}): {self.command(capability)}"

    @property
    def view(self) -> "CapabilityView":
        """A read-only view for modules: reports, never handles."""
        return CapabilityView(self)


class CapabilityView:
    """What modules may know about capabilities: bindings, adapters, the matrix and fixes; no handles (ADR 0028).

    `hz system capability` and `hz system runner` ask for it by type. Asking for a capability goes through a
    declaration (`requires=`) and injection, never through this view.
    """

    __slots__ = ("_capabilities",)

    def __init__(self, capabilities: Capabilities) -> None:
        """Wrap the kernel's capabilities."""
        self._capabilities = capabilities

    @property
    def adapters(self) -> list[Adapter]:
        """Every registered adapter, by capability and name."""
        return [adapter for _, group in sorted(self._capabilities.adapters.items()) for adapter in group]

    @property
    def unknown_keys(self) -> tuple[str, ...]:
        """Keys of the `[capabilities]` table that name no capability, e.g. typos."""
        return self._capabilities.unknown_keys

    def bound(self, capability: str) -> str | None:
        """The adapter that serves `capability`: configured, chosen automatically or the default; None if none."""
        return self._capabilities.bound(capability)

    def binding(self, capability: str) -> tuple[str | None, How]:
        """The adapter that serves `capability`, and how it was chosen: configured, automatic, default, off, unbound."""
        return self._capabilities.binding(capability)

    def bindings(self, capability: str) -> tuple[tuple[str, ...], How]:
        """Every adapter that serves `capability`, the default first, and how they were chosen (ADR 0161)."""
        return self._capabilities.bindings(capability)

    def links(self, adapter: Adapter) -> tuple[str, ...]:
        """The hosts of the links `adapter` opens: its declared links, else the hosts it reaches (ADR 0161)."""
        return self._capabilities.links_of(adapter)

    def defaulted(self, capability: str) -> bool:
        """Whether `capability` is served by the core's default, because nothing is configured or chosen."""
        return self._capabilities.binding(capability)[1] == "default"

    def hosts(self, adapter: Adapter) -> tuple[tuple[str, ...], str | None]:
        """The host names `adapter` reaches, and why they are unknown when its settings decide them and that failed."""
        return self._capabilities.hosts_of(adapter)

    def matrix(self) -> list[CapabilityUse]:
        """Who needs what, in which modes, through which adapter and hosts, and what is unmet."""
        return self._capabilities.matrix()

    def unknown_bindings(self) -> list[tuple[str, str]]:
        """Bindings that name an adapter no installed module provides, as (capability, adapter)."""
        return self._capabilities.unknown_bindings()

    def serves(self, requirement: Requirement) -> tuple[str | None, str | None]:
        """The adapter that would serve `requirement` and why it can't, if it can't; builds no adapter."""
        adapter, problem = self._capabilities.adapter_for(requirement)
        return (adapter.let.adapter if adapter else None), problem

    def installed(self, capability: str) -> list[str]:
        """The names of the installed adapters of `capability`, sorted."""
        return self._capabilities.installed(capability)

    def command(self, capability: str) -> str:
        """The command line that serves `capability`."""
        return self._capabilities.command(capability)

    def fix(self, capability: str) -> str:
        """What to do so that `capability` is served, ending with the command line that does it."""
        return self._capabilities.fix(capability)
