"""Module: the unit of functionality in Heinzelmännchen.

A module is a package whose `__init__.py` declares `module = Module(...)`. Packages below it that declare their own
`module` become its submodules. The kernel finds them on its own; nothing has to be registered by hand.
"""

import inspect
import re
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timedelta
from typing import Any, Literal, overload

from pydantic import BaseModel
from rich.console import RenderableType

from heinzel.kernel._capabilities import CAPABILITIES, MODES, Mode, Requirement, parse_requirements
from heinzel.kernel._config import ModuleSettings
from heinzel.kernel._duration import parse_duration
from heinzel.kernel._lets import (
    NAME_PATTERN,
    CatchUp,
    Check,
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
)
from heinzel.kernel._spec import hookimpl

RESERVED_NAMES = frozenset({"hz", "capabilities", "setup"})
"""Names a module can't take: the kernel's and the capability bindings' configuration tables, and `hz setup`."""
SETUP = "setup"
"""The command of a module's setup step (ADR 0032): no operation of a module with a step may take it."""
SERVICE_PATTERN = re.compile(r"^[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*)*$")


class Module:
    """Declares a module and collects the lets it advertises.

    Example:
        ```python
        module = Module("example", "Show how a Heinzelmännchen module is built.")


        @module.operation
        def hello(name: Annotated[str, Field(description="Who to greet.")] = "world") -> str:
            \"\"\"Greet someone.\"\"\"
            return f"Hello, {name}!"
        ```
    """

    def __init__(self, name: str, summary: str, *, essential: bool = False, requires: Iterable[str] = ()) -> None:
        """Declare a module.

        Args:
            name: Lowercase, dash-separated name; becomes the command group and the prefix of tool names.
            summary: One sentence on what the module is for.
            essential: Can't be disabled; for the few built-ins needed to repair a broken setup (`system`).
            requires: Capabilities the whole module reads (ADR 0028), e.g. `["tracker:read", "documents:read"]`;
                `write` is declared on the publish operation that writes, never here.

        Raises:
            ValueError: If the name is not a valid module name or is reserved, or a requirement is malformed or writes.
        """
        if not NAME_PATTERN.fullmatch(name):
            msg = f"Module name {name!r} must match {NAME_PATTERN.pattern}"
            raise ValueError(msg)
        if name in RESERVED_NAMES:
            what = "the command `hz setup`" if name == SETUP else f"`[{name}]`, a configuration table of the kernel"
            msg = f"Module name {name!r} is reserved: it is {what}"
            raise ValueError(msg)
        requirements = parse_requirements(requires)
        if writes := [str(r) for r in requirements if "write" in r.modes]:
            msg = (
                f"Module {name!r} can't require {', '.join(writes)}: declare write on the publish operation that "
                f"writes, `@module.operation(read_only=False, requires=[...])`"
            )
            raise ValueError(msg)
        self.name = name
        self.summary = summary
        self.essential = essential
        self._lets: list[Let] = []
        for requirement in requirements:
            self.add(RequirementLet(name=requirement.capability, requirement=requirement))

    @property
    def lets(self) -> tuple[Let, ...]:
        """Everything this module advertises, in declaration order."""
        return tuple(self._lets)

    def add(self, let: Let) -> None:
        """Advertise a let. The decorators below cover the common kinds.

        Raises:
            ValueError: If the module already advertises a let of the same kind and name.
        """
        if any(type(existing) is type(let) and existing.name == let.name for existing in self._lets):
            msg = f"Module {self.name!r} already has a {type(let).__name__} named {let.name!r}"
            raise ValueError(msg)
        if _setup_clash(let, self._lets):
            msg = f"Module {self.name!r} can't have both a setup step and an operation named {SETUP!r}"
            raise ValueError(msg)
        self._lets.append(let)

    @overload
    def operation[F: Callable[..., Any]](self, func: F, /) -> F: ...

    @overload
    def operation[F: Callable[..., Any]](
        self,
        /,
        *,
        name: str | None = None,
        read_only: bool = True,
        render: Callable[[Any], RenderableType] | None = None,
        exit_code: Callable[[Any], int] | None = None,
        heads: Iterable[str] | None = None,
        requires: Iterable[str] = (),
    ) -> Callable[[F], F]: ...

    def operation[F: Callable[..., Any]](  # noqa: PLR0913 - keyword-only options of a decorator
        self,
        func: F | None = None,
        /,
        *,
        name: str | None = None,
        read_only: bool = True,
        render: Callable[[Any], RenderableType] | None = None,
        exit_code: Callable[[Any], int] | None = None,
        heads: Iterable[str] | None = None,
        requires: Iterable[str] = (),
    ) -> F | Callable[[F], F]:
        """Advertise a function as an operation. Its docstring is required: it becomes the help text.

        Args:
            func: The function, when used as a bare decorator.
            name: Operation name; defaults to the function name with underscores as dashes.
            read_only: False if the operation changes state; heads use this for confirmations and HTTP verbs.
            render: How human-facing heads present the result; the JSON form is the default.
            exit_code: Maps the result to an exit code for heads that have one; 0 when not given.
            heads: Restrict the operation to these heads, e.g. `{"cli"}` for interactive ones; all heads by default.
            requires: Capabilities it asks for by type besides its module's (ADR 0028), e.g. `["tracker:read"]`;
                `write` modes only with `read_only=False`.

        Raises:
            ValueError: If the docstring is missing, or a requirement is malformed or writes from a read-only operation.
        """
        requirements = self._requirements(requires)

        def decorate(target: F) -> F:
            summary = inspect.getdoc(target)
            if not summary:
                msg = f"Operation {target.__qualname__} needs a docstring; it is the help text in every head."
                raise ValueError(msg)
            self.add(
                OperationLet(
                    name=name or target.__name__.strip("_").replace("_", "-"),
                    func=target,
                    summary=summary.splitlines()[0],
                    read_only=read_only,
                    render=render,
                    exit_code=exit_code,
                    heads=frozenset(heads) if heads is not None else None,
                    requires=requirements,
                )
            )
            return target

        return decorate(func) if func is not None else decorate

    def job[F: Callable[..., Any]](  # noqa: PLR0913 - keyword-only options of a decorator
        self,
        *,
        schedule: str,
        name: str | None = None,
        scope: Literal["personal"] = "personal",
        timeout: str | timedelta = "15m",
        retries: int = 0,
        catch_up: CatchUp | Literal["latest", "all", "skip"] = CatchUp.LATEST,
        idempotency: Callable[[datetime], str] | None = None,
        requires: Iterable[str] = (),
    ) -> Callable[[F], F]:
        """Advertise a function as a job the daemon runs on a schedule (ADR 0020). Its docstring is required.

        The function asks for services by type, like an operation, returns its result and never prints.

        Args:
            schedule: Five-field cron expression in local time, e.g. `0 9 * * MON`.
            name: Job name; defaults to the function name with underscores as dashes.
            scope: Who runs it; only `personal` (the owner's daemon) for now.
            timeout: How long one run may take, e.g. `15m`.
            retries: How often a failing run is retried within the same tick.
            catch_up: Missed slots: `latest` runs once, `all` once per slot, `skip` none.
            idempotency: Key of the run for a slot; `<job>@<slot>` by default.
            requires: Capabilities it asks for by type besides its module's (ADR 0028); read modes only.

        Raises:
            ValueError: If the docstring is missing, the name, schedule, timeout or retries are invalid, or a
                requirement is malformed or writes.
        """
        duration = parse_duration(timeout)
        requirements = self._requirements(requires)

        def decorate(target: F) -> F:
            summary = inspect.getdoc(target)
            if not summary:
                msg = f"Job {target.__qualname__} needs a docstring; it describes the job wherever it is listed."
                raise ValueError(msg)
            self.add(
                JobLet(
                    name=name or target.__name__.strip("_").replace("_", "-"),
                    func=target,
                    summary=summary.splitlines()[0],
                    schedule=schedule,
                    scope=scope,
                    timeout=duration,
                    retries=retries,
                    catch_up=CatchUp(catch_up),
                    idempotency=idempotency,
                    requires=requirements,
                )
            )
            return target

        return decorate

    def service[T](self, provides: type[T], factory: Callable[..., T] | None = None) -> None:
        """Advertise a service the kernel injects wherever `provides` is asked for.

        Args:
            provides: The type operations ask for.
            factory: Builds the instance; its own parameters are injected. Defaults to `provides` itself.
        """
        self.add(ServiceLet(name=provides.__name__, provides=provides, factory=factory or provides))

    def _requirements(self, requires: Iterable[str]) -> tuple[Requirement, ...]:
        """Parse a function's requirements; a pin must agree with the module's pin for the same capability.

        Raises:
            ValueError: If a requirement is malformed, or pins another adapter than the module does.
        """
        requirements = parse_requirements(requires)
        pins = {let.name: let.requirement.adapter for let in self._lets if isinstance(let, RequirementLet)}
        for requirement in requirements:
            if requirement.capability in pins and pins[requirement.capability] != requirement.adapter:
                module_pin = pins[requirement.capability] or "none"
                msg = (
                    f"Requirement {requirement} conflicts with the pin of module {self.name!r} for "
                    f"{requirement.capability} ({module_pin}): one adapter per capability"
                )
                raise ValueError(msg)
        return requirements

    def provides[F: Callable[..., Any]](
        self, capability: str, *, modes: Iterable[Mode], hosts: Iterable[str] = (), name: str | None = None
    ) -> Callable[[F], F]:
        """Advertise a class (or factory) as an adapter of a capability (ADR 0028); usable as a class decorator.

        The kernel builds one instance, injecting the factory's parameters (settings, `Credentials`), and hands
        requesters a handle limited to the modes they declared and this adapter serves.

        Args:
            capability: The capability implemented, e.g. `tracker`; the factory's instances implement its protocol.
            modes: The modes served: `{"read"}` or `{"read", "write"}`.
            hosts: Hosts the adapter sends data to, shown by `hz system info`; leave empty only when configuration
                decides.
            name: Name bindings use (`[capabilities] tracker = "<name>"`); defaults to the module name.

        Raises:
            ValueError: If the capability, a mode or the name is unknown or malformed.
        """
        spec = CAPABILITIES.get(capability)
        if spec is None:
            msg = f"Unknown capability {capability!r}; known: {', '.join(sorted(CAPABILITIES))}"
            raise ValueError(msg)
        served = frozenset(modes)
        if not served or not served <= spec.modes or not served <= set(MODES):
            msg = f"Adapter of {capability} must serve some of {sorted(spec.modes)}, not {sorted(served)}"
            raise ValueError(msg)
        adapter = name or self.name
        if not NAME_PATTERN.fullmatch(adapter):
            msg = f"Adapter name {adapter!r} must match {NAME_PATTERN.pattern}"
            raise ValueError(msg)

        def decorate(factory: F) -> F:
            self.add(
                ProviderLet(
                    name=f"{adapter}:{capability}",
                    adapter=adapter,
                    capability=capability,
                    modes=served,
                    hosts=tuple(hosts),
                    factory=factory,
                )
            )
            return factory

        return decorate

    def settings[S: ModuleSettings](self, model: type[S]) -> type[S]:
        """Declare the module's settings; usable as a class decorator.

        Values come from the module's table in the configuration files (`[example]`, `[system.changelog]`) and from
        `HZ_<MODULE>__<FIELD>` environment variables. Operations and services ask for `model` to get them.
        """
        self.add(SettingsLet(name=model.__name__, model=model))
        return model

    def info[F: Callable[..., dict[str, Any] | BaseModel]](self, func: F) -> F:
        """Advertise a section for `hz system info`."""
        self.add(InfoLet(name=self.name, provider=func))
        return func

    def health[F: Callable[..., list[Check]]](self, func: F) -> F:
        """Advertise health checks for `hz system doctor`."""
        self.add(HealthLet(name=self.name, provider=func))
        return func

    def credential(self, name: str, *, purpose: str, service: str | None = None) -> CredentialLet:
        """Declare a secret the module needs; pass the returned let to `Credentials.get` to read its value.

        Args:
            name: Lowercase, dash-separated, e.g. `api-token`.
            purpose: What it is for and where to get one, e.g. "GitHub token with repo scope, from github.com/settings".
            service: Keep it under another service to share it between modules, a qualified module name such as
                `github` or `system.credential`; defaults to this module's.

        Raises:
            ValueError: If the name or service is malformed, or the purpose is empty.
        """
        if not NAME_PATTERN.fullmatch(name):
            msg = f"Credential name {name!r} must match {NAME_PATTERN.pattern}"
            raise ValueError(msg)
        if service is not None and not SERVICE_PATTERN.fullmatch(service):
            msg = f"Credential service {service!r} must be a qualified module name matching {SERVICE_PATTERN.pattern}"
            raise ValueError(msg)
        if not purpose.strip():
            msg = f"Credential {name!r} needs a purpose; people see it when it is missing"
            raise ValueError(msg)
        let = CredentialLet(name=name, purpose=purpose, service=service)
        self.add(let)
        return let

    @overload
    def setup[F: Callable[..., Any]](self, func: F, /) -> F: ...

    @overload
    def setup[F: Callable[..., Any]](self, /, *, required: bool = False) -> Callable[[F], F]: ...

    def setup[F: Callable[..., Any]](self, func: F | None = None, /, *, required: bool = False) -> F | Callable[[F], F]:
        """Advertise the module's interactive setup step (ADR 0032): `hz <module> setup`, and part of `hz setup`.

        The function asks for `Prompter` and whatever else it needs by type, like an operation, and writes through
        `ConfigEditor` and `Credentials`. Its docstring is required: the first line introduces the step.

        Args:
            func: The function, when used as a bare decorator.
            required: `hz setup` runs it without offering to skip it; optional steps can be skipped.

        Raises:
            ValueError: If the docstring is missing, the module already has a step, or an operation named `setup`.
        """

        def decorate(target: F) -> F:
            summary = inspect.getdoc(target)
            if not summary:
                msg = f"Setup step {target.__qualname__} needs a docstring; it introduces the step."
                raise ValueError(msg)
            self.add(SetupLet(name=self.name, func=target, summary=summary.splitlines()[0], required=required))
            return target

        return decorate(func) if func is not None else decorate

    @hookimpl
    def heinzel_lets(self) -> Sequence[Let]:
        """Hand the lets to the kernel."""
        return self.lets

    def __repr__(self) -> str:
        """Show the name and what the module advertises."""
        return f"Module({self.name!r}, lets={[let.name for let in self._lets]})"


def _setup_clash(let: Let, lets: Iterable[Let]) -> bool:
    """Whether `let` and one of `lets` are a setup step and an operation named `setup`: both would be the command."""

    def setup_operation(candidate: Let) -> bool:
        return isinstance(candidate, OperationLet) and candidate.name == SETUP

    if isinstance(let, SetupLet):
        return any(map(setup_operation, lets))
    return setup_operation(let) and any(isinstance(existing, SetupLet) for existing in lets)
