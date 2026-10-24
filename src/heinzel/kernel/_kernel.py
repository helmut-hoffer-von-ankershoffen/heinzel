"""The kernel: a registry of modules plus a small type-based injector."""

import inspect
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any, cast, get_origin

import pluggy
from pydantic import ConfigDict, ValidationError, validate_call

from heinzel.kernel._bindings import CAPABILITIES_SECTION, Adapter, Capabilities, CapabilitySettings, Requester
from heinzel.kernel._capabilities import CAPABILITIES, capability_of
from heinzel.kernel._config import KERNEL_SECTION, Config, ModuleSettings
from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._installation import Installation
from heinzel.kernel._lets import (
    HealthLet,
    InfoLet,
    JobLet,
    Let,
    OperationLet,
    ProviderLet,
    RequirementLet,
    ServiceLet,
    SettingsLet,
)
from heinzel.kernel._module import Module
from heinzel.kernel._progress import Progress
from heinzel.kernel._settings import Settings
from heinzel.kernel._spec import PROJECT_NAME, Spec

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Origin:
    """Where a module comes from."""

    distribution: str
    version: str
    builtin: bool


@dataclass(frozen=True, slots=True)
class ModuleRecord:
    """A discovered module, under its qualified name (e.g. `system.changelog`)."""

    qualname: str
    module: Module
    origin: Origin

    @property
    def parent(self) -> str | None:
        """Qualified name of the parent module, or None for a top-level module."""
        return self.qualname.rpartition(".")[0] or None

    @property
    def path(self) -> tuple[str, ...]:
        """The qualified name split into its parts."""
        return tuple(self.qualname.split("."))


