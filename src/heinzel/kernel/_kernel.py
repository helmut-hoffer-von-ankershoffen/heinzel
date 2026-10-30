"""The kernel: a registry of modules plus a small type-based injector."""

import inspect
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Annotated, Any, cast, get_args, get_origin

import pluggy
from pydantic import ConfigDict, ValidationError, validate_call

from heinzel.kernel._bindings import (
    CAPABILITIES_SECTION,
    Adapter,
    Adapters,
    Capabilities,
    CapabilitySettings,
    CapabilityView,
    Requester,
)
from heinzel.kernel._capabilities import CAPABILITIES, Capability, capability_of
from heinzel.kernel._config import KERNEL_SECTION, Config, ModuleSettings
from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._identity import PRIVATE_READ, PRIVATE_WRITE, CurrentUser
from heinzel.kernel._installation import CORE_DISTRIBUTION, Installation
from heinzel.kernel._jobs import CompiledJobs
from heinzel.kernel._layers import Declared, Label, LayerLet, LayerProblem, LayerProblemKind, Layers, LayerStack
from heinzel.kernel._lets import (
    CredentialLet,
    HealthLet,
    InfoLet,
    JobLet,
    Let,
    OperationLet,
    ProviderLet,
    RequirementLet,
    ServiceLet,
    SettingsLet,
    SetupLet,
    settings_parameters,
)
from heinzel.kernel._local_web import LocalWebRuntime
from heinzel.kernel._module import Module
from heinzel.kernel._org import OrgContext
from heinzel.kernel._playbooks import PlaybookLet
from heinzel.kernel._progress import Progress
from heinzel.kernel._prompter import Prompter
from heinzel.kernel._services import ServiceContract
from heinzel.kernel._settings import Settings
from heinzel.kernel._spec import PROJECT_NAME, Spec

logger = logging.getLogger(__name__)

ADAPTER_ONLY_LETS = (ProviderLet, RequirementLet, SettingsLet, CredentialLet, LayerLet)
"""Lets that leave a module adapter-only: none of them runs code that could use the module's requirements, so its
needs count only while one of its adapters serves someone. Any other let, including kinds added later, makes them
count always."""


@dataclass(frozen=True, slots=True)
class Origin:
    """Where a module comes from."""

    distribution: str
    version: str
    builtin: bool
    url: str | None = None
    """Source repository of the distribution, from its Project-URL metadata; None if it names none."""


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


