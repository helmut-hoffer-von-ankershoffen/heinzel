import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ship.py"
spec = importlib.util.spec_from_file_location("ship", SCRIPT)
assert spec
assert spec.loader
ship = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ship)

PROSE = "# Title\n\nSome text with a typo.\n\n```sh\nhz --help\n```\n\nSee [the guide](other.md) and `hz ui`.\n"


def edit(path: str, old: str, new: str, text: str = PROSE) -> object:
    """A modified file as `collect` would build it: line 3 (or the first line holding `old`) replaced."""
    before = text.splitlines()
    index = next(i for i, line in enumerate(before) if old in line)
    after = [*before[:index], before[index].replace(old, new), *before[index + 1 :]]
    return ship.Change(
        path,
        "M",
        added=[(index + 1, after[index])],
        removed=[(index + 1, before[index])],
        old_text=text,
        new_text="\n".join(after),
    )


def lane(*changes) -> list[str]:
    return ship.classify_changes({c.path: c for c in changes})


def test_a_typo_fix_in_a_guide_is_low():
    assert lane(edit("docs/guides/install.md", "typo", "mistake")) == []


@pytest.mark.parametrize(
    "path",
    [
        "src/heinzel/kernel/_kernel.py",
        "src/heinzel/modules/system/updates.py",
        "tests/unit/x_test.py",
        ".github/workflows/ci.yml",
        "scripts/pr.sh",
        "Makefile",
        "pyproject.toml",
        "CHANGELOG.md",
        "docs/adr/0001-x.md",
        "docs/MODULES.md",
        "docs/plugins/heinzel-clock/index.md",
        "docs/SONAR_SECURITY.md",
        "docs/DESKTOP_PRODUCTION_GATES.md",
        "web/src/api/schema.d.ts",
        "web/package.json",
        "web/src/chat/Composer.tsx",
        "web/src/components/AuthPanel.tsx",
        "web/src/pages/Sessions.tsx",
        "web/src/layout/UpdatePane.tsx",
        "desktop/main.ts",
        ".claude/settings.json",
    ],
)
def test_sensitive_and_unlisted_paths_escalate(path):
    assert ship.path_reason(path)


@pytest.mark.parametrize(
    "path",
    [
        "docs/guides/install.md",
        "docs/TEAMS.md",
        "README.md",
        "web/src/components/Badge.tsx",
        "docs/overrides/home.html",
    ],
)
def test_small_locations_pass(path):
    assert ship.path_reason(path) is None


def test_changing_a_code_block_escalates():
    reasons = lane(edit("docs/guides/install.md", "hz --help", "hz --version"))
    assert any("code block" in r for r in reasons)


def test_changing_inline_code_escalates():
    reasons = lane(edit("docs/guides/install.md", "`hz ui`", "`hz web`"))
    assert any("inline code" in r for r in reasons)


def test_a_new_external_link_escalates_but_a_relative_one_does_not():
    assert lane(edit("docs/guides/install.md", "other.md", "third.md")) == []
    reasons = lane(edit("docs/guides/install.md", "typo", "typo, see https://example.com/x"))
    assert any("external link" in r for r in reasons)


def test_new_deleted_and_binary_files_escalate():
    added = ship.Change("docs/guides/new.md", "A", added=[(1, "text")])
    deleted = ship.Change("docs/guides/old.md", "D", removed=[(1, "text")])
    binary = ship.Change("docs/guides/pic.md", "M", binary=True)
    assert lane(added)
    assert lane(deleted)
    assert lane(binary)


def test_code_changes_flag_network_security_and_imports():
    base = "export const A = () => <p>Hi</p>;\n"
    for new in ("fetch('/x')", "localStorage.clear()", "import x from 'y'", "<a href='https://x.test'>"):
        change = ship.Change("web/src/components/A.tsx", "M", added=[(1, new)], removed=[(1, base)], new_text=new)
        assert lane(change), new
    ok = ship.Change("web/src/components/A.tsx", "M", added=[(1, "<p>Hello</p>")], removed=[(1, base)])
    assert lane(ok) == []


def test_size_limits():
    many = [ship.Change(f"docs/guides/g{i}.md", "M", added=[(1, "x")], removed=[(1, "y")]) for i in range(9)]
    assert any("files" in r for r in lane(*many))
    big = ship.Change("docs/guides/g.md", "M", added=[(i, "x") for i in range(81)], removed=[])
    assert any("changed lines" in r for r in lane(big))


def test_derived_readme_template_follows_the_readme():
    readme = edit("README.md", "typo", "mistake")
    derived = ship.Change("template/README.md.jinja", "M", added=[(1, "x")], removed=[(1, "y")])
    assert lane(readme, derived) == []
    assert lane(derived)


def test_parse_diff_numbers_the_lines():
    patch = (
        "diff --git a/docs/a.md b/docs/a.md\nindex 1..2 100644\n--- a/docs/a.md\n+++ b/docs/a.md\n"
        "@@ -3 +3 @@\n-old\n+new\n@@ -9,0 +10,2 @@\n+a\n+b\n"
        "diff --git a/docs/b.bin b/docs/b.bin\nnew file mode 100644\nBinary files /dev/null and b/docs/b.bin differ\n"
    )
    changes = ship.parse_diff(patch)
    assert changes["docs/a.md"].added == [(3, "new"), (10, "a"), (11, "b")]
    assert changes["docs/a.md"].removed == [(3, "old")]
    assert changes["docs/b.bin"].binary
    assert changes["docs/b.bin"].status == "A"


def test_snippet_shows_the_words_around_the_change():
    change = ship.Change(
        "docs/a.md",
        "M",
        added=[(1, "Operations have typed inputs and **outputs** in every head")],
        removed=[(1, "Operations have typed inputs and output in every head")],
    )
    assert "outputs" in ship.snippet(change)


def test_titles_follow_commitizen():
    assert ship.title_problem("docs(docs): fix a typo in the install guide") is None
    assert ship.title_problem("Fixed a typo") is not None
    assert ship.title_problem("docs(unknown): x") is not None


def test_the_second_failure_stops_the_change(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ship, "state_file", lambda: tmp_path / "ship.json")
    monkeypatch.setattr(ship, "branch", lambda: "docs/x")
    monkeypatch.setattr(ship, "root", lambda: tmp_path)
    with pytest.raises(SystemExit) as first:
        ship.fail("check", "typos failed.", "Fix it", ["a.md:1"])
    assert first.value.code == 1
    assert json.loads(capsys.readouterr().out.splitlines()[0])["failures"] == "1 of 2"
    with pytest.raises(SystemExit) as second:
        ship.fail("check", "typos failed.", "Fix it")
    assert second.value.code == 3
    line = json.loads(capsys.readouterr().out.splitlines()[0])
    assert line["stop"] is True
    assert "Run nothing else" in line["msg"]
    with pytest.raises(SystemExit) as later:
        ship.refuse_if_stopped("pr")
    assert later.value.code == 3
