import importlib.util
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "release_notes.py"
spec = importlib.util.spec_from_file_location("release_notes", SCRIPT)
assert spec
assert spec.loader
release_notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_notes)

CHANGELOG = """# Changelog

Intro.

## [0.2.0](https://example.com/v0.2.0) - 2026-11-01

### ✨ Features

- New thing

## [0.1.0](https://example.com/v0.1.0) - 2026-10-23

### 🐛 Bug fixes

- Old fix
"""


def test_extracts_the_section_of_a_version():
    assert release_notes.section(CHANGELOG, "v0.2.0") == "### ✨ Features\n\n- New thing\n"
    assert release_notes.section(CHANGELOG, "0.1.0") == "### 🐛 Bug fixes\n\n- Old fix\n"


def test_unknown_versions_fail():
    with pytest.raises(ValueError, match=r"no section for v9\.9\.9"):
        release_notes.section(CHANGELOG, "v9.9.9")


def test_commits_of_the_tag_missing_from_the_section_are_listed():
    notes = release_notes.section(CHANGELOG, "v0.2.0") + "- Merged during review (#885)\n"
    subjects = [
        "fix: late change (#886)",
        "feat: Merged during review (#885)",
        "chore(release): v0.2.0",
        "docs: no number",
    ]
    text = release_notes.with_missing(notes, subjects)
    assert text.endswith("- fix: late change (#886)\n- docs: no number\n")
    assert "### Also in this release" in text
    assert text.count("#885") == 1


def test_nothing_missing_leaves_the_notes_unchanged():
    notes = "- one (#1)\n"
    assert release_notes.with_missing(notes, ["feat: one (#1)"]) == notes


def test_every_commit_between_the_previous_tag_and_the_tag_is_listed(tmp_path, monkeypatch):
    import subprocess

    for name in [
        n for n in os.environ if n.startswith("GIT_")
    ]:  # a git hook sets GIT_DIR: the test repo must be its own
        monkeypatch.delenv(name)

    def git(*args):
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@example.com",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "tag.gpgsign=false",
                *args,
            ],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    for subject, tag in (
        ("feat: first (#1)", "v0.1.0"),
        ("fix: in the candidate (#886)", None),
        ("chore(release): v0.2.0", "v0.2.0"),
    ):
        git("commit", "-q", "--allow-empty", "-m", subject)
        if tag:
            git("tag", tag)
    monkeypatch.chdir(tmp_path)
    assert release_notes.tag_subjects("v0.2.0") == ["chore(release): v0.2.0", "fix: in the candidate (#886)"]
    git("tag", "v0.2.0-rc1", "HEAD~1")  # a prerelease tag between the releases is not the previous release
    git("tag", "edge", "HEAD~1")
    assert release_notes.tag_subjects("v0.2.0") == ["chore(release): v0.2.0", "fix: in the candidate (#886)"]
    assert release_notes.tag_subjects("v0.1.0") == []  # no earlier tag: nothing to add
    assert "- fix: in the candidate (#886)" in release_notes.with_missing(
        "- other\n", release_notes.tag_subjects("v0.2.0")
    )
