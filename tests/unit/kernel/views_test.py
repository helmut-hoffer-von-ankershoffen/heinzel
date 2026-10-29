"""View declarations never require a named provider or unsafe file access."""

from dataclasses import replace

import pytest

from heinzel import Module, ViewLet
from heinzel.kernel import read_view_asset

pytestmark = pytest.mark.unit


@pytest.fixture
def view(tmp_path):
    (tmp_path / "index.js").write_text("export default sdk => () => null;")
    return ViewLet(name="inspector", directory=tmp_path, source="read", title="Inspector", fallback="Use the CLI.")


def test_module_declares_a_provider_independent_local_factory(view):
    module = Module("arbitrary", "An arbitrary provider.")
    declared = module.view(
        view.directory, abi=1, name=view.name, source=view.source, title=view.title, fallback=view.fallback
    )
    assert declared in module.lets
    assert read_view_asset(declared, "index.js").startswith(b"export default")
    assert len(declared.fingerprint) == 64


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", "a/../../secret", ".hidden", "missing.js"])
def test_asset_reads_refuse_undeclared_paths(view, path):
    with pytest.raises(ValueError, match=r"No declared|View|view|Unsupported"):
        read_view_asset(view, path)


def test_asset_reads_reject_modified_files(view):
    (view.directory / "index.js").write_text("changed")
    with pytest.raises(ValueError, match="changed"):
        read_view_asset(view, "index.js")


def test_asset_reads_do_not_follow_a_replacement_symlink(view, tmp_path):
    target = tmp_path / "other.js"
    target.write_text("outside the declared bundle")
    (view.directory / "index.js").unlink()
    (view.directory / "index.js").symlink_to(target)
    with pytest.raises(OSError, match=r"Too many levels|Symbolic link"):
        read_view_asset(view, "index.js")


def test_incompatible_view_abi_does_not_remove_python_operations(view):
    future = replace(view, abi=999)
    assert future.abi == 999


@pytest.mark.parametrize(
    "change",
    [
        {"name": "../bad"},
        {"source": "../bad"},
        {"slot": "unknown"},
        {"refresh": 0},
        {"order": -1},
        {"order": 1.5},
        {"refresh": True},
        {"abi": 1.5},
        {"entry": "index.html"},
        {"title": "\x1bunsafe"},
    ],
)
def test_invalid_declaration_metadata_is_rejected(view, change):
    with pytest.raises(ValueError, match=r"No declared|View|view|Unsupported"):
        replace(view, **change)


def test_an_editable_plugin_can_supply_its_bundle_after_python_discovery(tmp_path):
    bundle = tmp_path / "built"
    declared = ViewLet(name="view", directory=bundle, source="read", title="View", fallback="Use the CLI.")
    assert declared.files == ()
    bundle.mkdir()
    (bundle / "index.js").write_text("export default sdk => () => null;")
    assert declared.prepared().files


@pytest.mark.parametrize(
    "kind", ["file-link", "directory-link", "oversize", "file-count", "total-size", "missing-entry"]
)
def test_broken_bundle_has_a_local_diagnostic_without_losing_python_operations(tmp_path, monkeypatch, kind):
    from heinzel.kernel import _views

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    entry = bundle / "index.js"
    entry.write_text("export default sdk => () => null")
    if kind == "file-link":
        entry.unlink()
        entry.symlink_to(tmp_path / "outside")
    elif kind == "directory-link":
        (bundle / "assets").symlink_to(tmp_path, target_is_directory=True)
    elif kind == "oversize":
        monkeypatch.setattr(_views, "MAX_VIEW_FILE", 8)
    elif kind == "file-count":
        monkeypatch.setattr(_views, "MAX_VIEW_FILES", 1)
        (bundle / "other.js").write_text("another module")
    elif kind == "total-size":
        monkeypatch.setattr(_views, "MAX_VIEW_TOTAL", 8)
    else:
        entry.unlink()
    module = Module("unforeseen", "A provider.")

    @module.operation
    def read() -> int:
        """Read a value even if the optional view was built incorrectly."""
        return 42

    declaration = module.view(bundle, abi=1, name="view", source="read", title="View", fallback="Use the CLI.")
    assert declaration.diagnostic
    assert not declaration.files
    assert read() == 42


def test_page_routes_and_actions_are_explicit_and_bounded(view):
    from heinzel import Navigate

    with pytest.raises(ValueError, match="declare its route"):
        replace(view, slot="page")
    with pytest.raises(ValueError, match="fixed local"):
        replace(view, slot="page", route="//outside.example")
    with pytest.raises(ValueError, match="eight named"):
        replace(view, actions={"../invalid": Navigate(path="/")})
    with pytest.raises(ValueError, match="eight named"):
        replace(view, actions={f"action-{index}": Navigate(path="/") for index in range(9)})
    page = replace(view, slot="page", route="/provider", actions={"home": Navigate(path="/")})
    assert page.route == "/provider"
