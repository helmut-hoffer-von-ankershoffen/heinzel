import importlib.util
import shutil
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


combines = _load("changelog_combines")

URL = "https://github.com/o/r"
SHA = "f31a181b450d517b41f9028b494981a39991f5e9"
OTHER = "8c618334fdd7a3ceef087fa29ec312cf6e57ba55"
# A combine body as merged: hand-written prose, wrapped member lines, a summary line and hz-combine's metadata.
BODY = """feat(web): combine of 3 web changes (#679)

Combine of 3 web member PRs, one squash commit per member.

Combines #659 feat(web): links standard, no text links in chrome
Combines #673 fix(web): name plugin settings pages properly, sort the
sidebar
Combines #668 feat(web)!: bring back Today widgets you hid

Combines #659 / #673 / #668

<!-- hz-combine area=web members=659:abc,673:def,668:123 -->
"""
SECTION = f"""# Changelog

## [0.9.33]({URL}/releases/tag/v0.9.33) - 2026-11-08

Combine of 3 web changes.

### ✨ Features

- **web:** Combine of 3 web changes ([#679]({URL}/pull/679)) ([f31a181]({URL}/commit/{SHA}))
- **chat:** Search conversations ([#645]({URL}/pull/645)) ([8c61833]({URL}/commit/{OTHER}))

## [0.9.32]({URL}/releases/tag/v0.9.32) - 2026-11-07

Combine of 2 older changes.

### ✨ Features

- **web:** Combine of 2 older changes ([#600]({URL}/pull/600)) ([f31a181]({URL}/commit/{SHA}))
"""


def test_members_join_wrapped_lines_and_skip_lines_without_a_type():
    assert combines.members(BODY) == [
        combines.Member(659, "feat", "web", breaking=False, title="links standard, no text links in chrome"),
        combines.Member(
            673, "fix", "web", breaking=False, title="name plugin settings pages properly, sort the sidebar"
        ),
        combines.Member(668, "feat", "web", breaking=True, title="bring back Today widgets you hid"),
    ]


def test_a_combine_entry_becomes_one_entry_per_member_under_its_own_type():
    bodies = {SHA: BODY, OTHER: "feat(chat): search conversations (#645)\n"}
    out = combines.expand(SECTION, "v0.9.33", bodies.__getitem__)
    released, older = out.split("## [0.9.32]")
    assert "Combine of" not in released
    assert released.index("### 💥 Breaking changes") < released.index("### ✨ Features")
    assert released.index("### ✨ Features") < released.index("### 🐛 Bug fixes")
    links = f"([#659]({URL}/pull/659), via [#679]({URL}/pull/679)) ([f31a181]({URL}/commit/{SHA}))"
    assert f"- **web:** Links standard, no text links in chrome {links}" in released
    assert "- **web:** Name plugin settings pages properly, sort the sidebar ([#673]" in released
    assert "- **web:** Bring back Today widgets you hid ([#668]" in released
    assert f"- **chat:** Search conversations ([#645]({URL}/pull/645))" in released
    assert "\nLinks standard, no text links in chrome; bring back Today widgets you hid; name plugin" in released
    assert older == SECTION.split("## [0.9.32]")[1], "other releases stay byte for byte"


def test_the_intro_takes_turns_between_combines_and_counts_the_rest():
    first = [
        combines.Member(1, "fix", None, breaking=False, title="a fix"),
        combines.Member(2, "feat", None, breaking=False, title="one"),
    ]
    second = [
        combines.Member(3, "feat", None, breaking=False, title="two"),
        combines.Member(4, "feat", None, breaking=False, title="three"),
    ]
    third = [
        combines.Member(5, "docs", None, breaking=False, title="d"),
        combines.Member(6, "feat", None, breaking=False, title="four"),
    ]
    assert combines.intro([first, second, third]) == "Two; one; three; four; and 2 more."


def test_a_written_intro_is_kept_and_a_section_without_combines_is_untouched():
    written = SECTION.replace("Combine of 3 web changes.", "Clearer links.")
    out = combines.expand(written, "0.9.33", {SHA: BODY, OTHER: "feat(chat): x\n"}.__getitem__)
    assert "\nClearer links.\n" in out
    plain = SECTION.replace(SHA, OTHER)
    assert combines.expand(plain, "0.9.33", lambda _: "feat(chat): search conversations\n") == plain
    with pytest.raises(ValueError, match=r"no section for 1\.0\.0"):
        combines.expand(SECTION, "1.0.0", lambda _: "")


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_the_script_reads_commit_bodies_from_the_repository(tmp_path):
    def git(*args: str) -> str:
        env = {
            "GIT_AUTHOR_NAME": "a",
            "GIT_AUTHOR_EMAIL": "a@b",
            "GIT_COMMITTER_NAME": "a",
            "GIT_COMMITTER_EMAIL": "a@b",
        }
        done = subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True, text=True, env=env)
        return done.stdout.strip()

    git("init", "-q")
    git("commit", "-q", "--allow-empty", "-m", BODY)
    sha = git("rev-parse", "HEAD")
    changelog = tmp_path / "CHANGELOG.md"
    entry = f"- **web:** Combine of 3 web changes ([#679]({URL}/pull/679)) ([{sha[:7]}]({URL}/commit/{sha}))"
    changelog.write_text(f"## [0.9.33]({URL}) - 2026-11-08\n\nCombine of 3.\n\n### ✨ Features\n\n{entry}\n")
    assert combines.main([str(changelog), "--version", "0.9.33", "--repo", str(tmp_path)]) == 0
    text = changelog.read_text()
    assert "- **web:** Links standard, no text links in chrome ([#659](" in text
    assert "Combine of" not in text
