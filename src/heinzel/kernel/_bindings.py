"""Binding capabilities to adapters, and handing requesters handles limited to what they declared (ADR 0028).

Configuration binds each capability to an adapter by name, in the layered configuration's `[capabilities]` table
(user and team files, `HZ_CAPABILITIES__<CAPABILITY>` variables). A requester (an operation, a job, or a module for
its services, info and health providers) gets a capability only if it declared it, and only in the modes it declared
and the bound adapter serves; `write` only in publish operations (`read_only=False`).
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

from pydantic import BaseModel, Field, model_validator

from heinzel.kernel._capabilities import CAPABILITIES, MODES, Capability, Mode, Requirement
from heinzel.kernel._config import ModuleSettings
from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._lets import ProviderLet

CAPABILITIES_SECTION = "capabilities"

RequesterKind = Literal["module", "operation", "job"]


class CapabilitySettings(ModuleSettings):
    """Which adapter serves each capability: the `[capabilities]` table or `HZ_CAPABILITIES__<CAPABILITY>` variables.

    Keys are capability names with `_` or `-` (`code_host` or `code-host`); values are adapter names, as
    `hz system capability list` shows them. A capability without a binding serves nobody.
    """

    code_host: str | None = Field(default=None, description="Adapter that serves `code-host`, e.g. `github`.")
    documents: str | None = Field(default=None, description="Adapter that serves `documents`.")
    tracker: str | None = Field(default=None, description="Adapter that serves `tracker`, e.g. `github`.")

    @model_validator(mode="before")
    @classmethod
    def _dashes(cls, data: Any) -> Any:  # noqa: ANN401 - raw settings input
        if isinstance(data, Mapping):
            return {str(key).replace("-", "_"): value for key, value in cast("Mapping[str, Any]", data).items()}
        return data

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
    """What its module declared, which it may use too."""
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
        modes = frozenset[Mode]().union(*(r.modes for r in found))
        if not self.publishes:
            modes -= {"write"}
        pin = next((r.adapter for r in found if r.adapter), None)
        declared = frozenset[Mode]().union(*(r.modes for r in found))
        return Requirement(capability, declared, pin), modes


@dataclass(frozen=True, slots=True)
class Adapter:
    """A registered adapter: the module advertising it and its declaration."""

    module: str
    let: ProviderLet


class CapabilityUse(BaseModel):
    """One row of the matrix: who needs which capability in which modes, through which adapter and hosts."""

    requester: str = Field(description="Who declared it: a module, or `<module> <operation or job>`.")
    kind: RequesterKind = Field(description="What the requester is: module, operation or job.")
    requirement: str = Field(description="As declared, e.g. `documents:read,write` or `github:tracker:read`.")
    capability: str = Field(description="The capability, e.g. `tracker`.")
    modes: list[Mode] = Field(description="The modes declared.")
    adapter: str | None = Field(description="The adapter that serves it: the pinned or bound one; none if unbound.")
    hosts: list[str] = Field(description="Hosts that adapter sends data to; empty if unknown or unbound.")
    problem: str | None = Field(description="Why the requirement is unmet; none when it is met.")


class _Handle:
    """A requester's view of an adapter: the capability's methods in the granted modes, nothing else."""

    __slots__ = ("_adapter", "_capability", "_declared", "_modes", "_requester")

    def __init__(
        self, adapter: object, capability: Capability, modes: frozenset[Mode], declared: frozenset[Mode], requester: str
    ) -> None:
        self._adapter = adapter
        self._capability = capability
        self._modes = modes
        self._declared = declared
        self._requester = requester

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401 - the adapter's method
        mode = self._capability.methods.get(name)
        if mode is None:
            msg = f"{self._capability.name} has no method {name!r}; it has {', '.join(self._capability.methods)}"
            raise AttributeError(msg)
        if mode not in self._modes:
            if mode in self._declared:
                why = "only publish operations (read_only=False) may write"
            else:
                why = f"declare `{self._capability.name}:{','.join(m for m in MODES if m in self._declared | {mode})}`"
            msg = f"{self._requester} can't call {self._capability.name}.{name}, which needs {mode}: {why}."
            raise HeinzelError(msg)
        return getattr(self._adapter, name)

    def __repr__(self) -> str:
        modes = ",".join(m for m in MODES if m in self._modes)
        return f"<{self._capability.name}:{modes} for {self._requester} via {type(self._adapter).__name__}>"


class Capabilities:
    """The adapters, the requirements and the bindings; resolves a capability for a requester (ADR 0028)."""

    def __init__(
        self,
        adapters: Iterable[Adapter],
        requesters: Iterable[Requester],
        owners: Mapping[Callable[..., Any], Requester],
        settings: CapabilitySettings,
        build: Callable[[Callable[..., Any]], Any],
    ) -> None:
        """Hold what the kernel registered.

        Args:
            adapters: Every registered adapter, at most one per capability and adapter name.
            requesters: Every module, operation and job, in registration order.
            owners: Every function that may ask for a capability, with the requester it acts for.
            settings: The bindings.
            build: Calls an adapter factory with injection, e.g. `Kernel.call`.
        """
        self.adapters = {(a.let.capability, a.let.adapter): a for a in adapters}
        self._requesters = [r for r in requesters if r.requires]
        self._owners = dict(owners)
        self.settings = settings
        self._build = build
        self._instances: dict[tuple[str, str], object] = {}

    @property
    def requesters(self) -> list[Requester]:
        """Every requester that declared something itself, in registration order."""
        return list(self._requesters)

    def adapter_for(self, requirement: Requirement) -> tuple[Adapter | None, str | None]:
        """The adapter that serves `requirement` (pinned or bound), and why it can't, if it can't."""
        name = requirement.adapter or self.settings.bound(requirement.capability)
        if name is None:
            return None, "no adapter bound"
        adapter = self.adapters.get((requirement.capability, name))
        if adapter is None:
            return None, f"no installed module provides {requirement.capability} as {name!r}"
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
                        modes=[m for m in MODES if m in requirement.modes],
                        adapter=adapter.let.adapter if adapter else None,
                        hosts=list(adapter.let.hosts) if adapter else [],
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
        """The handle `func` gets for `capability`: limited to the modes declared, granted and served.

        Raises:
            HeinzelError: If `func` is no requester, didn't declare the capability, or no adapter can serve it.
        """
        requester = self._owners.get(func)
        name = getattr(func, "__qualname__", repr(func))
        if requester is None:
            msg = (
                f"{name} asks for {capability.protocol.__name__}, but only operations, jobs, services, info and "
                f"health providers of a registered module can declare capabilities."
            )
            raise HeinzelError(msg)
        requirement, modes = requester.grant(capability.name)
        if requirement is None:
            msg = (
                f"{requester.label} asks for {capability.protocol.__name__} without declaring it; add "
                f'`requires=["{capability.name}:read"]` to its operation, job or module.'
            )
            raise HeinzelError(msg)
        wanted = Requirement(capability.name, modes or frozenset({"read"}), requirement.adapter)
        adapter, problem = self.adapter_for(wanted)
        if adapter is None or problem is not None:
            msg = f"{requester.label} needs {wanted}: {problem}. {self.fix(capability.name)}"
            raise HeinzelError(msg)
        key = (adapter.let.capability, adapter.let.adapter)
        if key not in self._instances:
            self._instances[key] = self._build(adapter.let.factory)
        return _Handle(self._instances[key], capability, modes, requirement.modes, requester.label)

    def fix(self, capability: str) -> str:
        """What to do so that `capability` is served: the binding to write, with the installed adapters."""
        installed = sorted(adapter for (name, adapter) in self.adapters if name == capability)
        if not installed:
            return f"Install a plugin that provides {capability}."
        key = capability.replace("-", "_")
        return f'Bind it in the configuration: [{CAPABILITIES_SECTION}] {key} = "{installed[0]}" (installed: ' + (
            ", ".join(installed) + ")"
        )
