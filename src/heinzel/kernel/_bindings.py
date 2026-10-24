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

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar, Literal, cast

from pydantic import Field, model_validator

from heinzel.kernel._capabilities import CAPABILITIES, MODES, Capability, Mode, Requirement
from heinzel.kernel._config import ModuleSettings
from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._lets import ProviderLet

CAPABILITIES_SECTION = "capabilities"

RequesterKind = Literal["module", "operation", "job"]


class CapabilitySettings(ModuleSettings):
    """Which adapter serves each capability: the `[capabilities]` table or `HZ_CAPABILITIES__<CAPABILITY>` variables.

    Keys are capability names with `_` or `-` (`code_host` or `code-host`); values are adapter names, as
    `hz system capability list` shows them. A capability without a binding serves nobody. A project `hz.toml` can't
    bind: only the site, user and `HZ_CONFIG` files and the environment can.
    """

    agent_runner: str | None = Field(
        default="claude-code",
        description="Adapter that serves `agent-runner`: `claude-code` (the default, because `command` needs a "
        "command line first), `command`, or one from a plugin.",
    )
    code_host: str | None = Field(default=None, description="Adapter that serves `code-host`, e.g. `github`.")
    documents: str | None = Field(default=None, description="Adapter that serves `documents`.")
    tracker: str | None = Field(default=None, description="Adapter that serves `tracker`, e.g. `github`.")

    ignored_in_project: ClassVar[frozenset[str]] = frozenset({"agent_runner", "code_host", "documents", "tracker"})

    @model_validator(mode="before")
    @classmethod
    def _dashes(cls, data: Any) -> Any:  # noqa: ANN401 - raw settings input
        if isinstance(data, Mapping):
            return {str(key).replace("-", "_"): value for key, value in cast("Mapping[str, Any]", data).items()}
        return data  # pragma: no cover - pydantic-settings always passes a mapping

    def bound(self, capability: str) -> str | None:
        """The adapter bound to `capability`, or None."""
        return cast("str | None", getattr(self, capability.replace("-", "_"), None))


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
    """Hosts that adapter declares; empty if it leaves them to its configuration, or none serves."""
    problem: str | None
    """Why the requirement is unmet; None when it is met."""


