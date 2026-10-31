"""The ADR index and the ADR pages are rendered from the records' front matter."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "adr_index.py"
spec = importlib.util.spec_from_file_location("adr_index", SCRIPT)
assert spec
assert spec.loader
hooks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hooks)


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


def test_the_hook_adds_the_block_under_the_title_of_an_adr_page_only(tmp_path):
    adr_dir = tmp_path / "adr"
    adr_dir.mkdir()
    _adr(adr_dir, "0001", "old", **{"amended-by": "0002"})
    _adr(adr_dir, "0002", "new", **{"amends": "0001", "amends-0001": "why"})
    page = SimpleNamespace(file=SimpleNamespace(abs_src_path=str(adr_dir / "0001-old.md")))
    text = "# 0001: Title old\n\nBody\n"
    shown = hooks.on_page_markdown(text, page=page, config={"repo_url": "https://example.test/r"})
    assert shown.startswith('# 0001: Title old\n\n<div class="adr-relations"')
    assert "**Amended by** [0002](0002-new.md) (why)" in shown
    other = SimpleNamespace(file=SimpleNamespace(abs_src_path=str(tmp_path / "GUIDE.md")))
    assert hooks.on_page_markdown("# G\n", page=other, config={"repo_url": "x"}) == "# G\n"
