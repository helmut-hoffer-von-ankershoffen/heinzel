"""Source-only loader experiment does not rely on Python timestamp caches."""

import builtins
import importlib
import os
import py_compile
import sys
from importlib.metadata import EntryPoint
from importlib.util import spec_from_file_location
from types import ModuleType

import pytest

from heinzel.kernel import Config, ConnectedReadService, Kernel, Origin, Settings, captured_source_active
from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._source_loading import (
    CaptureCatalog,
    SealedRuntime,
    SourceBinding,
    SourceOnlyFinder,
    SourceSnapshot,
    open_source_lease,
)
from tests._source_distribution_fixture import installed_entry

pytestmark = pytest.mark.unit


def test_public_capture_marker_is_limited_to_the_exact_current_module(tmp_path, monkeypatch):
    root = tmp_path / "scoped_marker_fixture"
    root.mkdir()
    (root / "__init__.py").write_text(
        "import builtins\nfrom heinzel import Module, captured_source_active\n"
        "root_before = captured_source_active()\nforeign = builtins.source_marker_foreign()\n"
        "from . import child\nroot_after = captured_source_active()\n"
        "def late(): return captured_source_active()\nmodule = Module('scope', 'fixture')\n"
    )
    (root / "child.py").write_text(
        "from heinzel import captured_source_active\nchild_active = captured_source_active()\n"
    )
    monkeypatch.setattr(builtins, "source_marker_foreign", captured_source_active, raising=False)

    # The foreign function must call the predicate from its own globals, not as the plugin itself.
    def foreign():
        return captured_source_active()

    monkeypatch.setattr(builtins, "source_marker_foreign", foreign)
    monkeypatch.syspath_prepend(str(tmp_path))
    assert not captured_source_active()
    entry = EntryPoint(name="scope", value=root.name, group="heinzel.modules")
    catalog = CaptureCatalog()
    captured = catalog.capture(
        entry, root, Origin(distribution="fixture", version="1", builtin=False), frozenset({"heinzel"})
    )
    try:
        plugin = sys.modules[root.name]
        assert plugin.root_before is True
        assert plugin.root_after is True
        assert plugin.child.child_active is True
        assert plugin.foreign is False
        assert plugin.late() is False
        assert not captured_source_active()
    finally:
        sys.meta_path.remove(captured.finder)
        sys.modules.pop(root.name + ".child", None)
        sys.modules.pop(root.name, None)


