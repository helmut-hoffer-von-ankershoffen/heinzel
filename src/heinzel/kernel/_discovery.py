"""Discovery: find built-in modules and plugin modules, without any registration by hand.

Built-in modules live in `heinzel.modules`. A plugin is a distribution with an entry point in the `heinzel.modules`
group that points at its root package. In both cases the kernel walks the package tree: every package declaring
`module = Module(...)` is a module, and the nearest declaring ancestor is its parent.
"""

import importlib
import logging
import pkgutil
from collections.abc import Iterator
from importlib.metadata import entry_points
from types import ModuleType

from heinzel import __version__
from heinzel.kernel._kernel import ModuleRecord, Origin
from heinzel.kernel._module import Module

BUILTIN_PACKAGE = "heinzel.modules"
ENTRY_POINT_GROUP = "heinzel.modules"

logger = logging.getLogger(__name__)


def discover(*, plugins: bool = True) -> tuple[list[ModuleRecord], dict[str, str]]:
    """Find built-in modules and, unless disabled, plugin modules.

    A plugin that fails to import is reported in the returned errors and skipped, so one broken plugin never takes
    the others down. A broken built-in module is a bug in Heinzelmännchen and raises.

    Returns:
        The discovered modules, and the errors of plugins that failed to load, keyed by entry point name.
    """
    builtin = Origin(distribution="heinzel", version=__version__, builtin=True)
    records = list(walk(importlib.import_module(BUILTIN_PACKAGE), builtin))
    errors: dict[str, str] = {}
    if not plugins:
        return records, errors
    for entry_point in sorted(entry_points(group=ENTRY_POINT_GROUP), key=lambda ep: ep.name):
        dist = entry_point.dist
        origin = Origin(
            distribution=dist.name if dist else entry_point.name,
            version=dist.version if dist else "unknown",
            builtin=False,
        )
        try:
            target = entry_point.load()
        except Exception as exc:  # noqa: BLE001 - isolate broken plugins
            _skip(errors, entry_point.name, f"{type(exc).__name__}: {exc}")
            continue
        if not isinstance(target, ModuleType):
            _skip(errors, entry_point.name, f"TypeError: entry point must name a package, not {type(target).__name__}")
            continue
        try:
            records.extend(walk(target, origin))
        except Exception as exc:  # noqa: BLE001 - isolate broken plugins
            _skip(errors, entry_point.name, f"{type(exc).__name__}: {exc}")
    return records, errors


def _skip(errors: dict[str, str], name: str, problem: str) -> None:
    logger.warning("Skipping plugin %r: %s", name, problem)
    errors[name] = problem


def walk(package: ModuleType, origin: Origin, parent: str | None = None) -> Iterator[ModuleRecord]:
    """Yield the modules declared in `package` and the packages below it. Private packages (`_x`) are skipped."""
    declared = getattr(package, "module", None)
    if isinstance(declared, Module):
        parent = f"{parent}.{declared.name}" if parent else declared.name
        yield ModuleRecord(qualname=parent, module=declared, origin=origin)
    for info in pkgutil.iter_modules(getattr(package, "__path__", [])):
        if info.ispkg and not info.name.startswith("_"):
            yield from walk(importlib.import_module(f"{package.__name__}.{info.name}"), origin, parent)