def _current_user() -> CurrentUser:
    """Build identity from trusted user settings only when a head actually needs it."""
    home = Path.home()
    state = Config.load(home).settings(Settings, KERNEL_SECTION).state_dir.expanduser()
    # Relative user settings must not implicitly select the repository a head happened to start in.
    return CurrentUser((state if state.is_absolute() else home / state) / "identity")


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
        self._blocked: dict[str, ModuleRecord] = {}
        for record in sorted(records, key=lambda r: (not r.origin.builtin, r.path)):
            if problem := self._collision(record):
                self._reject(record, problem)
                continue
            if self._is_disabled(record.qualname) and not record.module.essential:
                self._manager.set_blocked(record.qualname)
                self._blocked[record.qualname] = record
                self._unavailable.update(_provided_types(record.module))
                continue
            self._manager.register(record.module, name=record.qualname)
            self._records[record.qualname] = record

        self._instances: dict[type[Any], object] = {
            Kernel: self,
            Settings: self.settings,
            Config: self.config,
            Progress: Progress(),
            LocalWebRuntime: LocalWebRuntime(),
            Prompter: Prompter(),
        }
        self._factory_instances: dict[type[Any], object] = {}
        """Instances constructed by registered factories; explicit binds invalidate this provenance."""
        self._factories: dict[type[Any], Callable[..., Any]] = {
            Installation: Installation,
            CurrentUser: _current_user,
        }
        self._layers: Layers | None = None
        self._providers: dict[type[Any], str] = {}
        """The module providing each service type, by qualified name."""
        # Built-ins first, so a plugin providing a type a built-in provides is the one skipped and reported, whatever
        # its name sorts as.
        for record, service in sorted(self.lets(ServiceLet), key=_builtins_first):
            self._provide(service.provides, service.factory, record)
        for record, declared in sorted(self.lets(SettingsLet), key=_builtins_first):
            renamed = record.module.renamed_from if record.parent is None else None
            factory = partial(self.config.settings, declared.model, record.qualname, renamed_from=renamed)
            self._provide(declared.model, factory, record)
        self._records = dict(sorted(self._records.items(), key=lambda item: item[1].path))
        self.capability_settings = self.config.settings(CapabilitySettings, CAPABILITIES_SECTION)
        requesters, owners = self._requesters()
        unknown = [
            k for k in self.config.section(CAPABILITIES_SECTION) if _field(k) not in CapabilitySettings.model_fields
        ]
        self._capabilities = Capabilities(
            self._adapters(),
            requesters,
            owners,
            self.capability_settings,
            self.call,
            unknown_keys=sorted(unknown),
            compiled=self._compiled_requesters,
            # Settings only, not `call`: hosts are read from configuration, never from services or credentials.
            settings_of=self.resolve,
        )
        # Modules get a view that reports; handles come only through declarations and injection (ADR 0028).
        self._instances[CapabilityView] = self._capabilities.view

    @property
    def modules(self) -> tuple[ModuleRecord, ...]:
        """Registered modules, parents before children."""
        return tuple(self._records.values())

    @property
    def blocked(self) -> tuple[ModuleRecord, ...]:
        """Modules `[hz] disabled_modules` switched off, with their submodules: discovered but not registered."""
        return tuple(sorted(self._blocked.values(), key=lambda record: record.path))

    def module(self, qualname: str) -> ModuleRecord:
        """Look up a registered module by qualified name."""
        return self._records[qualname]

    def renamed(self, old: str) -> str | None:
        """The top-level module that was called `old` before a rename (`Module(renamed_from=...)`), if one is."""
        return next(
            (r.qualname for r in self._records.values() if r.parent is None and r.module.renamed_from == old), None
        )

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
        kind = _injected_type(kind)
        return isinstance(kind, type) and (
            kind in self._instances or kind in self._factories or capability_of(kind) is not None or kind is Adapters
        )

    def bind[T](self, kind: type[T], instance: T) -> None:
        """Replace what the kernel injects for `kind`; heads use this to bind their own `Progress`."""
        self._instances[kind] = instance
        self._factory_instances.pop(kind, None)

    def available(self, func: Callable[..., Any]) -> bool:
        """Whether required services exist, including explicit contracts and transitive factory dependencies."""
        return self._available(func, frozenset())

    def _available(self, func: Callable[..., Any], visiting: frozenset[type[Any]]) -> bool:
        for parameter in inspect.signature(func, eval_str=True).parameters.values():
            kind = _injected_type(parameter.annotation)
            if not isinstance(kind, type):
                continue
            if (kind in self._unavailable or _service_contract(kind)) and not self.provides(kind):
                return False
            factory = self._factories.get(kind)
            if (
                factory is not None
                and kind not in self._instances
                and kind not in visiting
                and not self._available(factory, visiting | {kind})
            ):
                return False
        return True

    def resolve[T](self, kind: type[T]) -> T:
        """Return the instance of `kind`, building it (once) from its service factory.

        Raises:
            HeinzelError: If nothing provides `kind`.
        """
        if kind not in self._instances:
            if kind not in self._factories:
                msg = f"No module provides {kind.__name__}"
                raise HeinzelError(msg)
            instance = self.call(self._factories[kind])
            self._instances[kind] = instance
            self._factory_instances[kind] = instance
        return cast("T", self._instances[kind])

    def resolve_builtin_service[T](self, kind: type[T], owner: str) -> T:
        """Resolve a service only from its named built-in module and its exact registered class.

        This checks registry origin and injection continuity, not a publisher signature or code attestation.

        Raises:
            HeinzelError: If the provider or bound instance is not the named built-in class.
        """
        record = self._records.get(owner)
        factory = self._factories.get(kind)
        if (
            self._providers.get(kind) != owner
            or record is None
            or not record.origin.builtin
            or record.origin.distribution != CORE_DISTRIBUTION
            or not isinstance(factory, type)
            or not factory.__module__.startswith(f"heinzel.modules.{owner}.")
        ):
            msg = "The expected built-in service provider is unavailable"
            raise HeinzelError(msg)
        instance = self.resolve(kind)
        if type(instance) is not factory or self._factory_instances.get(kind) is not instance:
            msg = "The expected built-in service instance changed"
            raise HeinzelError(msg)
        return instance

    def resolve_registered_service[T](self, kind: type[T], record: ModuleRecord) -> T:
        """Resolve the exact class instance registered by this kernel's selected module record.

        Selection must already come from trusted host configuration; this does not authenticate a publisher,
        grant data access, or prove that an installed implementation is safe.

        Raises:
            HeinzelError: If the record, factory, or cached instance no longer matches the registration.
        """
        factory = self._factories.get(kind)
        if (
            self._records.get(record.qualname) is not record
            or self._providers.get(kind) != record.qualname
            or not isinstance(factory, type)
        ):
            msg = "The selected registered service is unavailable"
            raise HeinzelError(msg)
        instance = self.resolve(kind)
        if type(instance) is not factory or self._factory_instances.get(kind) is not instance:
            msg = "The selected registered service instance changed"
            raise HeinzelError(msg)
        return instance

    def resolve_selected_service[T](self, contract: type[T], record: ModuleRecord) -> T:
        """Resolve one concrete service offered by the exact host-selected module record.

        Different modules may offer distinct concrete subclasses of one contract without competing for a global
        service key. The caller must derive the selected record from trusted configuration; this is no grant or
        publisher authentication.

        Raises:
            HeinzelError: If the record or its unique concrete class service is unavailable.
        """
        if self._records.get(record.qualname) is not record:
            msg = "The selected registered service is unavailable"
            raise HeinzelError(msg)
        matches = [
            let.provides
            for owner, let in self.lets(ServiceLet)
            if owner is record
            and let.provides is not contract
            and issubclass(let.provides, contract)
            and let.factory is let.provides
        ]
        if len(matches) != 1:
            msg = "The selected module does not provide exactly one eligible service"
            raise HeinzelError(msg)
        return self.resolve_registered_service(matches[0], record)

    def handle_for(self, requester: Requester, capability: str) -> object:
        """A handle on `capability` for `requester`, for code that acts on its behalf without being its function.

        The seam for reads declared by data rather than code, made by the module that compiles jobs: workflow
        contracts' inputs (ADR 0021), playbooks' discovery hints and the approved version of an asset (ADR 0037). The
        module builds a `Requester` of kind `job` from what the data declares and asks here, getting only the read
        modes it declared and the bound adapter serves. Writes come with approvals (`hz inbox`, ADR 0020), so a
        publishing requester is refused.

        Raises:
            HeinzelError: If the capability is unknown; the requester isn't a job of the enabled module providing
                `CompiledJobs`, publishes or pins an adapter; it didn't declare the capability; or no adapter serves it.
        """
        spec = CAPABILITIES.get(capability)
        if spec is None:
            msg = f"Unknown capability {capability!r}; known: {', '.join(sorted(CAPABILITIES))}"
            raise HeinzelError(msg)
        compiler = self._providers.get(CompiledJobs)
        if requester.kind != "job" or compiler is None or requester.module != compiler:
            msg = (
                f"{requester.label} can't get handles on another's behalf: only jobs of the module that compiles "
                f"jobs ({compiler or 'none enabled'}) can."
            )
            raise HeinzelError(msg)
        if requester.publishes:
            msg = (
                f"{requester.label} publishes; handles for code acting on a requester's behalf are read-only until "
                f"approvals exist (`hz inbox`, ADR 0020)."
            )
            raise HeinzelError(msg)
        if pins := [str(r) for r in (*requester.requires, *requester.inherited) if r.adapter]:
            msg = f"{requester.label} pins {', '.join(pins)}: compiled jobs use the adapters configuration binds."
            raise HeinzelError(msg)
        return self._capabilities.handle_for(requester, spec)

    def layers(self) -> Layers:
        """Every installed layer, checked once: what is wrong with the tree is in `problems` (ADR 0040)."""
        if self._layers is None:
            # The adapters the kernel registered, by plugin: a layer brings its whole plugin's.
            adapters: dict[str, list[str]] = {}
            for registered in (a for found in self._capabilities.adapters.values() for a in found):
                adapters.setdefault(registered.module.split(".")[0], []).append(registered.let.name)
            playbooks = {record.qualname: let for record, let in self.lets(PlaybookLet)}
            self._layers = Layers(
                Declared(
                    let=let,
                    module=record.qualname,
                    distribution=record.origin.distribution,
                    adapters=tuple(sorted(adapters.get(record.path[0], ()))),
                    playbook=playbooks.get(record.qualname),
                    plugin=not record.origin.builtin and record.parent is None,
                )
                for record, let in self.lets(LayerLet)
            )
        return self._layers

    def layer_stack(self, layer: str | None = None) -> LayerStack:
        """The stack of `layer`, or of the layer the person is in, general to specific (ADR 0040); built afresh.

        Without `layer`, the person's layer is what their team declares (`OrgContext.layer`, provided by `team`);
        when nothing declares one, the only team layer installed, or the only company when there is no team. Built on
        every call, so a team joined or a `[team] layer` set earlier in the same process counts.
        """
        tree = self.layers()
        if layer is not None:
            return tree.stack(layer, source="asked for")
        declared, problems = None, list[LayerProblem]()
        if self.provides(OrgContext):
            try:
                declared = self.resolve(OrgContext).layer()
            # An unreadable team repository or invalid team settings leave the stack to what is installed.
            except (HeinzelError, ValidationError) as exc:
                reason = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else str(exc)
                problems.append(
                    LayerProblem(
                        layer="",
                        problem=LayerProblemKind.UNREADABLE,
                        detail=f"your team's layer couldn't be read: {reason}",
                        fix="hz team show",
                    )
                )
        if declared is not None:
            stack = tree.stack(declared, source="your team")
        else:
            candidates = tree.teams() or [found for found in tree.layers if found.label is Label.COMPANY]
            if len(candidates) == 1:
                stack = tree.stack(candidates[0].name, source=f"the only {candidates[0].label} layer installed")
            else:
                stack = tree.stack(None)
            if len(candidates) > 1:
                names = ", ".join(c.name for c in candidates)
                problems.append(
                    LayerProblem(
                        layer="",
                        problem=LayerProblemKind.AMBIGUOUS,
                        detail=f"{len(candidates)} {candidates[0].label} layers are installed ({names}) and nothing "
                        "says which is yours",
                        fix=f"hz system config set team.layer {candidates[0].name}",
                    )
                )
        return replace(stack, problems=(*stack.problems, *problems)) if problems else stack

    def compiled_jobs(self) -> list[tuple[str, JobLet]]:
        """The jobs compiled from data, each with the module that compiled it; compiled afresh on every call.

        Data such as workflow contracts changes while a process runs (the foreground daemon loop), so nothing is
        cached; none when no enabled module provides `CompiledJobs`.

        Raises:
            Exception: Whatever the compiling module's factory raises; callers isolate it.
        """
        compiler = self._providers.get(CompiledJobs)
        if compiler is None:
            return []
        compiled = cast("CompiledJobs", self.call(self._factories[CompiledJobs]))
        return [(compiler, let) for let in compiled.jobs]

    def _compiled_requesters(self) -> list[Requester]:
        """Requesters of the compiled jobs, for the capability matrix; none while compiling fails."""
        try:
            jobs = self.compiled_jobs()
        except Exception:
            logger.warning("Compiling jobs failed; the capability matrix leaves them out", exc_info=True)
            return []
        return [Requester(module, "job", let.name, let.requires) for module, let in jobs]

    def call[R](self, func: Callable[..., R], /, **kwargs: Any) -> R:  # noqa: ANN401 - forwards arbitrary arguments
        """Call `func`, injecting every parameter the kernel provides that the caller didn't pass."""
        return func(**self._inject(func, kwargs))

    def invoke(self, operation: OperationLet, /, **kwargs: Any) -> Any:  # noqa: ANN401 - operations return anything
        """Run an operation for a head: inject dependencies and validate the arguments against their annotations.

        `Annotated[int, Field(ge=1)]` and the like are enforced here, so every head behaves the same.

        Raises:
            HeinzelError: If an argument is invalid, with a message naming it.
        """
        if operation.private:
            permission = PRIVATE_READ if operation.read_only else PRIVATE_WRITE
            if permission not in self.resolve(CurrentUser).context().permissions:
                msg = "This credential does not permit the requested private operation"
                raise HeinzelError(msg)
        func = operation.func
        signature = inspect.signature(func, eval_str=True)
        # What the kernel injects is its own and not validated; everything the caller passes is, before anything is
        # injected, so invalid input never builds a service or an adapter. Capability protocols aren't
        # runtime-checkable, so a caller passing one explicitly is validated as Any.
        public = [
            p.replace(annotation=Any) if capability_of(_injected_type(p.annotation)) else p
            for name, p in signature.parameters.items()
            if name in kwargs or not (self.provides(p.annotation) or _service_contract(p.annotation))
        ]

        def collect(**arguments: Any) -> dict[str, Any]:  # noqa: ANN401 - whatever the operation takes
            return arguments

        collect.__signature__ = signature.replace(parameters=public)  # type: ignore[attr-defined]  # pydantic reads it
        collect.__annotations__ = {p.name: p.annotation for p in public if p.annotation is not inspect.Parameter.empty}
        try:
            arguments = validate_call(config=ConfigDict(arbitrary_types_allowed=True))(collect)(**kwargs)
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'input'}: {e['msg']}" for e in exc.errors())
            msg = f"Invalid arguments for {operation.name}: {problems}"
            raise HeinzelError(msg) from exc
        return func(**self._inject(func, arguments))

    def _inject(self, func: Callable[..., Any], kwargs: dict[str, Any]) -> dict[str, Any]:
        for name, parameter in inspect.signature(func, eval_str=True).parameters.items():
            annotation = _injected_type(parameter.annotation)
            if name in kwargs:
                continue
            if annotation is Adapters:
                kwargs[name] = self._capabilities.handles(_adapters_capability(parameter.annotation), func)
            elif capability := capability_of(annotation):
                # A handle per requester: what it may call depends on what `func` declared (ADR 0028).
                kwargs[name] = self._capabilities.handle(capability, func)
            elif self.provides(annotation) or _service_contract(annotation):
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
        self._providers[kind] = record.qualname

    def _adapters(self) -> list[Adapter]:
        """Every adapter whose class serves its declared modes; two built-ins under one name are a bug.

        Two adapters with one name for one capability, one of them from a plugin, are both kept: binding to that name
        is refused as ambiguous, naming both distributions, instead of silently picking one.

        Raises:
            HeinzelError: If built-in adapters collide or a built-in adapter lacks methods of its modes.
        """
        adapters: list[Adapter] = []
        for record, let in sorted(self.lets(ProviderLet), key=_builtins_first):
            problem = None
            clash = [a for a in adapters if (a.let.capability, a.let.adapter) == (let.capability, let.adapter)]
            if record.origin.builtin and any(self._records[a.module].origin.builtin for a in clash):
                problem = f"another module already provides {let.capability} as {let.adapter!r}"
            elif missing := _missing_methods(let):
                problem = f"adapter {let.name} lacks {', '.join(missing)} of {let.capability}"
            elif foreign := self._foreign_settings(let, record):
                problem = f"the hosts of adapter {let.name} read settings {record.qualname} doesn't declare: {foreign}"
            if problem is None:
                adapters.append(Adapter(module=record.qualname, distribution=record.origin.distribution, let=let))
            elif record.origin.builtin:
                msg = f"Built-in module {record.qualname!r}: {problem}"
                raise HeinzelError(msg)
            else:
                logger.warning("Skipping an adapter of %r: %s", record.qualname, problem)
                self.errors[record.qualname] = f"Adapter skipped: {problem}"
        return adapters

    def _foreign_settings(self, let: ProviderLet, record: ModuleRecord) -> str:
        """Settings classes the adapter's hosts function reads that its own module doesn't declare, comma-separated."""
        if let.configured_hosts is None:
            return ""
        models = settings_parameters(let.configured_hosts).values()
        return ", ".join(m.__name__ for m in models if self._providers.get(m) != record.qualname)

    def _requesters(self) -> tuple[list[Requester], dict[Callable[..., Any], Requester]]:
        """Every requester, and every function that may ask for a capability with the requester it acts for."""
        requesters: list[Requester] = []
        owners: dict[Callable[..., Any], Requester] = {}
        for record in self._records.values():
            lets = record.module.lets
            own = tuple(let.requirement for let in lets if isinstance(let, RequirementLet))
            # A module that only provides adapters needs its requirements only while one of them serves someone.
            provided = tuple((let.capability, let.adapter) for let in lets if isinstance(let, ProviderLet))
            serves = provided if all(isinstance(let, ADAPTER_ONLY_LETS) for let in lets) else ()
            module = Requester(
                module=record.qualname, kind="module", name=record.module.name, requires=own, serves=serves
            )
            requesters.append(module)
            for let in lets:
                match let:
                    case OperationLet():
                        requester = Requester(
                            record.qualname,
                            "operation",
                            let.name,
                            let.requires,
                            own,
                            publishes=not let.read_only,
                            optional=let.optional,
                        )
                        requesters.append(requester)
                        self._own(owners, let.func, requester, record)
                    case JobLet():
                        requesters.append(requester := Requester(record.qualname, "job", let.name, let.requires, own))
                        self._own(owners, let.func, requester, record)
                    # Factories and providers act for their module; a factory two modules share is a service
                    # collision, reported where services are registered.
                    case ServiceLet() | ProviderLet():
                        owners.setdefault(let.factory, module)
                    case InfoLet() | HealthLet():
                        owners.setdefault(let.provider, module)
                    case SetupLet():
                        owners.setdefault(let.func, module)
                    case _:
                        pass
        return requesters, owners

    def _own(
        self,
        owners: dict[Callable[..., Any], Requester],
        func: Callable[..., Any],
        requester: Requester,
        record: ModuleRecord,
    ) -> None:
        """Record who an operation's or job's function acts for; registered twice, it would get the wrong grants.

        Raises:
            HeinzelError: If a built-in module registers one function for two requesters.
        """
        earlier = owners.setdefault(func, requester)
        if earlier == requester:
            return
        name = getattr(func, "__qualname__", repr(func))
        problem = f"{name} is registered for {earlier.label} and {requester.label}; a function acts for one only"
        if record.origin.builtin:
            msg = f"Built-in module {record.qualname!r}: {problem}"
            raise HeinzelError(msg)
        logger.warning("Module %r: %s", record.qualname, problem)
        self.errors[record.qualname] = f"Requirement skipped: {problem}"

    def _is_disabled(self, qualname: str) -> bool:
        parts = qualname.split(".")
        return any(".".join(parts[: i + 1]) in self.settings.disabled_modules for i in range(len(parts)))


