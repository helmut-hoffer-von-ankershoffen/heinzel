import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


changelog_intros = _load("changelog_intros")
release_notes = _load("release_notes")

HEADER = "# Changelog\n\nAll notable changes.\n"
V010 = "## [0.1.0](https://example.com/v0.1.0) - 2026-10-23\n"
V020 = "## [0.2.0](https://example.com/v0.2.0) - 2026-11-01\n"
INTRO_010 = "The first release: a kernel and modules.\nMore on the [roadmap](https://example.com/roadmap)."

# CHANGELOG.md as released in 0.1.0, after the maintainer wrote the introduction in the release pull request.
BEFORE = f"{HEADER}\n{V010}\n{INTRO_010}\n\n### ✨ Features\n\n- Foundation\n"
# What `git-cliff --tag v0.2.0` writes when cutting 0.2.0: every release from the commits, no introductions.
REGENERATED = f"{HEADER}\n{V020}\n### 🐛 Bug fixes\n\n- A fix\n\n{V010}\n### ✨ Features\n\n- Foundation\n"


def test_reads_the_introductions_under_release_headings():
    assert changelog_intros.intros(BEFORE) == {"0.1.0": INTRO_010}
    assert changelog_intros.intros(REGENERATED) == {}


def test_an_introduction_survives_regenerating_the_changelog_for_the_next_release():
    restored = changelog_intros.restore(REGENERATED, changelog_intros.intros(BEFORE))
    assert (
        restored
        == f"{HEADER}\n{V020}\n### 🐛 Bug fixes\n\n- A fix\n\n{V010}\n{INTRO_010}\n\n### ✨ Features\n\n- Foundation\n"
    )
    assert release_notes.section(restored, "v0.1.0").startswith(INTRO_010 + "\n\n### ✨ Features")


def test_restoring_twice_changes_nothing():
    kept = changelog_intros.intros(BEFORE)
    once = changelog_intros.restore(REGENERATED, kept)
    assert changelog_intros.restore(once, kept) == once
    assert changelog_intros.restore(once, changelog_intros.intros(once)) == once


def test_a_heading_without_an_introduction_gets_one_blank_line_not_git_cliffs_two():
    generated = f"{HEADER}\n{V020}\n\n### 🐛 Bug fixes\n\n- A fix\n"
    assert changelog_intros.restore(generated, {}) == f"{HEADER}\n{V020}\n### 🐛 Bug fixes\n\n- A fix\n"


def test_a_release_without_sections_keeps_its_introduction():
    kept = {"0.1.0": "Only prose."}
    assert changelog_intros.restore(f"{HEADER}\n{V010}", kept) == f"{HEADER}\n{V010}\nOnly prose.\n"


def test_the_release_script_writes_the_new_release_introduction(tmp_path):
    previous, changelog = tmp_path / "previous.md", tmp_path / "CHANGELOG.md"
    previous.write_text(BEFORE, encoding="utf-8")
    changelog.write_text(REGENERATED, encoding="utf-8")
    intro = "Heinzelmännchen now fixes things.\n"
    assert changelog_intros.main([str(previous), str(changelog), "--version", "v0.2.0", "--intro", intro]) == 0
    text = changelog.read_text(encoding="utf-8")
    assert release_notes.section(text, "0.2.0") == "Heinzelmännchen now fixes things.\n\n### 🐛 Bug fixes\n\n- A fix\n"
    assert INTRO_010 in release_notes.section(text, "0.1.0")


def test_without_an_intro_or_a_previous_changelog_the_regenerated_one_stays(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(REGENERATED, encoding="utf-8")
    assert (
        changelog_intros.main([str(tmp_path / "absent.md"), str(changelog), "--version", "0.2.0", "--intro", ""]) == 0
    )
    assert changelog.read_text(encoding="utf-8") == REGENERATED


def test_an_intro_needs_a_version(tmp_path):
    with pytest.raises(SystemExit):
        changelog_intros.main([str(tmp_path / "a.md"), str(tmp_path / "b.md"), "--intro", "Prose."])
