"""Host source continuity for declared connected providers; separate from access and publisher trust."""

import ast
import builtins
import hashlib
import importlib.abc
import importlib.util
import io
import json
import os
import stat
import sys
import sysconfig
import zipfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from importlib import machinery
from importlib.machinery import ModuleSpec
from importlib.metadata import EntryPoint, distribution
from importlib.resources.abc import Traversable, TraversableResources
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import cast

from pydantic import JsonValue

from heinzel.kernel._captured_source import executing_captured_source
from heinzel.kernel._connected_read import (
    ConnectedReadLease,
    ConnectedReadMethod,
    ConnectedReadReview,
    ConnectedReadService,
)
from heinzel.kernel._discovery import walk
from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._kernel import Kernel, ModuleRecord, Origin

_MAX_FILES = 256
_MAX_BYTES = 2_000_000


def _read_source(path: Path) -> bytes:
    """Read stable regular source bytes.

    Raises:
        HeinzelError: If source is unsuitable or changed while read.
    """
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_BYTES:
            msg = "Source file is unsupported"
            raise HeinzelError(msg)
        data = source.read(_MAX_BYTES + 1)
        after = os.fstat(source.fileno())
    if len(data) != before.st_size or (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        msg = "Source changed while inspected"
        raise HeinzelError(msg)
    return data


def _validate_path(path: Path) -> None:
    """Refuse linked files and native code.

    Raises:
        HeinzelError: If the path is unsupported.
    """
    if path.is_symlink():
        msg = "Source package contains a linked file"
        raise HeinzelError(msg)
    if path.suffix in {".so", ".pyd", ".dll", ".dylib"}:
        msg = "Native plugin code is unsupported"
        raise HeinzelError(msg)


def _snapshot(root: Path) -> dict[str, bytes]:
    """Capture a bounded Python-only package tree.

    Raises:
        HeinzelError: If the tree is unavailable, unsupported or changed.
    """
    if root.is_symlink() or not root.is_dir():
        msg = "Source package is unavailable"
        raise HeinzelError(msg)
    result: dict[str, bytes] = {}
    for directory, names, files in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in names:
            if (base / name).is_symlink():
                msg = "Source package contains a linked directory"
                raise HeinzelError(msg)
        names[:] = [name for name in names if name != "__pycache__"]
        for name in files:
            path = base / name
            _validate_path(path)
            data = _read_source(path)
            result[path.relative_to(root).as_posix()] = data
            if len(result) > _MAX_FILES or sum(map(len, result.values())) > _MAX_BYTES:
                msg = "Source package exceeds bounds"
                raise HeinzelError(msg)
    if "__init__.py" not in result:
        msg = "Source package has no initializer"
        raise HeinzelError(msg)
    return result


@dataclass(frozen=True)
class SourceSnapshot:
    """Immutable observed source bytes and a deterministic continuity digest."""

    root: Path
    files: tuple[tuple[str, bytes], ...]

    @classmethod
    def capture(cls, root: Path) -> "SourceSnapshot":
        return cls(root, tuple(sorted(_snapshot(root).items())))

    def verify(self) -> None:
        if tuple(sorted(_snapshot(self.root).items())) != self.files:
            msg = "Plugin source changed; restart is required"
            raise HeinzelError(msg)

    def digest(self) -> str:
        digest = hashlib.sha256()
        for name, data in self.files:
            digest.update(len(name.encode()).to_bytes(4, "big"))
            digest.update(name.encode())
            digest.update(len(data).to_bytes(8, "big"))
            digest.update(data)
        return digest.hexdigest()


def _check_dependencies(snapshot: SourceSnapshot, package: str, approved: frozenset[str]) -> None:
    """Refuse syntactically declared imports outside the host-approved runtime set.

    Raises:
        HeinzelError: If a declared external import is not in the approved set.
    """
    allowed = set(sys.stdlib_module_names) | set(approved) | {package}
    for name, data in snapshot.files:
        if not name.endswith(".py"):
            continue
        tree = ast.parse(data, filename=name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                dependencies = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                dependencies = [node.module.split(".")[0]]
            else:
                continue
            if any(dependency not in allowed for dependency in dependencies):
                msg = "Plugin declares an unapproved runtime dependency"
                raise HeinzelError(msg)


class SealedRuntime:
    """Already-loaded host runtime objects available to a captured plugin's static imports.

    This is not a sandbox: trusted runtime code and Python reflection remain outside the source graph.
    """

    def __init__(self, snapshot: SourceSnapshot, package: str, approved: frozenset[str]) -> None:
        self.package = package
        self.modules: dict[str, ModuleType] = {}
        self.attributes: dict[tuple[str, str], object] = {}
        self.specs: dict[str, tuple[object, object, object, object]] = {}
        self.paths: dict[str, tuple[str, ...] | None] = {}
        self.stdlib = Path(sysconfig.get_path("stdlib")).resolve()
        self.site_roots = tuple(Path(sysconfig.get_path(key)).resolve() for key in ("purelib", "platlib"))
        core = sys.modules["heinzel"]
        core_file = vars(core).get("__file__")
        if not isinstance(core_file, str):
            msg = "The host core runtime has no verifiable origin"
            raise HeinzelError(msg)
        self.core_root = Path(core_file).resolve().parent
        dist = distribution("pydantic")
        if dist.files is None:
            msg = "The host pydantic manifest is unavailable"
            raise HeinzelError(msg)
        locations = [dist.locate_file(item) for item in dist.files]
        if any(not isinstance(location, Path) for location in locations):
            msg = "The host pydantic manifest has unsupported locations"
            raise HeinzelError(msg)
        self.pydantic_files = {location.resolve() for location in locations if isinstance(location, Path)}
        self.approved = approved
        self._scan(snapshot)

    def _seal(self, name: str) -> ModuleType:
        module = sys.modules.get(name)
        if not isinstance(module, ModuleType):
            msg = f"A declared host runtime module is not already loaded: {name}"
            raise HeinzelError(msg)
        spec = vars(module).get("__spec__")
        if not isinstance(spec, ModuleSpec) or sys.modules.get(spec.name) is not module:
            msg = f"A declared host runtime module has an invalid spec: {name}"
            raise HeinzelError(msg)
        origin = spec.origin
        if origin in {"built-in", "frozen"}:
            expected = machinery.BuiltinImporter if origin == "built-in" else machinery.FrozenImporter
            canonical = expected.find_spec(spec.name)
            if cast("object", spec.loader) is not expected or canonical is None or canonical.origin != origin:
                msg = "A host runtime loader changed"
                raise HeinzelError(msg)
        else:
            if not isinstance(origin, str) or vars(module).get("__file__") != origin:
                msg = "A host runtime origin changed"
                raise HeinzelError(msg)
            path = Path(origin).resolve()
            top = spec.name.split(".")[0]
            valid = (
                (top == "heinzel" and path.is_relative_to(self.core_root))
                or (top == "pydantic" and path in self.pydantic_files)
                or (
                    top in sys.stdlib_module_names
                    and path.is_relative_to(self.stdlib)
                    and not any(path.is_relative_to(site) for site in self.site_roots)
                    and "site-packages" not in path.relative_to(self.stdlib).parts
                    and "dist-packages" not in path.relative_to(self.stdlib).parts
                )
            )
            if not valid or not isinstance(
                spec.loader,
                (
                    machinery.SourceFileLoader,
                    machinery.SourcelessFileLoader,
                    machinery.ExtensionFileLoader,
                ),
            ):
                msg = f"A declared host runtime origin is unverified: {name}"
                raise HeinzelError(msg)
        self.modules[name] = module
        self.specs[name] = (spec, spec.loader, spec.origin, vars(module).get("__file__"))
        search = vars(module).get("__path__")
        if search is not None:
            if not isinstance(search, (list, tuple)):
                msg = "A host runtime package path is unsupported"
                raise HeinzelError(msg)
            paths = tuple(cast("Sequence[object]", search))
            if (
                len(paths) != 1
                or not isinstance(paths[0], str)
                or Path(paths[0]).resolve() != Path(origin).resolve().parent
            ):
                msg = "A host runtime package path changed"
                raise HeinzelError(msg)
            self.paths[name] = (paths[0],)
        else:
            self.paths[name] = None
        return module

    def _scan(self, snapshot: SourceSnapshot) -> None:
        for filename, data in snapshot.files:
            if not filename.endswith(".py"):
                continue
            for node in ast.walk(ast.parse(data, filename=filename)):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                    attrs: list[str] = []
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module]
                    attrs = [alias.name for alias in node.names]
                else:
                    continue
                for name in names:
                    self._seal_import(name, attrs)

    def _seal_import(self, name: str, attrs: list[str]) -> None:
        top = name.split(".", maxsplit=1)[0]
        if top == self.package:
            return
        if top not in self.approved and top not in sys.stdlib_module_names:
            msg = "Plugin declares an unapproved runtime dependency"
            raise HeinzelError(msg)
        parts = name.split(".")
        for end in range(1, len(parts) + 1):
            self._seal(".".join(parts[:end]))
            if end > 1:
                parent_name = ".".join(parts[: end - 1])
                child = parts[end - 1]
                if vars(self.modules[parent_name]).get(child) is not self.modules[".".join(parts[:end])]:
                    msg = "A host runtime submodule binding changed"
                    raise HeinzelError(msg)
                self.attributes[(parent_name, child)] = self.modules[".".join(parts[:end])]
        module = self.modules[name]
        for attr in attrs:
            if attr == "*" or attr not in vars(module):
                msg = f"A declared host runtime attribute is not already loaded: {name}.{attr}"
                raise HeinzelError(msg)
            value = vars(module)[attr]
            if isinstance(value, ModuleType):
                child_spec = vars(value).get("__spec__")
                if not isinstance(child_spec, ModuleSpec) or self._seal(child_spec.name) is not value:
                    msg = "A host runtime attribute module is unverified"
                    raise HeinzelError(msg)
            self.attributes[(name, attr)] = value

    def verify(self) -> None:
        """Refuse changes to the already-loaded modules and attributes used by the plugin.

        Raises:
            HeinzelError: If a sealed object changed.
        """
        for name, module in self.modules.items():
            spec, loader, origin, file = self.specs[name]
            path = vars(module).get("__path__")
            if (
                sys.modules.get(name) is not module
                or vars(module).get("__spec__") is not spec
                or not isinstance(spec, ModuleSpec)
                or spec.loader is not loader
                or spec.origin != origin
                or vars(module).get("__file__") != file
                or (tuple(cast("Sequence[object]", path)) if isinstance(path, (list, tuple)) else None)
                != self.paths[name]
            ):
                msg = "The sealed host runtime module changed"
                raise HeinzelError(msg)
        for (name, attr), value in self.attributes.items():
            if vars(self.modules[name]).get(attr) is not value:
                msg = "The sealed host runtime attribute changed"
                raise HeinzelError(msg)

    def import_module(
        self,
        name: str,
        caller_globals: dict[str, object] | None = None,
        caller_locals: Mapping[str, object] | None = None,
        fromlist: tuple[str, ...] | None = (),
        level: int = 0,
    ) -> object:
        self.verify()
        if level or name == self.package or name.startswith(self.package + "."):
            return builtins.__import__(name, caller_globals, caller_locals, fromlist, level)
        module = self.modules.get(name)
        if module is None or sys.modules.get(name) is not module:
            msg = "The host runtime import is not sealed"
            raise HeinzelError(msg)
        for attr in fromlist or ():
            key = (name, attr)
            if key not in self.attributes or vars(module).get(attr) is not self.attributes[key]:
                msg = "The host runtime attribute changed"
                raise HeinzelError(msg)
        root = name.split(".", maxsplit=1)[0]
        result = module if fromlist else self.modules[root]
        if sys.modules.get(root) is not self.modules[root]:
            msg = "The host runtime module changed"
            raise HeinzelError(msg)
        return result


class _CapturedResources(TraversableResources):
    """Read package resources from captured bytes, never from the later disk state."""

    def __init__(self, snapshot: SourceSnapshot, package_path: str) -> None:
        self.snapshot = snapshot
        self.prefix = package_path + "/" if package_path else ""

    def files(self) -> Traversable:
        """Expose an immutable in-memory archive of the captured resource bytes."""
        self.snapshot.verify()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, data in self.snapshot.files:
                archive.writestr(name, data)
        return zipfile.Path(zipfile.ZipFile(buffer, "r"), at=self.prefix)

    def open_resource(self, resource: str) -> io.BufferedReader:
        self.snapshot.verify()
        if "/" in resource or resource in {"", ".", ".."}:
            raise FileNotFoundError
        data = dict(self.snapshot.files).get(self.prefix + resource)
        if data is None:
            raise FileNotFoundError
        return io.BufferedReader(io.BytesIO(data))

    def resource_path(self, resource: str) -> str:
        raise FileNotFoundError(resource)

    def is_resource(self, path: str) -> bool:
        self.snapshot.verify()
        return "/" not in path and self.prefix + path in dict(self.snapshot.files)

    def contents(self) -> Iterator[str]:
        self.snapshot.verify()
        return iter(
            sorted(
                {
                    name[len(self.prefix) :].split("/", 1)[0]
                    for name, _ in self.snapshot.files
                    if name.startswith(self.prefix)
                }
            )
        )


class SourceOnlyFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Compile captured package bytes without relying on timestamp bytecode caches."""

    def __init__(self, name: str, snapshot: SourceSnapshot, runtime: SealedRuntime | None = None) -> None:
        if not name.isidentifier() or name in sys.modules or any(key.startswith(name + ".") for key in sys.modules):
            msg = "Package was already loaded or has an invalid name"
            raise HeinzelError(msg)
        self.name = name
        self.snapshot = snapshot
        self.files = MappingProxyType(dict(snapshot.files))
        self.runtime = runtime

    def find_spec(self, fullname: str, path: object = None, target: ModuleType | None = None) -> ModuleSpec | None:
        del path
        if fullname != self.name and not fullname.startswith(self.name + "."):
            return None
        if target is not None:
            msg = "Reloading a captured plugin is unsupported"
            raise HeinzelError(msg)
        self.snapshot.verify()
        part = fullname[len(self.name) :].lstrip(".").replace(".", "/")
        init = f"{part}/__init__.py" if part else "__init__.py"
        module = f"{part}.py"
        if init in self.files:
            return importlib.util.spec_from_file_location(
                fullname,
                self.snapshot.root / init,
                loader=self,
                submodule_search_locations=[str((self.snapshot.root / init).parent)],
            )
        if module in self.files:
            return importlib.util.spec_from_file_location(fullname, self.snapshot.root / module, loader=self)
        msg = "Module is outside the captured source graph"
        raise ModuleNotFoundError(msg)

    def get_resource_reader(self, fullname: str) -> _CapturedResources | None:
        if fullname != self.name and not fullname.startswith(self.name + "."):
            return None
        part = fullname[len(self.name) :].lstrip(".").replace(".", "/")
        init = f"{part}/__init__.py" if part else "__init__.py"
        return _CapturedResources(self.snapshot, part) if init in self.files else None

    def create_module(self, spec: ModuleSpec) -> None:
        del spec

    def exec_module(self, module: ModuleType) -> None:
        self.snapshot.verify()
        spec = module.__spec__
        if spec is None or spec.origin is None:
            msg = "The source-bound module has no origin"
            raise HeinzelError(msg)
        origin = spec.origin
        path = Path(origin).relative_to(self.snapshot.root).as_posix()
        code = compile(self.files[path], origin, "exec", dont_inherit=True)
        if self.runtime is not None:
            module.__dict__["__builtins__"] = dict(vars(builtins), __import__=self.runtime.import_module)

        def verify_context() -> bool:
            active_spec = module.__spec__
            if (
                self.runtime is None
                or sys.modules.get(module.__name__) is not module
                or active_spec is None
                or active_spec.loader is not self
            ):
                return False
            self.snapshot.verify()
            self.runtime.verify()
            return True

        with executing_captured_source(module.__dict__, verify_context):
            exec(code, module.__dict__)  # noqa: S102 - explicitly owner-selected plugin source
        self.snapshot.verify()


@dataclass(frozen=True)
class CapturedEntry:
    """Host-owned association between source, entry-point records, and loader."""

    finder: SourceOnlyFinder
    records: tuple[ModuleRecord, ...]
    approved_runtime: frozenset[str]
    entry: EntryPoint
    origin: Origin
    require_declaration: bool

    def service[T](self, kernel: Kernel, record: ModuleRecord, contract: type[T]) -> T:
        """Resolve only an exact recorded service whose class came from this source loader.

        Raises:
            HeinzelError: If source, registration, or the live implementation changed.
        """
        if not any(candidate is record for candidate in self.records) or kernel.module(record.qualname) is not record:
            msg = "The source-bound module record changed"
            raise HeinzelError(msg)
        if self.entry.dist is not None:
            _validate_distribution_source(self.entry, self.finder.snapshot.root, self.origin, self.require_declaration)
        self.finder.snapshot.verify()
        if self.finder.runtime is not None:
            self.finder.runtime.verify()
        instance = kernel.resolve_selected_service(contract, record)
        cls = type(instance)
        module = sys.modules.get(cls.__module__)
        if (
            (not cls.__module__.startswith(self.finder.name + ".") and cls.__module__ != self.finder.name)
            or module is None
            or module.__spec__ is None
            or module.__spec__.loader is not self.finder
        ):
            msg = "The source-bound service implementation changed"
            raise HeinzelError(msg)
        self.finder.snapshot.verify()
        if self.finder.runtime is not None:
            self.finder.runtime.verify()
        return instance


def _check_entry_loaded(module: object, finder: SourceOnlyFinder) -> None:
    if not isinstance(module, ModuleType) or module.__spec__ is None or module.__spec__.loader is not finder:
        msg = "The entry point did not use the source loader"
        raise HeinzelError(msg)


def capture_entry(
    entry: EntryPoint,
    root: Path,
    origin: Origin,
    approved_runtime: frozenset[str] = frozenset(),
) -> CapturedEntry:
    """Load an exact selected entry point from captured source before its package is imported.

    The caller must derive the selected package and source root from trusted host discovery.
    This creates no access authority; external imports use the already-loaded sealed runtime.

    Raises:
        HeinzelError: If the entry point is unsuitable or returns an unbound package.
    """
    if entry.module != entry.value or "." in entry.module:
        msg = "The source-bound entry point must name one root package"
        raise HeinzelError(msg)
    snapshot = SourceSnapshot.capture(root)
    _check_dependencies(snapshot, entry.module, approved_runtime)
    if not root.is_absolute():
        msg = "The host source root must be absolute"
        raise HeinzelError(msg)
    runtime = SealedRuntime(snapshot, entry.module, approved_runtime)
    finder = SourceOnlyFinder(entry.module, snapshot, runtime)
    sys.meta_path.insert(0, finder)
    try:
        module = entry.load()
        _check_entry_loaded(module, finder)
        records = tuple(walk(module, origin))
        finder.snapshot.verify()
        return CapturedEntry(finder, records, approved_runtime, entry, origin, _has_declaration(entry))
    except BaseException:
        sys.meta_path.remove(finder)
        raise


@dataclass(frozen=True)
class SourceBinding[T]:
    """Exact host-selected provider binding for a source-guarded call."""

    capture: CapturedEntry
    kernel: Kernel
    record: ModuleRecord
    contract: type[T]
    capability: str


class CaptureCatalog:
    """Host-owned catalog; reuse only the same sealed package in this process."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[EntryPoint, Path, Origin, frozenset[str], bool, CapturedEntry]] = {}

    def capture(self, entry: EntryPoint, root: Path, origin: Origin, approved_runtime: frozenset[str]) -> CapturedEntry:
        """Reuse the host's exact capture, or capture a fresh not-yet-imported entry point.

        Raises:
            HeinzelError: If the host selection, source, or registration has changed.
        """
        existing = self._entries.get(entry.module)
        if existing:
            old_entry, old_root, old_origin, old_runtime, old_declaration, captured = existing
            require_declaration = _has_declaration(entry)
            if (entry, root, origin, approved_runtime, require_declaration) != (
                old_entry,
                old_root,
                old_origin,
                old_runtime,
                old_declaration,
            ):
                msg = "The host source selection changed"
                raise HeinzelError(msg)
            captured.finder.snapshot.verify()
            loaded = sys.modules.get(entry.module)
            _check_entry_loaded(loaded, captured.finder)
            return captured
        captured = capture_entry(entry, root, origin, approved_runtime)
        self._entries[entry.module] = (entry, root, origin, approved_runtime, captured.require_declaration, captured)
        return captured

    def guarded_call[T](self, binding: SourceBinding[T], call: Callable[[T], object]) -> object:
        """Check the same registered instance and source before and after a host-selected operation.

        Raises:
            HeinzelError: If the result was produced after the source or instance changed.
        """
        capture, kernel, record, contract, capability = (
            binding.capture,
            binding.kernel,
            binding.record,
            binding.contract,
            binding.capability,
        )
        if not any(stored[-1] is capture for stored in self._entries.values()):
            msg = "The source capture is not owned by this host catalog"
            raise HeinzelError(msg)
        if kernel.selected_capability_provider(capability)[0] is not record:
            msg = "The selected capability changed"
            raise HeinzelError(msg)
        instance = capture.service(kernel, record, contract)
        result = call(instance)
        if kernel.selected_capability_provider(capability)[0] is not record:
            msg = "The selected capability changed during the operation"
            raise HeinzelError(msg)
        if capture.service(kernel, record, contract) is not instance:
            msg = "The source-bound service instance changed during the operation"
            raise HeinzelError(msg)
        return result


class SourceGuardedLease[T, L]:
    """Source-bound lease fence; cleanup remains available after source changes."""

    def __init__(
        self, catalog: CaptureCatalog, binding: SourceBinding[T], lease: L, cleanup: Callable[[L], None]
    ) -> None:
        self._catalog, self._binding, self._lease, self._cleanup = catalog, binding, lease, cleanup
        self._closed = False

    def call(self, operation: Callable[[L], object]) -> object:
        """Run a host-selected lease operation with source and selected-provider checks.

        Raises:
            HeinzelError: If the lease is closed or source authority changed.
        """
        if self._closed:
            msg = "The source-bound lease is closed"
            raise HeinzelError(msg)
        return self._catalog.guarded_call(self._binding, lambda _service: operation(self._lease))

    def close(self) -> None:
        """Release resources even if the source is stale; never perform a new read."""
        if not self._closed:
            self._closed = True
            self._cleanup(self._lease)


def open_source_lease[T, L](
    catalog: CaptureCatalog,
    binding: SourceBinding[T],
    opener: Callable[[T], L],
    cleanup: Callable[[L], None],
) -> SourceGuardedLease[T, L]:
    """Capture a lease only while its provider remains current; close if the post-check fails.

    Raises:
        HeinzelError: If provider authority changed before or during opening.
    """
    opened: list[L] = []

    def open_once(service: T) -> L:
        lease = opener(service)
        opened.append(lease)
        return lease

    try:
        catalog.guarded_call(binding, open_once)
    except BaseException:
        for lease in opened:
            cleanup(lease)
        raise
    return SourceGuardedLease(catalog, binding, opened[0], cleanup)


def _has_declaration(entry: EntryPoint) -> bool:
    if entry.dist is None:
        return False
    declared = [
        item
        for item in entry.dist.entry_points
        if item.group == "heinzel.connected_sources" and item.name == entry.name
    ]
    return len(declared) == 1 and declared[0].value == entry.value


def _validate_distribution_source(
    entry: EntryPoint, root: Path, origin: Origin, require_declaration: bool = False
) -> None:
    """Bind the selected installed distribution's manifest to the exact source package.

    Raises:
        HeinzelError: If the distribution, manifest or selected root cannot be established.
        OSError: If installation metadata is unavailable.
        ValueError: If metadata is malformed.
    """
    dist = entry.dist
    if dist is None or dist.files is None or entry not in dist.entry_points:
        msg = "The installed distribution manifest is unavailable"
        raise HeinzelError(msg)
    if require_declaration and not _has_declaration(entry):
        msg = "The installed connected source declaration changed"
        raise HeinzelError(msg)
    if origin.distribution != dist.name or origin.version != dist.version:
        msg = "The selected distribution identity changed"
        raise HeinzelError(msg)
    direct_url = dist.read_text("direct_url.json")
    if direct_url is not None:
        metadata = cast("JsonValue", json.loads(direct_url))
        directory = metadata.get("dir_info", {}) if isinstance(metadata, dict) else None
        if not isinstance(directory, dict) or directory.get("editable", False):
            msg = "Editable source distributions cannot be source-bound"
            raise HeinzelError(msg)
    located = dist.locate_file(entry.module)
    if (
        not root.is_absolute()
        or root.is_symlink()
        or not isinstance(located, Path)
        or located.resolve() != root.resolve()
    ):
        msg = "The selected source root is not owned by the installed distribution"
        raise HeinzelError(msg)
    manifest: set[Path] = set()
    for item in dist.files:
        location = dist.locate_file(item)
        if not isinstance(location, Path):
            msg = "The installed distribution has an unsupported source location"
            raise HeinzelError(msg)
        manifest.add(location.resolve())
    snapshot = SourceSnapshot.capture(root)
    if any((root / name).resolve() not in manifest for name, _ in snapshot.files):
        msg = "The selected source graph is not covered by the distribution manifest"
        raise HeinzelError(msg)


@dataclass(frozen=True)
class ReviewedSource:
    """A host-supplied audit selection, not a plugin-provided attestation."""

    package: str
    root: Path
    approved_runtime: frozenset[str]


class SourceDiscovery:
    """Capture declared installed providers before loading their entry points."""

    def __init__(self, selections: dict[str, ReviewedSource] | None = None, *, installed: bool = False) -> None:
        self._selections = MappingProxyType(dict(selections or {}))
        self._installed = installed
        self._catalog = CaptureCatalog()
        self._captured: dict[str, CapturedEntry] = {}

    @property
    def catalog(self) -> CaptureCatalog:
        """Host-owned capture catalog for source-guarded operations."""
        return self._catalog

    def try_capture(self, entry: EntryPoint, origin: Origin) -> CapturedEntry | None:
        """Capture host-selected candidates before loading the entry point.

        Raises:
            HeinzelError: If capture fails before import.
            OSError: If source inspection fails.
            SyntaxError: If source cannot be parsed.
            ValueError: If source metadata is invalid.
        """
        reviewed = self._selections.get(entry.name)
        require_declaration = False
        if reviewed is None and self._installed and entry.dist is not None:
            declarations = [
                item
                for item in entry.dist.entry_points
                if item.group == "heinzel.connected_sources" and item.name == entry.name
            ]
            if len(declarations) == 1 and declarations[0].value == entry.value:
                location = entry.dist.locate_file(entry.module)
                if isinstance(location, Path):
                    reviewed = ReviewedSource(entry.module, location, frozenset({"heinzel", "pydantic"}))
                    require_declaration = True
        if reviewed is None or entry.module != reviewed.package:
            return None
        try:
            _validate_distribution_source(entry, reviewed.root, origin, require_declaration)
            snapshot = SourceSnapshot.capture(reviewed.root)
            _check_dependencies(snapshot, entry.module, reviewed.approved_runtime)
            SealedRuntime(snapshot, entry.module, reviewed.approved_runtime)
        except (HeinzelError, OSError, SyntaxError, ValueError):
            return None
        preloaded = entry.module in sys.modules
        try:
            capture = self._catalog.capture(entry, reviewed.root, origin, reviewed.approved_runtime)
        except (HeinzelError, OSError, SyntaxError, ValueError):
            if preloaded:
                return None
            raise
        self._captured[entry.name] = capture
        return capture

    def binding[T](self, kernel: Kernel, capability: str, contract: type[T]) -> SourceBinding[T]:
        """Bind the host-selected record to the exact catalog-owned captured provider.

        Raises:
            HeinzelError: If no captured source matches the current capability selection.
        """
        record, provider = kernel.selected_capability_provider(capability)
        matches = [capture for capture in self._captured.values() if any(item is record for item in capture.records)]
        if len(matches) != 1:
            msg = "The selected provider has no reviewed source capture"
            raise HeinzelError(msg)
        capture = matches[0]
        factory_module = sys.modules.get(provider.factory.__module__)
        if (
            factory_module is None
            or factory_module.__spec__ is None
            or factory_module.__spec__.loader is not capture.finder
        ):
            msg = "The selected capability implementation is outside the captured source"
            raise HeinzelError(msg)
        binding = SourceBinding(capture, kernel, record, contract, capability)
        self._catalog.guarded_call(binding, lambda service: service)
        return binding


class SourceGuardedConnectedLease(ConnectedReadLease):
    """Host guard around a provider lease and its source revision."""

    def __init__(self, lease: SourceGuardedLease[ConnectedReadService, ConnectedReadLease], source: str) -> None:
        self._lease, self._source = lease, source

    def review(self) -> ConnectedReadReview:
        observed = self._lease.call(lambda lease: lease.review())
        review = ConnectedReadReview.model_validate(observed)
        digest = hashlib.sha256((self._source + "\0" + review.source_revision).encode()).hexdigest()
        return review.model_copy(update={"source_revision": "host-source-v1:" + digest})

    def read(self, arguments: Mapping[str, JsonValue]) -> JsonValue:
        return cast("JsonValue", self._lease.call(lambda lease: lease.read(arguments)))

    def close(self) -> None:
        self._lease.close()


class SourceGuardedConnectedService(ConnectedReadService):
    """Host continuity wrapper; this is not approval or publisher verification."""

    def __init__(self, discovery: SourceDiscovery, binding: SourceBinding[ConnectedReadService]) -> None:
        self._discovery, self._binding = discovery, binding

    def supported_reads(self) -> tuple[ConnectedReadMethod, ...]:
        value = self._discovery.catalog.guarded_call(self._binding, lambda service: service.supported_reads())
        if not isinstance(value, tuple) or any(
            not isinstance(item, ConnectedReadMethod) for item in cast("tuple[object, ...]", value)
        ):
            msg = "The source-bound provider returned invalid methods"
            raise HeinzelError(msg)
        return cast("tuple[ConnectedReadMethod, ...]", value)

    def open_lease(self, session: str, operation: str, fixed_arguments: Mapping[str, JsonValue]) -> ConnectedReadLease:
        lease = open_source_lease(
            self._discovery.catalog,
            self._binding,
            lambda service: service.open_lease(session, operation, fixed_arguments),
            lambda value: value.close(),
        )
        capture = self._binding.capture
        source = (
            str(capture.finder.snapshot.root)
            + "\0"
            + capture.finder.snapshot.digest()
            + "\0"
            + "\0".join(sorted(capture.approved_runtime))
        )
        return SourceGuardedConnectedLease(lease, source)
