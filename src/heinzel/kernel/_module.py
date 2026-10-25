"""Module: the unit of functionality in Heinzelmännchen.

A module is a package whose `__init__.py` declares `module = Module(...)`. Packages below it that declare their own
`module` become its submodules. The kernel finds them on its own; nothing has to be registered by hand.
"""

import inspect
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, overload

from pydantic import BaseModel
from rich.console import RenderableType

from heinzel.kernel._capabilities import CAPABILITIES, MODES, NO_ADAPTER, Mode, Requirement, parse_requirements
from heinzel.kernel._config import ConfigEditor, ModuleSettings
from heinzel.kernel._contributions import (
    MAX_PER_MODULE,
    ORDER_DEFAULT,
    ActionLet,
    ContributionLet,
    Icon,
    Navigate,
    OpenUrl,
    RunCommand,
    RunOperation,
    Slot,
    StatusLet,
    View,
    WidgetLet,
)
from heinzel.kernel._duration import parse_duration
from heinzel.kernel._layers import LayerLet
from heinzel.kernel._lets import (
    NAME_PATTERN,
    SETUP_PHASES,
    Audience,
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
    SetupPhase,
    host_of,
)
from heinzel.kernel._playbooks import AssetType, ContractTemplate, PlaybookLet, PlaybookSetup, templates_in
from heinzel.kernel._prompter import Prompter
from heinzel.kernel._soundscapes import Soundscape, SoundscapeLet, sourced_soundscapes
from heinzel.kernel._spec import hookimpl
from heinzel.kernel._theme_signals import SignalLet, ThemeSignals
from heinzel.kernel._themes import Theme, ThemeLet, sourced_themes

RESERVED_NAMES = frozenset({"hz", "capabilities", "setup", "join"})
"""Names a module can't take: the kernel's and the capability bindings' tables, `hz setup` and `hz join`."""
SETUP = "setup"
"""The top-level command that runs modules' setup steps (ADR 0032), so no module may take its name."""
COMMANDS = frozenset({SETUP, "join"})
"""Reserved names that are top-level commands of the CLI head."""
SERVICE_PATTERN = re.compile(r"^[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*)*$")


