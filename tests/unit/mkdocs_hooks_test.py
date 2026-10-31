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


def _adr(directory, number, name, **front):
    meta = "".join(f"{key}: {value}\n" for key, value in front.items())
    (directory / f"{number}-{name}.md").write_text(
        f"---\nstatus: proposed\ndate: 2026-10-24\n{meta}---\n\n# {number}: Title {name}\n", encoding="utf-8"
    )


def test_adr_relations_are_rendered_on_both_sides_with_the_amending_reason(tmp_path):
    _adr(tmp_path, "0001", "old", **{"amended-by": "0002"})
    _adr(tmp_path, "0002", "new", **{"amends": "0001", "amends-0001": "what it changes", "implemented-by": "7, 9"})
    records = hooks.adr_records(tmp_path)
    table = hooks.adr_index_table(records, "https://example.test/r")
    assert (
        '| [0001](0001-old.md) Title old | proposed · 24 Oct |  | amended by [0002](0002-new.md "what it changes") |'
        in table
    )
    assert "[#7](https://example.test/r/pull/7), [#9](https://example.test/r/pull/9)" in table
    assert "- **Amended by** [0002](0002-new.md) (what it changes)" in hooks.adr_page_block(records, "0001", "u")
    assert "- **Implemented by** [#7](u/pull/7), [#9](u/pull/9)" in hooks.adr_page_block(records, "0002", "u")
    assert hooks.adr_page_block({"0003": {"file": "x.md"}}, "0003", "u") == ""
