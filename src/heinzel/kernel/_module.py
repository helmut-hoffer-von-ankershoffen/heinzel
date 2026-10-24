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

from heinzel.kernel._config import ModuleSettings
from heinzel.kernel._duration import parse_duration
from heinzel.kernel._lets import (
    CatchUp,
    Check,
    HealthLet,
    InfoLet,
    JobLet,
    Let,
    OperationLet,
    ServiceLet,
    SettingsLet,
)
from heinzel.kernel._spec import hookimpl

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")


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

    def __init__(self, name: str, summary: str, *, essential: bool = False) -> None:
        """Declare a module.

        Args:
            name: Lowercase, dash-separated name; becomes the command group and the prefix of tool names.
            summary: One sentence on what the module is for.
            essential: Can't be disabled; for the few built-ins needed to repair a broken setup (`system`).

        Raises:
            ValueError: If the name is not a valid module name.
        """
        if not NAME_PATTERN.fullmatch(name):
            msg = f"Module name {name!r} must match {NAME_PATTERN.pattern}"
            raise ValueError(msg)
        self.name = name
        self.summary = summary
        self.essential = essential
        self._lets: list[Let] = []

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
    ) -> F | Callable[[F], F]:
        """Advertise a function as an operation. Its docstring is required: it becomes the help text.

        Args:
            func: The function, when used as a bare decorator.
            name: Operation name; defaults to the function name with underscores as dashes.
            read_only: False if the operation changes state; heads use this for confirmations and HTTP verbs.
            render: How human-facing heads present the result; the JSON form is the default.
            exit_code: Maps the result to an exit code for heads that have one; 0 when not given.
            heads: Restrict the operation to these heads, e.g. `{"cli"}` for interactive ones; all heads by default.
        """

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

        Raises:
            ValueError: If the docstring is missing, the timeout is malformed or retries is negative.
        """
        if retries < 0:
            msg = f"retries must be 0 or more, not {retries}"
            raise ValueError(msg)
        duration = parse_duration(timeout)

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

    @hookimpl
    def heinzel_lets(self) -> Sequence[Let]:
        """Hand the lets to the kernel."""
        return self.lets

    def __repr__(self) -> str:
        """Show the name and what the module advertises."""
        return f"Module({self.name!r}, lets={[let.name for let in self._lets]})"