def test_public_capture_marker_is_false_for_ordinary_and_unsealed_loaders(tmp_path, monkeypatch):
    root = tmp_path / "ordinary_marker_fixture"
    root.mkdir()
    (root / "__init__.py").write_text("from heinzel import captured_source_active\nactive = captured_source_active()\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    ordinary = importlib.import_module(root.name)
    assert ordinary.active is False
    sys.modules.pop(root.name)
    finder = SourceOnlyFinder(root.name, SourceSnapshot.capture(root))
    sys.meta_path.insert(0, finder)
    try:
        unsealed = importlib.import_module(root.name)
        assert unsealed.active is False
    finally:
        sys.meta_path.remove(finder)
        sys.modules.pop(root.name, None)


def test_unloaded_shadowed_stdlib_is_refused_before_plugin_initializer(tmp_path, monkeypatch):
    root = tmp_path / "shadow_source_fixture"
    root.mkdir()
    marker = tmp_path / "executed"
    (root / "__init__.py").write_text(f"import mailbox\nopen({str(marker)!r}, 'w').close()\n")
    (tmp_path / "mailbox.py").write_text(f"open({str(marker)!r}, 'w').close()\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "mailbox", raising=False)
    entry = EntryPoint(name="shadow", value=root.name, group="heinzel.modules")
    with pytest.raises(HeinzelError, match="not already loaded"):
        CaptureCatalog().capture(entry, root, Origin(distribution="fixture", version="1", builtin=False), frozenset())
    assert not marker.exists()


def test_shadowed_external_runtime_is_refused_before_plugin_initializer(tmp_path, monkeypatch):
    root = tmp_path / "external_shadow_fixture"
    root.mkdir()
    marker = tmp_path / "executed"
    (root / "__init__.py").write_text(f"import pydantic\nopen({str(marker)!r}, 'w').close()\n")
    fake = tmp_path / "pydantic.py"
    fake.write_text("pass\n")
    module = ModuleType("pydantic")
    module.__spec__ = spec_from_file_location("pydantic", fake)
    module.__file__ = str(fake)
    monkeypatch.setitem(sys.modules, "pydantic", module)
    entry = EntryPoint(name="shadow", value=root.name, group="heinzel.modules")
    with pytest.raises(HeinzelError, match="origin is unverified"):
        CaptureCatalog().capture(
            entry, root, Origin(distribution="fixture", version="1", builtin=False), frozenset({"pydantic"})
        )
    assert not marker.exists()


def test_preloaded_shadowed_stdlib_is_refused_before_plugin_initializer(tmp_path, monkeypatch):
    root = tmp_path / "cached_shadow_fixture"
    root.mkdir()
    marker = tmp_path / "executed"
    (root / "__init__.py").write_text(f"import mailbox\nopen({str(marker)!r}, 'w').close()\n")
    fake = tmp_path / "mailbox.py"
    fake.write_text("pass\n")
    module = ModuleType("mailbox")
    module.__spec__ = spec_from_file_location("mailbox", fake)
    module.__file__ = str(fake)
    monkeypatch.setitem(sys.modules, "mailbox", module)
    entry = EntryPoint(name="shadow", value=root.name, group="heinzel.modules")
    with pytest.raises(HeinzelError, match="origin is unverified"):
        CaptureCatalog().capture(entry, root, Origin(distribution="fixture", version="1", builtin=False), frozenset())
    assert not marker.exists()


def test_sealed_runtime_refuses_changed_cached_modules_and_attributes(tmp_path, monkeypatch):
    import pathlib

    root = tmp_path / "sealed_fixture"
    root.mkdir()
    (root / "__init__.py").write_text("from pathlib import Path\n")
    runtime = SealedRuntime(SourceSnapshot.capture(root), root.name, frozenset())
    assert runtime.import_module("pathlib", fromlist=("Path",)) is pathlib
    with monkeypatch.context() as patch:
        patch.setattr(pathlib, "Path", object())
        with pytest.raises(HeinzelError, match="attribute changed"):
            runtime.import_module("pathlib", fromlist=("Path",))
    with monkeypatch.context() as patch:
        patch.setitem(sys.modules, "pathlib", ModuleType("pathlib"))
        with pytest.raises(HeinzelError, match="module changed"):
            runtime.import_module("pathlib", fromlist=("Path",))


def test_sealed_runtime_rejects_replaced_submodule(tmp_path, monkeypatch):

    root = tmp_path / "sealed_submodule_fixture"
    root.mkdir()
    (root / "__init__.py").write_text("from collections.abc import Mapping\n")
    runtime = SealedRuntime(SourceSnapshot.capture(root), root.name, frozenset())
    monkeypatch.setitem(sys.modules, "collections.abc", ModuleType("collections.abc"))
    with pytest.raises(HeinzelError, match="module changed"):
        runtime.import_module("collections.abc", fromlist=("Mapping",))


def test_sealed_runtime_rejects_changed_package_search_path(tmp_path, monkeypatch):
    import collections

    root = tmp_path / "sealed_path_fixture"
    root.mkdir()
    (root / "__init__.py").write_text("from collections.abc import Mapping\n")
    runtime = SealedRuntime(SourceSnapshot.capture(root), root.name, frozenset())
    monkeypatch.setattr(collections, "__path__", [str(tmp_path)])
    with pytest.raises(HeinzelError, match="module changed"):
        runtime.import_module("collections.abc", fromlist=("Mapping",))


def test_source_loader_ignores_stale_bytecode_and_detects_in_place_change(tmp_path):
    root = tmp_path / "plugin"
    root.mkdir()
    entry = root / "__init__.py"
    entry.write_text("value = 'old'\n")
    py_compile.compile(str(entry), doraise=True)
    before = entry.stat()
    entry.write_text("value = 'new'\n")
    os.utime(entry, ns=(before.st_atime_ns, before.st_mtime_ns))
    snapshot = SourceSnapshot.capture(root)
    finder = SourceOnlyFinder("sample_verified_plugin", snapshot)
    sys.meta_path.insert(0, finder)
    try:
        loaded = importlib.import_module(finder.name)
        assert loaded.value == "new"
        entry.write_text("value = 'two'\n")
        with pytest.raises(HeinzelError, match="changed"):
            snapshot.verify()
    finally:
        sys.meta_path.remove(finder)
        sys.modules.pop(finder.name, None)


def test_preloaded_and_symlinked_packages_are_refused(tmp_path):
    root = tmp_path / "plugin"
    root.mkdir()
    (root / "__init__.py").write_text("pass\n")
    snapshot = SourceSnapshot.capture(root)
    with pytest.raises(HeinzelError, match="already loaded"):
        SourceOnlyFinder("sys", snapshot)
    (root / "link.py").symlink_to(root / "__init__.py")
    with pytest.raises(HeinzelError, match="linked"):
        SourceSnapshot.capture(root)


def test_changed_package_refuses_later_captured_submodule_import(tmp_path):
    root = tmp_path / "plugin"
    root.mkdir()
    (root / "__init__.py").write_text("pass\n")
    module = root / "reader.py"
    module.write_text("value = 'one'\n")
    snapshot = SourceSnapshot.capture(root)
    finder = SourceOnlyFinder("sample_lazy_plugin", snapshot)
    sys.meta_path.insert(0, finder)
    try:
        importlib.import_module(finder.name)
        module.write_text("value = 'two'\n")
        with pytest.raises(HeinzelError, match="changed"):
            importlib.import_module(finder.name + ".reader")
    finally:
        sys.meta_path.remove(finder)
        sys.modules.pop(finder.name + ".reader", None)
        sys.modules.pop(finder.name, None)


def test_external_native_extensions_and_missing_modules_refuse(tmp_path):
    root = tmp_path / "plugin"
    root.mkdir()
    (root / "__init__.py").write_text("pass\n")
    (root / "native.so").write_bytes(b"not a native library")
    with pytest.raises(HeinzelError, match="Native"):
        SourceSnapshot.capture(root)
    (root / "native.so").unlink()
    finder = SourceOnlyFinder("sample_missing_plugin", SourceSnapshot.capture(root))
    sys.meta_path.insert(0, finder)
    try:
        importlib.import_module(finder.name)
        with pytest.raises(ModuleNotFoundError, match="outside"):
            importlib.import_module(finder.name + ".unknown")
    finally:
        sys.meta_path.remove(finder)
        sys.modules.pop(finder.name, None)


def _exercise_wrong_selection(catalog, captured, kernel, record):
    foreign = SourceBinding(captured, kernel, record, ConnectedReadService, "mail")
    with pytest.raises(HeinzelError):
        catalog.guarded_call(foreign, lambda value: value.read())


def _exercise_guarded_lease(catalog, binding, entry, old, new):
    _exercise_wrong_selection(catalog, binding.capture, binding.kernel, binding.record)
    closed = []
    lease = open_source_lease(catalog, binding, lambda _service: {"read": "private"}, closed.append)
    assert lease.call(lambda value: value["read"]) == "private"
    with pytest.raises(HeinzelError, match="changed"):
        lease.call(lambda _value: entry.write_text(old))
    lease.close()
    assert closed == [{"read": "private"}]
    entry.write_text(new)

    def open_after_change(_service):
        entry.write_text(old)
        return {"read": "opened"}

    with pytest.raises(HeinzelError, match="changed"):
        open_source_lease(catalog, binding, open_after_change, closed.append)
    assert closed[-1] == {"read": "opened"}


def test_real_entry_point_records_and_registered_provider_use_captured_source(tmp_path):
    root = tmp_path / "fake_bound_plugin"
    root.mkdir()
    entry = root / "__init__.py"
    old = """from pathlib import Path
from heinzel.kernel import ConnectedReadService, Module
assert Path(__file__).is_file()
module = Module("fixture", "fixture")
class Reader(ConnectedReadService):
    label = "old"
    def read(self): return self.label
module.service(Reader)
class Calendar:
    def events(self, window): return []
    def free_busy(self, people, window): return []
module.provides("calendar", modes={"read"}, name="fixture", hosts=["fixture.invalid"])(Calendar)
"""
    new = old.replace('"old"', '"new"')
    entry.write_text(old)
    py_compile.compile(str(entry), doraise=True)
    before = entry.stat()
    entry.write_text(new)
    os.utime(entry, ns=(before.st_atime_ns, before.st_mtime_ns))
    (root / "metadata.txt").write_text("captured resource")
    package = root / "child"
    package.mkdir()
    (package / "__init__.py").write_text('from heinzel.kernel import Module\nmodule = Module("child", "child")\n')
    ep = EntryPoint(name="fake", value="fake_bound_plugin", group="heinzel.modules")
    catalog = CaptureCatalog()
    origin = Origin(distribution="fixture", version="1", builtin=False)
    captured = catalog.capture(ep, root, origin, frozenset({"heinzel"}))
    try:
        assert [r.qualname for r in captured.records] == ["fixture", "fixture.child"]
        assert catalog.capture(ep, root, origin, frozenset({"heinzel"})) is captured
        kernel = Kernel(
            Settings(), captured.records, config=Config(files=(), data={"capabilities": {"calendar": "fixture"}})
        )
        record, actual = kernel.selected_capability_service(ConnectedReadService, "calendar")
        service = captured.service(kernel, record, ConnectedReadService)
        second_kernel = Kernel(
            Settings(), captured.records, config=Config(files=(), data={"capabilities": {"calendar": "fixture"}})
        )
        second_record, second_actual = second_kernel.selected_capability_service(ConnectedReadService, "calendar")
        assert captured.service(second_kernel, second_record, ConnectedReadService) is second_actual
        assert second_record is record
        assert service is actual
        assert service.label == "new"
        binding = SourceBinding(captured, kernel, record, ConnectedReadService, "calendar")
        assert catalog.guarded_call(binding, lambda value: value.read()) == "new"
        _exercise_guarded_lease(catalog, binding, entry, old, new)
        entry.write_text(new)

        def change_source(value):
            entry.write_text(old)
            return value.read()

        with pytest.raises(HeinzelError, match="changed"):
            catalog.guarded_call(binding, change_source)
        entry.write_text(new)
        assert sys.modules[ep.module].__file__ == str(entry)
        assert list(sys.modules[ep.module].__path__) == [str(root)]
        from importlib.resources import files

        assert files(ep.module).joinpath("metadata.txt").read_text() == "captured resource"
        entry.write_text(old)
        with pytest.raises(HeinzelError, match="changed"):
            captured.service(kernel, record, ConnectedReadService)
    finally:
        sys.meta_path.remove(captured.finder)
        for name in [ep.module + ".child", ep.module]:
            sys.modules.pop(name, None)


def test_unknown_declared_dependency_refused_before_initializer(tmp_path):
    from importlib.metadata import EntryPoint

    from heinzel.kernel import Origin
    from heinzel.kernel._source_loading import capture_entry

    root = tmp_path / "unknown_dependency_plugin"
    root.mkdir()
    (root / "__init__.py").write_text("import unapproved_third_party\n")
    ep = EntryPoint(name="fake", value="unknown_dependency_plugin", group="heinzel.modules")
    with pytest.raises(HeinzelError, match="unapproved"):
        capture_entry(ep, root, Origin(distribution="fixture", version="1", builtin=False))
    assert ep.module not in sys.modules


def test_static_dependency_check_is_not_a_dynamic_import_security_boundary(tmp_path, monkeypatch):
    from importlib.metadata import EntryPoint

    from heinzel.kernel import Origin
    from heinzel.kernel._source_loading import CaptureCatalog

    root = tmp_path / "dynamic_import_fixture"
    root.mkdir()
    (root / "__init__.py").write_text(
        "import importlib\nfrom heinzel.kernel import Module\n"
        'dependency = importlib.import_module("unapproved_helper_fixture")\nmodule = Module("demo", "demo")\n'
    )
    (tmp_path / "unapproved_helper_fixture.py").write_text("value = 42\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    ep = EntryPoint(name="demo", value="dynamic_import_fixture", group="heinzel.modules")
    catalog = CaptureCatalog()
    origin = Origin(distribution="fixture", version="1", builtin=False)
    captured = catalog.capture(ep, root, origin, frozenset({"heinzel"}))
    assert catalog.capture(ep, root, origin, frozenset({"heinzel"})) is captured
    try:
        assert sys.modules[ep.module].dependency.value == 42
    finally:
        sys.meta_path.remove(captured.finder)
        sys.modules.pop(ep.module, None)
        sys.modules.pop("unapproved_helper_fixture", None)


def test_opt_in_real_discovery_preserves_ordinary_compatibility_and_reuses_capture(tmp_path, monkeypatch):
    from heinzel.kernel import _discovery
    from heinzel.kernel._source_loading import ReviewedSource, SourceDiscovery

    package = "fake_discovery_source"
    root = tmp_path / package
    root.mkdir()
    entry_file = root / "__init__.py"
    entry_file.write_text(
        "from heinzel.kernel import ConnectedReadService, Module\n"
        'module = Module("fixture", "fixture")\n'
        "class Reader(ConnectedReadService): pass\n"
        "module.service(Reader)\n"
        "class Calendar:\n"
        "    def events(self, window): return []\n"
        "    def free_busy(self, people, window): return []\n"
        'module.provides("calendar", modes={"read"}, name="fixture", hosts=["fixture.invalid"])(Calendar)\n'
    )
    ep = installed_entry(root, "fixture", package)
    monkeypatch.setattr(_discovery, "entry_points", lambda **_kwargs: [ep])
    monkeypatch.syspath_prepend(str(tmp_path))
    sources = SourceDiscovery({ep.name: ReviewedSource(package, root, frozenset({"heinzel"}))})
    try:
        records, errors = _discovery.discover(source_discovery=sources)
        assert errors == {}
        again, errors = _discovery.discover(source_discovery=sources)
        assert errors == {}
        record = next(item for item in records if item.qualname == "fixture")
        assert next(item for item in again if item.qualname == "fixture") is record
        kernel = Kernel(Settings(), records, config=Config(files=(), data={"capabilities": {"calendar": "fixture"}}))
        binding = sources.binding(kernel, "calendar", ConnectedReadService)
        assert binding.record is record
        entry_file.write_text("# changed after discovery\n")
        with pytest.raises(HeinzelError, match="changed"):
            sources.binding(kernel, "calendar", ConnectedReadService)
        ordinary, _ = _discovery.discover()
        assert any(item.qualname == "fixture" for item in ordinary)
    finally:
        loaded = sys.modules.pop(package, None)
        if loaded is not None:
            finder = loaded.__spec__.loader
            if finder in sys.meta_path:
                sys.meta_path.remove(finder)


def test_mismatched_distribution_root_and_editable_install_never_capture(tmp_path):
    from heinzel.kernel._source_loading import ReviewedSource, SourceDiscovery

    installed = tmp_path / "installed_demo"
    installed.mkdir()
    (installed / "__init__.py").write_text("raise RuntimeError('initializer must not run')\n")
    entry = installed_entry(installed, "demo", "installed_demo")
    foreign = tmp_path / "unrelated"
    foreign.mkdir()
    (foreign / "__init__.py").write_text("raise RuntimeError('initializer must not run')\n")
    origin = Origin(distribution=entry.dist.name, version=entry.dist.version, builtin=False)
    mismatch = SourceDiscovery({entry.name: ReviewedSource(entry.module, foreign, frozenset())})
    assert mismatch.try_capture(entry, origin) is None
    assert entry.module not in sys.modules
    (installed / "unlisted.py").write_text("pass\n")
    missing_manifest = SourceDiscovery({entry.name: ReviewedSource(entry.module, installed, frozenset())})
    assert missing_manifest.try_capture(entry, origin) is None
    assert entry.module not in sys.modules
    (installed / "unlisted.py").unlink()
    (tmp_path / "demo-1.0.dist-info" / "direct_url.json").write_text('{"dir_info":{"editable":true}}')
    editable = SourceDiscovery({entry.name: ReviewedSource(entry.module, installed, frozenset())})
    assert editable.try_capture(entry, origin) is None
    assert entry.module not in sys.modules


def test_default_boot_denies_undeclared_preloaded_provider_but_keeps_ordinary_module(tmp_path, monkeypatch):
    from heinzel.kernel import _boot, _discovery
    from heinzel.kernel._source_loading import SourceDiscovery

    package = "undeclared_boot_provider"
    root = tmp_path / package
    root.mkdir()
    (root / "__init__.py").write_text('from heinzel.kernel import Module\nmodule = Module("fixture", "fixture")\n')
    entry = installed_entry(root, "fixture", package)
    monkeypatch.setattr(_discovery, "entry_points", lambda **_kwargs: [entry])
    monkeypatch.setattr(_boot, "_installed_sources", SourceDiscovery(installed=True))
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        loaded = importlib.import_module(package)
        kernel = _boot.boot(Settings())
        assert kernel.module("fixture").module is loaded.module
        assert kernel.source_discovery is not None
        assert not kernel.source_discovery._captured
    finally:
        sys.modules.pop(package, None)


def test_unknown_runtime_dependency_keeps_ordinary_discovery_without_verified_read(tmp_path, monkeypatch):
    from heinzel.kernel import _boot, _discovery
    from heinzel.kernel._source_loading import SourceDiscovery

    package = "extra_dependency_fixture"
    root = tmp_path / package
    root.mkdir()
    (root / "__init__.py").write_text(
        'import external_dependency_fixture\nfrom heinzel.kernel import Module\nmodule = Module("fixture", "fixture")\n'
    )
    (tmp_path / "external_dependency_fixture.py").write_text("value = 1\n")
    entry = installed_entry(root, "fixture", package, declared=True)
    monkeypatch.setattr(_discovery, "entry_points", lambda **_kwargs: [entry])
    monkeypatch.setattr(_boot, "_installed_sources", SourceDiscovery(installed=True))
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        kernel = _boot.boot(Settings())
        assert kernel.module("fixture")
        assert kernel.source_discovery is not None
        assert not kernel.source_discovery._captured
    finally:
        sys.modules.pop(package, None)
        sys.modules.pop("external_dependency_fixture", None)