class _Handle:
    """A requester's view of an adapter: wrappers around the capability's methods in the granted modes, nothing else.

    It holds no reference to the adapter as an attribute, and its methods are plain functions, not bound methods.
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


def _wrap(method: Callable[..., Any], capability: Capability, name: str) -> Callable[..., Any]:
    def call(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401 - forwards the protocol method
        return method(*args, **kwargs)

    # Not functools.wraps: it would set __wrapped__ to the bound method and with it the adapter.
    call.__name__ = name
    call.__qualname__ = f"{capability.protocol.__name__}.{name}"
    call.__doc__ = getattr(capability.protocol, name).__doc__
    return call


class Capabilities:
    """The adapters, the requirements and the bindings; resolves a capability for a requester (ADR 0028).

    Kernel-internal: modules get a `CapabilityView`, which reports but hands out nothing.
    """

    def __init__(  # noqa: PLR0913, PLR0917 - everything the kernel registered
        self,
        adapters: Iterable[Adapter],
        requesters: Iterable[Requester],
        owners: Mapping[Callable[..., Any], Requester],
        settings: CapabilitySettings,
        build: Callable[[Callable[..., Any]], Any],
        unknown_keys: Iterable[str] = (),
    ) -> None:
        """Hold what the kernel registered.

        Args:
            adapters: Every registered adapter; two with one adapter name for one capability are ambiguous.
            requesters: Every module, operation and job, in registration order.
            owners: Every function that may ask for a capability, with the requester it acts for.
            settings: The bindings.
            build: Calls an adapter factory with injection, e.g. `Kernel.call`.
            unknown_keys: Keys of the `[capabilities]` table that name no capability, e.g. typos.
        """
        self.adapters: dict[tuple[str, str], list[Adapter]] = {}
        for adapter in adapters:
            self.adapters.setdefault((adapter.let.capability, adapter.let.adapter), []).append(adapter)
        self.requesters = [r for r in requesters if r.requires]
        self._owners = dict(owners)
        self.settings = settings
        self.unknown_keys = tuple(unknown_keys)
        self._build = build
        self._instances: dict[tuple[str, str], object] = {}

    def adapter_for(self, requirement: Requirement) -> tuple[Adapter | None, str | None]:
        """The adapter that serves `requirement` (pinned or bound), and why it can't, if it can't."""
        name = requirement.adapter or self.settings.bound(requirement.capability)
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
        """Who needs what, in which modes, through which adapter and hosts, and what is unmet."""
        rows: list[CapabilityUse] = []
        for requester in self.requesters:
            for requirement in requester.requires:
                adapter, problem = self.adapter_for(requirement)
                rows.append(
                    CapabilityUse(
                        requester=requester.label,
                        kind=requester.kind,
                        requirement=str(requirement),
                        capability=requirement.capability,
                        modes=tuple(m for m in MODES if m in requirement.modes),
                        adapter=adapter.let.adapter if adapter else None,
                        hosts=adapter.let.hosts if adapter else (),
                        problem=problem,
                    )
                )
        return rows

    def unknown_bindings(self) -> list[tuple[str, str]]:
        """Bindings that name an adapter no installed module provides, as (capability, adapter)."""
        return [
            (name, bound)
            for name in CAPABILITIES
            if (bound := self.settings.bound(name)) is not None and (name, bound) not in self.adapters
        ]

    def handle(self, capability: Capability, func: Callable[..., Any]) -> object:
        """The handle `func` gets for `capability`, for the injector.

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
        adapter, problem = self.adapter_for(wanted)
        if adapter is None or problem is not None:
            msg = f"{requester.label} needs {wanted}: {problem}. {self.fix(capability.name)}"
            raise HeinzelError(msg)
        instance = self._instance(adapter, capability)
        allowed: dict[str, Callable[..., Any]] = {}
        denied: dict[str, str] = {}
        for name, mode in capability.methods.items():
            if mode in modes:
                allowed[name] = _wrap(getattr(instance, name), capability, name)
                continue
            if mode in requirement.modes:
                why = "only publish operations (read_only=False) may write"
            else:
                why = f"declare `{capability.name}:{','.join(m for m in MODES if m in requirement.modes | {mode})}`"
            denied[name] = f"{requester.label} can't call {capability.name}.{name}, which needs {mode}: {why}."
        label = f"{capability.name}:{','.join(m for m in MODES if m in modes)} for {requester.label}"
        return _Handle(allowed, denied, label)

    def _instance(self, adapter: Adapter, capability: Capability) -> object:
        key = (adapter.let.capability, adapter.let.adapter)
        if key not in self._instances:
            instance = self._build(adapter.let.factory)
            served = [m for m, mode in capability.methods.items() if mode in adapter.let.modes]
            if missing := [m for m in served if not callable(getattr(instance, m, None))]:
                msg = f"Adapter {adapter.let.name} of {adapter.module} lacks {', '.join(missing)} of {capability.name}."
                raise HeinzelError(msg)
            self._instances[key] = instance
        return self._instances[key]

    def fix(self, capability: str) -> str:
        """What to do so that `capability` is served: the binding to write, with the installed adapters."""
        installed = sorted({adapter for (name, adapter) in self.adapters if name == capability})
        if not installed:
            return f"Install a plugin that provides {capability}."
        key = capability.replace("-", "_")
        return (
            f'Bind it in your user configuration: [{CAPABILITIES_SECTION}] {key} = "{installed[0]}" '
            f"(installed: {', '.join(installed)})"
        )

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
        """The adapter bound to `capability`, or None."""
        return self._capabilities.settings.bound(capability)

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

    def fix(self, capability: str) -> str:
        """What to do so that `capability` is served."""
        return self._capabilities.fix(capability)