def _named(name: str | None, view: Callable[..., Any]) -> str:
    return name or getattr(view, "__name__", "view").strip("_").replace("_", "-")


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

    def __init__(
        self,
        name: str,
        summary: str,
        *,
        essential: bool = False,
        requires: Iterable[str] = (),
        renamed_from: str | None = None,
    ) -> None:
        """Declare a module.

        Args:
            name: Lowercase, dash-separated name; becomes the command group and the prefix of tool names.
            summary: One sentence on what the module is for.
            essential: Can't be disabled; for the few built-ins needed to repair a broken setup (`system`).
            requires: Capabilities the whole module reads (ADR 0028), e.g. `["tracker:read", "documents:read"]`;
                `write` is declared on the publish operation that writes, never here.
            renamed_from: The module's old name, for the one minor release both work (ADR 0051): its settings table
                and `HZ_<OLD>__*` variables fill in what the new ones don't set, and the daemon runs a job asked for
                by its old id. Old command paths are each operation's `aliases`.

        Raises:
            ValueError: If the name or the old name is not a valid module name or is reserved, or a requirement is
                malformed or writes.
        """
        if not NAME_PATTERN.fullmatch(name):
            msg = f"Module name {name!r} must match {NAME_PATTERN.pattern}"
            raise ValueError(msg)
        if name in RESERVED_NAMES:
            what = f"the command `hz {name}`" if name in COMMANDS else f"`[{name}]`, a kernel configuration table"
            msg = f"Module name {name!r} is reserved: it is {what}"
            raise ValueError(msg)
        if renamed_from is not None and (not NAME_PATTERN.fullmatch(renamed_from) or renamed_from in RESERVED_NAMES):
            msg = f"Module {name!r} can't be renamed from {renamed_from!r}: not a module name a module could have had"
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
        self.renamed_from = renamed_from
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
        optional: bool = False,
        audience: Audience = "expert",
        aliases: Iterable[str] = (),
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
        optional: bool = False,
        audience: Audience = "expert",
        aliases: Iterable[str] = (),
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
            optional: True if it does without its own `requires` when nothing serves them, asking
                `CapabilityView.serves` first: an unmet one is then no doctor finding, and its handle refuses only
                when called. Read-only operations only.
            audience: `everyday` to list it in help, completion and the web app's navigation for everyone; `expert`
                (the default) lists it in expert mode only. It runs either way (ADR 0041).
            aliases: Old command paths that still run it for one minor release, e.g. `["workflow run"]`: the CLI
                hides them and names the new command on stderr, MCP keeps the old tool name marked deprecated
                (ADR 0051).

        Raises:
            ValueError: If the docstring is missing, the audience unknown, a requirement is malformed or writes from
                a read-only operation, a publish operation is optional, or an alias isn't a command path.
        """
        requirements = self._requirements(requires)
        old_paths = tuple(aliases)
        if bad := [a for a in old_paths if not a.split() or not all(NAME_PATTERN.fullmatch(w) for w in a.split())]:
            msg = f"Operation aliases must be command paths of lowercase words, e.g. 'workflow run'; got {bad!r}"
            raise ValueError(msg)

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
                    optional=optional,
                    audience=audience,
                    aliases=tuple(" ".join(a.split()) for a in old_paths),
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
        self,
        capability: str,
        *,
        modes: Iterable[Mode],
        hosts: Iterable[str] | Callable[..., Iterable[str]] = (),
        name: str | None = None,
    ) -> Callable[[F], F]:
        """Advertise a class (or factory) as an adapter of a capability (ADR 0028); usable as a class decorator.

        The kernel builds one instance, injecting the factory's parameters (settings, `Credentials`), and hands
        requesters a handle limited to the modes they declared and this adapter serves.

        Args:
            capability: The capability implemented, e.g. `tracker`; the factory's instances implement its protocol.
            modes: The modes served: `{"read"}` or `{"read", "write"}`.
            hosts: Hosts the adapter sends data to, shown by `hz system capability list`, `hz system info` and doctor:
                host names (a URL is reduced to its host), or, when its settings decide them, a function that takes
                this module's settings by type and returns host names or URLs, such as
                `def hosts(settings: AcmeSettings) -> list[str]: return [settings.api_url]`. It is called when the
                matrix is built, gets nothing but settings, and must not do I/O; when it fails or returns no valid
                host, the hosts show as `unknown (configured)` and doctor says why. List a setting that decides
                hosts in `ignored_in_project`, so a project `hz.toml` can't redirect data.
            name: Name bindings use (`[capabilities] tracker = "<name>"`); defaults to the module name.

        Raises:
            ValueError: If the capability, a mode or the name is unknown or malformed, a host is not a host name or
                URL, or the hosts function takes anything but settings.
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
        if adapter == NO_ADAPTER:
            msg = (
                f"Adapter name {NO_ADAPTER!r} is reserved: `[capabilities] <capability> = {NO_ADAPTER!r}` binds nothing"
            )
            raise ValueError(msg)
        configured = hosts if callable(hosts) else None
        static = () if callable(hosts) else tuple(host_of(host) or host for host in hosts)

        def decorate(factory: F) -> F:
            self.add(
                ProviderLet(
                    name=f"{adapter}:{capability}",
                    adapter=adapter,
                    capability=capability,
                    modes=served,
                    hosts=static,
                    factory=factory,
                    configured_hosts=configured,
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

    @overload
    def health[F: Callable[..., list[Check]]](self, func: F, /) -> F: ...

    @overload
    def health[F: Callable[..., list[Check]]](self, /, *, live: bool = False) -> Callable[[F], F]: ...

    def health[F: Callable[..., list[Check]]](
        self, func: F | None = None, /, *, live: bool = False
    ) -> F | Callable[[F], F]:
        """Advertise health checks: offline ones for `hz system doctor`, or with `live=True` a connection test.

        A live provider may reach the network, with a short timeout of its own, and makes one cheap read-only call,
        such as asking the vendor's CLI who it is signed in as; it runs only on demand (`hz system connector test`),
        never in doctor, which stays offline (ADR 0035, ADR 0055). A module has at most one of each.

        Args:
            func: The provider, when used as a bare decorator.
            live: Whether it tests the connection rather than checking local state.
        """

        def decorate(target: F) -> F:
            self.add(HealthLet(name=f"{self.name} live" if live else self.name, provider=target, live=live))
            return target

        return decorate if func is None else decorate(func)

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
    def setup[F: Callable[..., Any]](
        self, /, *, required: bool = False, phase: SetupPhase = "connect"
    ) -> Callable[[F], F]: ...

    def setup[F: Callable[..., Any]](
        self, func: F | None = None, /, *, required: bool = False, phase: SetupPhase = "connect"
    ) -> F | Callable[[F], F]:
        """Advertise the module's interactive setup step (ADR 0032): `hz setup <module>`, and part of `hz setup`.

        The function asks for `Prompter` and whatever else it needs by type, like an operation, and writes through
        `ConfigEditor` and `Credentials`. Its docstring is required: the first line introduces the step.

        Args:
            func: The function, when used as a bare decorator.
            required: `hz setup` runs it without offering to skip it; optional steps can be skipped.
            phase: Where in `hz setup` it runs: `identity`, `membership`, `connect` (the default: tools, credentials,
                bindings), `work` or `run` (ADR 0032, amended).

        Raises:
            ValueError: If the docstring is missing, the phase unknown, or the module already has a step.
        """
        if phase not in SETUP_PHASES:
            msg = f"Setup phase {phase!r} is not one of {', '.join(SETUP_PHASES)}"
            raise ValueError(msg)

        def decorate(target: F) -> F:
            summary = inspect.getdoc(target)
            if not summary:
                msg = f"Setup step {target.__qualname__} needs a docstring; it introduces the step."
                raise ValueError(msg)
            self.add(
                SetupLet(name=self.name, func=target, summary=summary.splitlines()[0], required=required, phase=phase)
            )
            return target

        return decorate(func) if func is not None else decorate

    def playbook(
        self,
        summary: str,
        *,
        assets: Iterable[AssetType] = (),
        templates: Iterable[Path | ContractTemplate] = (),
        defaults: Mapping[str, Any] | None = None,
        setup: bool = True,
    ) -> PlaybookLet:
        """Advertise the module's playbook (ADR 0037): asset types, template contracts and recommended settings.

        Data only, one per module: `hz heinzel playbooks` lists it, `hz heinzel discover` evaluates its hints
        through read-only capability handles, and `hz heinzel new --template <module>:<name>` writes its templates.
        Unless `setup` is False, it also declares the module's setup step (ADR 0032): `hz setup <module>` and
        `hz setup` propose the recommended settings, the assets the hints find and the templates, and apply only what
        the person accepts. A module with its own setup step passes `setup=False` and asks for `PlaybookSetup` in it.

        Args:
            summary: What the playbook is for, in one sentence.
            assets: The asset types it knows, with discovery hints.
            templates: Template contracts: `ContractTemplate`s, `*.prose.md` files or directories of them, typically
                `Path(__file__).parent / "templates"`; read now, at import.
            defaults: Recommended settings by `<section>.<key>`, e.g. `{"capabilities.tracker": "github"}`.
            setup: Declare the module's setup step that walks through the playbook.

        Raises:
            ValueError: If the playbook is malformed or the module already has one.
            OSError: If a template file can't be read.
        """
        if setup and any(isinstance(existing, SetupLet) for existing in self._lets):
            msg = (
                f"Module {self.name!r} already has a setup step; pass module.playbook(..., setup=False) and ask "
                "for heinzel.kernel.PlaybookSetup in it"
            )
            raise ValueError(msg)
        let = PlaybookLet(
            name=self.name,
            summary=summary,
            assets=tuple(assets),
            templates=templates_in(templates),
            defaults=dict(defaults or {}),
        )
        self.add(let)
        if setup:

            def walk(playbooks: PlaybookSetup, prompter: Prompter, editor: ConfigEditor) -> None:
                playbooks.run(let, prompter, editor)

            walk.__doc__ = f"Playbook: {let.summary}"
            walk.__qualname__ = f"{self.name}.playbook_setup"
            self.setup(walk)
        return let

    def layer(self, *, parent: str | None = None, site: str | None = None, roles: Iterable[str] = ()) -> LayerLet:
        """Declare that this module's plugin is a layer of a company's tree (ADR 0040); once per module.

        Layers are untyped: a layer without a parent is the root (the company), and company, org, site and team are
        labels hz derives from where a layer sits. Name a layer by its path from the root, joined with dashes
        (`acme`, `acme-payments`, `acme-payments-checkout`, `acme-lisbon`), so its name says where it sits; doctor
        hints at a name that doesn't. List the parent's and the site's plugins as dependencies of this one, so
        installing a team installs its stack.

        Example:
            ```python
            module = Module("acme-payments-checkout", "The Checkout team of Acme Payments.")
            module.layer(parent="acme-payments", site="acme-lisbon", roles=["checkout-captain"])
            ```

        Args:
            parent: The module name of the layer above, e.g. `acme-payments`; none for the root.
            site: The module name of a site layer this layer is at, e.g. `acme-lisbon`; a site sits beside the org
                tree, under the company, and is never one of this layer's parents.
            roles: Roles this layer adds to the core vocabulary for everyone whose stack includes it.

        Raises:
            ValueError: If the module is a layer already (a layer has one parent), a name is malformed, or the layer
                is its own parent or site, or its parent is its site.
        """
        if any(isinstance(existing, LayerLet) for existing in self._lets):
            msg = f"Module {self.name!r} is a layer already; a layer has one parent and at most one site"
            raise ValueError(msg)
        let = LayerLet(name=self.name, parent=parent, site=site, roles=tuple(roles))
        self.add(let)
        return let

    def theme(self, source: Path | Theme) -> tuple[ThemeLet, ...]:
        """Offer themes for the web app (ADR 0044): a `Theme`, a `*.toml` file of one, or a directory of them.

        Data only, read and checked now, at import: `hz system theme list` lists them and `hz system theme use <name>`
        picks one. Two modules offering a theme of the same name: the more specific layer of the person's stack wins
        (ADR 0040), then a layer in the stack over a module outside it, then the first module by name. A theme file's
        `[[fonts]]` are `.woff2` files relative to its directory, checked now too.

        Example:
            ```python
            module.theme(Path(__file__).parent / "themes")
            ```

        Raises:
            ValueError: If a theme is malformed, is called `default` (the built-in one), a font file is missing,
                outside the theme's directory, no woff2 or over 2 MB, or the module already offers a theme of that name.
            OSError: If a theme file can't be read.
        """
        lets: list[ThemeLet] = []
        for theme, directory in sourced_themes(source):
            let = ThemeLet(name=theme.name, theme=theme, directory=directory)
            self.add(let)
            lets.append(let)
        return tuple(lets)

    def soundscape(self, source: Path | Soundscape) -> tuple[SoundscapeLet, ...]:
        """Offer soundscapes for the web app (ADR 0058): a `Soundscape`, a `*.toml` file of one, or a directory of them.

        Data only, read and checked now, at import: `hz system sound list` lists them and `hz system sound use <name>`
        picks one. A layered soundscape's audio files are relative to its file's directory and checked now (path,
        size, licence, signature); a stream names a provider of `STREAM_PROVIDERS` and a video, never a URL. Two
        modules offering one name: the more specific layer wins, as with themes (ADR 0044).

        Example:
            ```python
            module.soundscape(Path(__file__).parent / "soundscapes")
            ```

        Raises:
            ValueError: If a soundscape is malformed, is called `off`, an audio file doesn't pass its checks, or the
                module already offers a soundscape of that name.
            OSError: If a soundscape file can't be read.
        """
        lets: list[SoundscapeLet] = []
        for soundscape, directory in sourced_soundscapes(source):
            let = SoundscapeLet(name=soundscape.name, soundscape=soundscape, directory=directory)
            self.add(let)
            lets.append(let)
        return tuple(lets)

    def signals[F: Callable[..., ThemeSignals]](
        self, *, every: str | timedelta, name: str | None = None
    ) -> Callable[[F], F]:
        """Publish signals themes follow (ADR 0060): a read-only operation returning `ThemeSignals`, read every `every`.

        `system.theme` calls it at most once per interval and hands its values to the web app, whose theme picks the
        variant they match; it is an ordinary operation too, so `hz <module> <name>` shows what it publishes. Keep it
        cheap and quick: it runs while the web app asks for the signals, and a failure only leaves them unknown.

        Example:
            ```python
            @module.signals(every="15m")
            def now(service: WeatherService) -> ThemeSignals:
                \"\"\"The weather now, for themes.\"\"\"
                return ThemeSignals(weather=service.now().kind, sunrise=..., sunset=...)
            ```

        Args:
            every: How long its values hold, 1m to 1d, e.g. `15m`.
            name: The operation's name; defaults to the function name with underscores as dashes.

        Raises:
            ValueError: If the interval is malformed or out of range, or the function has no docstring.
        """
        interval = parse_duration(every)

        def decorate(target: F) -> F:
            self.operation(name=name)(target)
            self.add(SignalLet(name=name or target.__name__.strip("_").replace("_", "-"), func=target, every=interval))
            return target

        return decorate

    def status[F: View](
        self,
        *,
        source: str,
        name: str | None = None,
        order: int = ORDER_DEFAULT,
        refresh: str | timedelta = "60s",
        slots: Mapping[Slot, bool] | None = None,
    ) -> Callable[[F], F]:
        """Add an item to the web app's status bar (ADR 0056): a pure view over one of this module's operations.

        `system.ui` runs `source`, a read-only operation of this module that takes no required argument, as a head
        would, and calls the view with its result; the view returns a `StatusItem`, or None to show nothing. The view
        takes nothing injected and does no I/O: what it needs comes from the result.

        Example:
            ```python
            @module.status(source="clock")
            def primary_zone(times: list[ZoneTime]) -> StatusItem | None:
                return StatusItem(text=times[0].zone, live=LiveClock(zone=times[0].zone)) if times else None
            ```

        Args:
            source: The operation's name in this module, e.g. `clock`.
            name: The item's name; defaults to the view's name with underscores as dashes.
            order: Where it sits, 0 to 1000, lower first; 500 by default.
            refresh: How often the app asks again, 15s to 1d, e.g. `60s` or `5m`.
            slots: Where it appears and whether by default: `footer` (the status bar) and `sidebar` (the bottom of the
                navigation); `{"footer": True}` by default. The person turns each on or off.

        Raises:
            ValueError: If the name, source, order, refresh or a slot is invalid, or the module offers three items
                already.
        """
        interval = parse_duration(refresh)

        def decorate(view: F) -> F:
            let = StatusLet(
                name=_named(name, view),
                source=source,
                view=view,
                order=order,
                refresh=interval,
                slots=dict(slots or {"footer": True}),
            )
            self._contribute("status", let)
            return view

        return decorate

    def widget[F: View](
        self, *, source: str, name: str | None = None, order: int = ORDER_DEFAULT, refresh: str | timedelta = "5m"
    ) -> Callable[[F], F]:
        """Add a widget to the web app's Today screen (ADR 0056): a pure view over one of this module's operations.

        Like `status`, but the view returns a `Card`, `ListWidget`, `Metric` or `Progress`, or None.

        Args:
            source: The operation's name in this module, e.g. `clock`.
            name: The widget's name; defaults to the view's name with underscores as dashes.
            order: Where it sits, 0 to 1000, lower first; 500 by default.
            refresh: How often the app asks again, 60s to 1d; `5m` by default.

        Raises:
            ValueError: If the name, source, order or refresh is invalid, or the module offers four widgets already.
        """
        interval = parse_duration(refresh)

        def decorate(view: F) -> F:
            let = WidgetLet(
                name=_named(name, view), source=source, view=view, order=order, refresh=interval, slots={"today": True}
            )
            self._contribute("widget", let)
            return view

        return decorate

    def action(  # noqa: PLR0913 - keyword-only data of one action
        self,
        name: str,
        *,
        label: str,
        icon: Icon,
        effect: Navigate | OpenUrl | RunOperation | RunCommand,
        order: int = ORDER_DEFAULT,
        source: str | None = None,
        badge: View | None = None,
        refresh: str | timedelta = "60s",
    ) -> ActionLet:
        """Add a button to the web app's header (ADR 0056): a label, an icon and one effect, with an optional badge.

        Example:
            ```python
            module.action("team-clock", label="Team clock", icon="clock", effect=RunCommand(command="hz clock"))
            ```

        Args:
            name: Lowercase and dashed, e.g. `team-clock`.
            label: Its accessible name and tooltip.
            icon: One of `Icon`.
            effect: What a click does: `Navigate`, `OpenUrl`, `RunOperation` (an operation of this module; one that
                writes runs only after the person confirms the exact call) or `RunCommand`.
            order: Where it sits, 0 to 1000, lower first.
            source: A read-only operation of this module whose result `badge` views.
            badge: A pure function of `source`'s result returning a count, up to four characters, or None.
            refresh: How often the app asks for the badge again, 15s to 1d.

        Raises:
            ValueError: If a value is invalid, only one of source and badge is given, or the module offers two actions
                already.
        """
        let = ActionLet(
            name=name,
            label=label,
            icon=icon,
            effect=effect,
            order=order,
            source=source,
            badge=badge,
            refresh=parse_duration(refresh),
            slots={"header": True},
        )
        self._contribute("action", let)
        return let

    def _contribute(self, kind: str, let: ContributionLet) -> None:
        limit = MAX_PER_MODULE[kind]
        if sum(isinstance(existing, type(let)) for existing in self._lets) >= limit:
            msg = f"Module {self.name!r} offers {limit} {kind} contributions already, the most one module may"
            raise ValueError(msg)
        self.add(let)

    @hookimpl
    def heinzel_lets(self) -> Sequence[Let]:
        """Hand the lets to the kernel."""
        return self.lets

    def __repr__(self) -> str:
        """Show the name and what the module advertises."""
        return f"Module({self.name!r}, lets={[let.name for let in self._lets]})"
