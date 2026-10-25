"""The documentation site never copies a linked file from outside its source directory."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "mkdocs_hooks.py"
spec = importlib.util.spec_from_file_location("mkdocs_hooks", SCRIPT)
assert spec
assert spec.loader
hooks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hooks)


def test_site_refuses_linked_repository_sources_before_copying_them(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("A made-up private sentinel", encoding="utf-8")
    linked = docs / "linked.md"
    linked.symlink_to(outside)
    files = [SimpleNamespace(abs_src_path=str(linked))]
    with pytest.raises(ValueError, match="symlink"):
        hooks.on_files(files, {"docs_dir": str(docs)})


def test_site_keeps_regular_sources_and_generated_theme_files(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "index.md"
    source.write_text("# Example", encoding="utf-8")
    files = [SimpleNamespace(abs_src_path=str(source)), SimpleNamespace(abs_src_path=None)]
    assert hooks.on_files(files, {"docs_dir": str(docs)}) is files
