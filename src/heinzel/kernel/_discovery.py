"""Discovery: find built-in modules and plugin modules, without any registration by hand.

Built-in modules live in `heinzel.modules`. A plugin is a distribution with an entry point in the `heinzel.modules`
group that points at its root package. In both cases the kernel walks the package tree: every package declaring
`module = Module(...)` is a module, and the nearest declaring ancestor is its parent.
"""

import importlib
import logging
import pkgutil
from collections.abc import Iterator
from importlib.metadata import Distribution, PackageNotFoundError, distribution, entry_points
from types import ModuleType

from heinzel import __version__
from heinzel.kernel._installation import CORE_DISTRIBUTION
from heinzel.kernel._kernel import ModuleRecord, Origin
from heinzel.kernel._module import Module

BUILTIN_PACKAGE = "heinzel.modules"
REPOSITORY_LABELS = ("source", "sourcecode", "repository", "homepage")
"""Project-URL labels naming the source repository, normalized as in PEP 753, most specific first."""
ENTRY_POINT_GROUP = "heinzel.modules"

logger = logging.getLogger(__name__)


def discover(*, plugins: bool = True) -> tuple[list[ModuleRecord], dict[str, str]]:
    """Find built-in modules and, unless disabled, plugin modules.

    A plugin that fails to import is reported in the returned errors and skipped, so one broken plugin never takes
    the others down. A broken built-in module is a bug in Heinzelmännchen and raises.

    Returns:
        The discovered modules, and the errors of plugins that failed to load, keyed by entry point name.
    """
    builtin = Origin(distribution=CORE_DISTRIBUTION, version=__version__, builtin=True, url=core_repository())
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
            url=repository_of(dist) if dist else None,
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


def repository_of(dist: Distribution) -> str | None:
    """The source repository a distribution names in its Project-URL metadata, never a hard-coded one.

    Labels are compared as PEP 753 normalizes them (`Source Code` is `sourcecode`); Source beats Repository beats
    Homepage. Only http(s) URLs count: the value is shown on terminals and in help.
    """
    try:
        entries = dist.metadata.get_all("Project-URL") or []
    except Exception:  # noqa: BLE001 - broken metadata costs the link, never the plugin
        return None
    urls: dict[str, str] = {}
    for entry in entries:
        label, _, url = entry.partition(",")
        normalized = "".join(char for char in label.lower() if char.isalnum())
        url = url.strip()
        if url.startswith(("https://", "http://")) and not any(char.isspace() for char in url):
            urls.setdefault(normalized, url)
    return next((urls[label] for label in REPOSITORY_LABELS if label in urls), None)


def core_repository() -> str | None:
    """The source repository of Heinzelmännchen itself, from its own metadata; None if it isn't installed."""
    try:
        return repository_of(distribution(CORE_DISTRIBUTION))
    except PackageNotFoundError:
        return None