class Kernel:
    """Holds the registered modules, hands out their lets and injects services."""

    def __init__(
        self,
        settings: Settings | None = None,
        records: Iterable[ModuleRecord] = (),
        errors: Mapping[str, str] | None = None,
        config: Config | None = None,
    ) -> None:
        """Register modules. Disabled modules (and their submodules) are blocked, not registered.

        Args:
            settings: Kernel settings; built from `config` if not given.
            records: Modules to register, typically from `heinzel.kernel.discover`.
            errors: Plugins that failed to load, by entry point name, for reporting.
            config: Configuration files and their content; none (defaults and environment only) if not given.

        A plugin module that collides with a registered one — same qualified name, a submodule named like one of its
        parent's operations, or a service type provided twice — is skipped and reported in `errors`, so one bad
        plugin never takes Heinzelmännchen down. The same collision among built-in modules is a bug and raises.

        Raises:
            HeinzelError: If built-in modules collide.
        """
        self.config = config or Config.empty()
        self.settings = settings or self.config.settings(Settings, KERNEL_SECTION)
        self.errors: dict[str, str] = dict(errors or {})
        self._manager = pluggy.PluginManager(PROJECT_NAME)
        self._manager.add_hookspecs(Spec)
        self._records: dict[str, ModuleRecord] = {}
        self._unavailable: set[type[Any]] = set()
        for record in sorted(records, key=lambda r: (not r.origin.builtin, r.path)):
            if problem := self._collision(record):
                self._reject(record, problem)
                continue
            if self._is_disabled(record.qualname) and not record.module.essential:
                self._manager.set_blocked(record.qualname)
                self._unavailable.update(_provided_types(record.module))
                continue
            self._manager.register(record.module, name=record.qualname)
            self._records[record.qualname] = record

        self._instances: dict[type[Any], object] = {
            Kernel: self,
            Settings: self.settings,
            Config: self.config,
            Progress: Progress(),
        }
        self._factories: dict[type[Any], Callable[..., Any]] = {Installation: Installation}
        # Built-ins first, so a plugin providing a type a built-in provides is the one skipped and reported, whatever
        # its name sorts as.
        for record, service in sorted(self.lets(ServiceLet), key=_builtins_first):
            self._provide(service.provides, service.factory, record)
        for record, declared in sorted(self.lets(SettingsLet), key=_builtins_first):
            self._provide(declared.model, partial(self.config.settings, declared.model, record.qualname), record)
        self._records = dict(sorted(self._records.items(), key=lambda item: item[1].path))
        self.capability_settings = self.config.settings(CapabilitySettings, CAPABILITIES_SECTION)
        requesters, owners = self._requesters()
        self.capabilities = Capabilities(self._adapters(), requesters, owners, self.capability_settings, self.call)
        self._instances[Capabilities] = self.capabilities

    @property
    def modules(self) -> tuple[ModuleRecord, ...]:
        """Registered modules, parents before children."""
        return tuple(self._records.values())

    def module(self, qualname: str) -> ModuleRecord:
        """Look up a registered module by qualified name."""
        return self._records[qualname]

    def children(self, qualname: str | None) -> tuple[ModuleRecord, ...]:
        """Direct submodules of a module; top-level modules for None."""
        return tuple(r for r in self._records.values() if r.parent == qualname)

    def lets[L: Let](self, kind: type[L]) -> list[tuple[ModuleRecord, L]]:
        """Every let of a kind, with the module advertising it, in module order."""
        found: list[tuple[ModuleRecord, L]] = []
        for impl in self._manager.hook.heinzel_lets.get_hookimpls():
            record = self._records[impl.plugin_name]
            lets = cast("Sequence[Let]", impl.function())
            found.extend((record, let) for let in lets if isinstance(let, kind))
        found.sort(key=lambda pair: pair[0].path)
        return found

    def provides(self, kind: object) -> bool:
        """Whether the kernel can inject a parameter annotated with `kind`: a service, settings or a capability."""
        return isinstance(kind, type) and (
            kind in self._instances or kind in self._factories or capability_of(kind) is not None
        )

    def bind[T](self, kind: type[T], instance: T) -> None:
        """Replace what the kernel injects for `kind`; heads use this to bind their own `Progress`."""
        self._instances[kind] = instance

    def available(self, func: Callable[..., Any]) -> bool:
        """Whether `func` can be called: none of its parameters needs a type only a disabled module provides."""
        return not any(
            (get_origin(p.annotation) or p.annotation) in self._unavailable
            for p in inspect.signature(func, eval_str=True).parameters.values()
        )

    def resolve[T](self, kind: type[T]) -> T:
        """Return the instance of `kind`, building it (once) from its service factory.

        Raises:
            HeinzelError: If nothing provides `kind`.
        """
        if kind not in self._instances:
            if kind not in self._factories:
                msg = f"No module provides {kind.__name__}"
                raise HeinzelError(msg)
            self._instances[kind] = self.call(self._factories[kind])
        return cast("T", self._instances[kind])

    def call[R](self, func: Callable[..., R], /, **kwargs: Any) -> R:  # noqa: ANN401 - forwards arbitrary arguments
        """Call `func`, injecting every parameter the kernel provides that the caller didn't pass."""
        return func(**self._inject(func, kwargs))

    def invoke(self, operation: OperationLet, /, **kwargs: Any) -> Any:  # noqa: ANN401 - operations return anything
        """Run an operation for a head: inject dependencies and validate the arguments against their annotations.

        `Annotated[int, Field(ge=1)]` and the like are enforced here, so every head behaves the same.

        Raises:
            HeinzelError: If an argument is invalid, with a message naming it.
        """
        func = operation.func
        injected = {k: v for k, v in self._inject(func, dict(kwargs)).items() if k not in kwargs}
        signature = inspect.signature(func, eval_str=True)

        # Only what the caller passes is validated: injected values are the kernel's own, and capability protocols
        # are not runtime-checkable, so pydantic could not build an isinstance check for them.
        def call(**arguments: Any) -> Any:  # noqa: ANN401 - forwards the operation's result
            return func(**arguments, **injected)

        public = [p for name, p in signature.parameters.items() if name not in injected]
        call.__signature__ = signature.replace(parameters=public)  # type: ignore[attr-defined]  # pydantic reads it
        call.__annotations__ = {p.name: p.annotation for p in public if p.annotation is not inspect.Parameter.empty}
        validated = validate_call(config=ConfigDict(arbitrary_types_allowed=True))(call)
        try:
            return validated(**kwargs)
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'input'}: {e['msg']}" for e in exc.errors())
            msg = f"Invalid arguments for {operation.name}: {problems}"
            raise HeinzelError(msg) from exc

    def _inject(self, func: Callable[..., Any], kwargs: dict[str, Any]) -> dict[str, Any]:
        for name, parameter in inspect.signature(func, eval_str=True).parameters.items():
            annotation = get_origin(parameter.annotation) or parameter.annotation
            if name in kwargs:
                continue
            if capability := capability_of(annotation):
                # A handle per requester: what it may call depends on what `func` declared (ADR 0028).
                kwargs[name] = self.capabilities.handle(capability, func)
            elif self.provides(annotation):
                kwargs[name] = self.resolve(annotation)
        return kwargs

    def settings_of(self) -> list[tuple[str, ModuleSettings]]:
        """Every settings object in effect, by section: the kernel's `hz` first, then each module's."""
        sections: list[tuple[str, ModuleSettings]] = [
            (KERNEL_SECTION, self.settings),
            (CAPABILITIES_SECTION, self.capability_settings),
        ]
        sections.extend((record.qualname, self.resolve(let.model)) for record, let in self.lets(SettingsLet))
        return sections

    def _collision(self, record: ModuleRecord) -> str | None:
        if record.qualname in self._records:
            return f"another module is already named {record.qualname!r}"
        parent = self._records.get(record.parent) if record.parent else None
        if parent and any(
            isinstance(let, OperationLet) and let.name == record.module.name for let in parent.module.lets
        ):
            return f"{parent.qualname!r} already has an operation named {record.module.name!r}"
        return None

    def _reject(self, record: ModuleRecord, problem: str) -> None:
        if record.origin.builtin:
            msg = f"Built-in module {record.qualname!r} can't be registered: {problem}"
            raise HeinzelError(msg)
        logger.warning("Skipping module %r of %s: %s", record.qualname, record.origin.distribution, problem)
        self.errors[record.qualname] = f"Skipped: {problem}"

    def _provide(self, kind: type[Any], factory: Callable[..., Any], record: ModuleRecord) -> None:
        if kind in self._factories or kind in self._instances:
            problem = f"{kind.__name__} is already provided by another module"
            if record.origin.builtin:
                msg = f"Built-in module {record.qualname!r}: {problem}"
                raise HeinzelError(msg)
            self.errors[record.qualname] = f"Service skipped: {problem}"
            return
        self._factories[kind] = factory

    def _adapters(self) -> list[Adapter]:
        adapters: dict[tuple[str, str], Adapter] = {}
        for record, let in sorted(self.lets(ProviderLet), key=_builtins_first):
            problem = None
            if (let.capability, let.adapter) in adapters:
                problem = f"another module already provides {let.capability} as {let.adapter!r}"
            elif missing := _missing_methods(let):
                problem = f"adapter {let.name} lacks {', '.join(missing)} of {let.capability}"
            if problem is None:
                adapters[let.capability, let.adapter] = Adapter(module=record.qualname, let=let)
            elif record.origin.builtin:
                msg = f"Built-in module {record.qualname!r}: {problem}"
                raise HeinzelError(msg)
            else:
                logger.warning("Skipping an adapter of %r: %s", record.qualname, problem)
                self.errors[record.qualname] = f"Adapter skipped: {problem}"
        return list(adapters.values())

    def _requesters(self) -> tuple[list[Requester], dict[Callable[..., Any], Requester]]:
        """Every requester, and every function that may ask for a capability with the requester it acts for."""
        requesters: list[Requester] = []
        owners: dict[Callable[..., Any], Requester] = {}
        for record in self._records.values():
            lets = record.module.lets
            own = tuple(let.requirement for let in lets if isinstance(let, RequirementLet))
            module = Requester(module=record.qualname, kind="module", name=record.module.name, requires=own)
            requesters.append(module)
            for let in lets:
                match let:
                    case OperationLet():
                        requester = Requester(
                            record.qualname, "operation", let.name, let.requires, own, publishes=not let.read_only
                        )
                        requesters.append(requester)
                        owners[let.func] = requester
                    case JobLet():
                        requesters.append(requester := Requester(record.qualname, "job", let.name, let.requires, own))
                        owners[let.func] = requester
                    case ServiceLet() | ProviderLet():
                        owners[let.factory] = module
                    case InfoLet() | HealthLet():
                        owners[let.provider] = module
                    case _:
                        pass
        return requesters, owners

    def _is_disabled(self, qualname: str) -> bool:
        parts = qualname.split(".")
        return any(".".join(parts[: i + 1]) in self.settings.disabled_modules for i in range(len(parts)))


def _builtins_first(pair: tuple[ModuleRecord, Let]) -> tuple[bool, tuple[str, ...]]:
    return (not pair[0].origin.builtin, pair[0].path)


def _missing_methods(let: ProviderLet) -> list[str]:
    """Protocol methods a class-based adapter doesn't define; a factory function is checked when it is called."""
    if not isinstance(let.factory, type):
        return []
    return [name for name in CAPABILITIES[let.capability].methods if not callable(getattr(let.factory, name, None))]


def _provided_types(module: Module) -> set[type[Any]]:
    provided: set[type[Any]] = set()
    for let in module.lets:
        if isinstance(let, ServiceLet):
            provided.add(let.provides)
        elif isinstance(let, SettingsLet):
            provided.add(let.model)
    return provided