def _adapters_capability(annotation: Any) -> Capability:  # noqa: ANN401 - an annotation
    """The capability of `Adapters[P]`: the one whose protocol is `P`.

    Raises:
        HeinzelError: If `P` is missing or is no capability's protocol.
    """
    if get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    args = get_args(annotation)
    capability = capability_of(args[0]) if args else None
    if capability is None:
        msg = (
            f"Ask for `Adapters[<protocol>]` with a capability's protocol, e.g. `Adapters[AiAccess]`, not {annotation}"
        )
        raise HeinzelError(msg)
    return capability


def _builtins_first(pair: tuple[ModuleRecord, Let]) -> tuple[bool, tuple[str, ...]]:
    return (not pair[0].origin.builtin, pair[0].path)


def _missing_methods(let: ProviderLet) -> list[str]:
    """Methods of its declared modes a class-based adapter doesn't define; a factory's result is checked when built."""
    if not isinstance(let.factory, type):
        return []
    methods = CAPABILITIES[let.capability].methods
    return [
        name for name, mode in methods.items() if mode in let.modes and not callable(getattr(let.factory, name, None))
    ]


def _field(key: str) -> str:
    return key.replace("-", "_")


def _provided_types(module: Module) -> set[type[Any]]:
    provided: set[type[Any]] = set()
    for let in module.lets:
        if isinstance(let, ServiceLet):
            provided.add(let.provides)
        elif isinstance(let, SettingsLet):
            provided.add(let.model)
    return provided


def _service_contract(kind: object) -> bool:
    kind = _injected_type(kind)
    return isinstance(kind, type) and issubclass(kind, ServiceContract)


def _injected_type(annotation: Any) -> Any:  # noqa: ANN401 - arbitrary inspected Python annotations
    """Ignore descriptive metadata while retaining it on the public user-input signature."""
    if get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    return get_origin(annotation) or annotation
