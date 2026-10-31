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


def test_the_apps_words_may_change_but_not_its_code():
    def words(line):
        return ship.Change("web/src/i18n.ts", "M", added=[(5, line)], removed=[(5, '  "a.b": "old",')])

    assert lane(words('  "plugins.firstParty": "First-party",')) == []
    assert lane(words('    "Reporting lines are guessed from your calendar. Connect one in Settings.",')) == []
    assert lane(words('  "nav.artifacts":')) == []
    assert lane(words('  "a.b": fetch("/x"),'))
    assert lane(words("  const x = 1;"))


@pytest.mark.parametrize(
    "line",
    [
        '"plugins.firstParty": "First-party",',
        '  "nav.team": "Team roster"',
        '    "Reporting lines are guessed from your calendar. Connect one in Settings.",',
        '  "say.quote": "She said \\"hi\\" twice",',
        '  "nav.artifacts":',
        "",
    ],
)
def test_i18n_entries_that_are_plain_strings_match(line):
    assert ship.I18N_ENTRY.fullmatch(line.strip())


@pytest.mark.parametrize(
    "line",
    [
        '  "a.b": fetch("/x"),',
        "const x = 1;",
        '"a": "b" + c,',
        '"a": "unterminated',
        'import "a": "b"',
        '"a": "b", "c": "d",',
    ],
)
def test_i18n_lines_with_code_do_not_match(line):
    assert not ship.I18N_ENTRY.fullmatch(line.strip())


@pytest.mark.parametrize(
    "line",
    [
        "import x from 'y';",
        "  import type { A } from './a';",
        "export { a } from './b';",
        "export * as ns from './c';",
        "const m = require('x');",
        "import('lazy')",
    ],
)
def test_import_lines_match(line):
    assert ship.IMPORT.search(line)


@pytest.mark.parametrize(
    "line", ["<p>important</p>", "const exported = 1;", "// a required( field", "export const A = 1;"]
)
def test_ordinary_lines_are_not_imports(line):
    assert not ship.IMPORT.search(line)


def test_the_line_patterns_stay_fast_on_hostile_input():
    import time

    hostile = ['"' + "\\" * 20000, '"a": "' + "x" * 50000, "export" + " " * 50000, " " * 50000 + "x", "\\" * 50000]
    start = time.monotonic()
    for text in hostile:
        ship.I18N_ENTRY.fullmatch(text.strip())
        ship.IMPORT.search(text)
    assert time.monotonic() - start < 1.0


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


def test_no_snippet_for_a_badge_only_change():
    badge = '<a href="https://example.test/x"><img src="https://example.test/y.svg" alt="b"></a>'
    assert ship.snippet(ship.Change("docs/a.md", "M", added=[(1, badge)], removed=[(1, "old")])) == ""


def test_pages_of_documents_on_the_site():
    assert ship.page_url("docs/guides/install.md") == ship.site_url() + "guides/install/"
    assert ship.page_url("docs/README.md") == ship.site_url()
    assert ship.page_url("docs/guides/README.md") == ship.site_url() + "guides/"
    assert ship.page_url("web/src/index.css") is None


def test_titles_follow_commitizen():
    assert ship.title_problem("docs(docs): fix a typo in the install guide") is None
    assert ship.title_problem("Fixed a typo") is not None
    assert ship.title_problem("docs(unknown): x") is not None


def test_a_rejected_title_lists_the_scopes_and_the_docs_scope():
    message = ship.title_problem("docs(teams): x")
    assert "docs, adr" in message
    assert "docs(docs)" in message


def test_web_checks_run_biome_first_then_types_and_related_tests_together():
    changes = {
        "web/src/components/A.tsx": ship.Change("web/src/components/A.tsx", "M"),
        "docs/TEAMS.md": ship.Change("docs/TEAMS.md", "M"),
    }
    steps = ship.web_steps(changes)
    assert [step.split("=", 1)[0] for step in steps] == ["biome", "types", "&tests"]
    assert "src/components/A.tsx" in steps[0]
    assert "docs/TEAMS.md" not in " ".join(steps)
    assert ship.web_steps({"docs/TEAMS.md": ship.Change("docs/TEAMS.md", "M")}) == []


def test_markdown_gets_the_tests_that_read_documents_and_a_failing_one_stops_the_change(tmp_path, monkeypatch, capsys):
    steps = ship.steps_for({"docs/TEAMS.md": ship.Change("docs/TEAMS.md", "M")})
    assert [name for name, _ in steps] == ["confidential", "typos", "generated", "doc-tests"]
    monkeypatch.setattr(ship, "state_file", lambda: tmp_path / "ship.json")
    monkeypatch.setattr(ship, "branch", lambda: "docs/x")
    monkeypatch.setattr(ship, "root", lambda: tmp_path)
    with pytest.raises(SystemExit) as stopped:
        ship.fail_step(
            "doc-tests", "E   AssertionError: the old sentence\nFAILED tests/unit/pypi_description_test.py::t"
        )
    assert stopped.value.code == 3
    assert "asserts the text you changed" in json.loads(capsys.readouterr().out.splitlines()[0])["msg"]


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


def not_small_state(tmp_path, monkeypatch, changes):
    """A change stopped as not small; `changes` is what the working tree holds afterwards."""
    monkeypatch.setattr(ship, "state_file", lambda: tmp_path / "ship.json")
    monkeypatch.setattr(ship, "branch", lambda: "docs/x")
    monkeypatch.setattr(ship, "root", lambda: tmp_path)
    monkeypatch.setattr(ship, "collect", lambda head=None: changes)
    ship.save(
        {"failures": [], "stopped": True, "why": "Not a small change: x.", "kind": "not-small", "stopped_at": "old"}
    )


def test_a_not_small_stop_clears_when_the_diff_becomes_small(tmp_path, monkeypatch):
    small = {"docs/guides/g.md": ship.Change("docs/guides/g.md", "M", added=[(1, "x y z")], removed=[(1, "a")])}
    not_small_state(tmp_path, monkeypatch, small)
    ship.refuse_if_stopped("classify")  # no exit: the diff changed and is small now
    assert ship.load()["stopped"] is False


def test_a_not_small_stop_stays_while_the_diff_is_unchanged_or_still_not_small(tmp_path, monkeypatch):
    big = {"src/heinzel/a.py": ship.Change("src/heinzel/a.py", "M", added=[(1, "x")])}
    not_small_state(tmp_path, monkeypatch, big)
    with pytest.raises(SystemExit) as stopped:
        ship.refuse_if_stopped("classify")
    assert stopped.value.code == 3
    assert ship.load()["stopped"] is True
    same = {"docs/guides/g.md": ship.Change("docs/guides/g.md", "M", added=[(1, "x y z")], removed=[(1, "a")])}
    not_small_state(tmp_path, monkeypatch, same)
    ship.save({**ship.load(), "stopped_at": ship.signature(same)})
    with pytest.raises(SystemExit):
        ship.refuse_if_stopped("classify")


def test_a_failure_stop_never_clears_itself(tmp_path, monkeypatch):
    small = {"docs/guides/g.md": ship.Change("docs/guides/g.md", "M", added=[(1, "x y z")], removed=[(1, "a")])}
    not_small_state(tmp_path, monkeypatch, small)
    ship.save({**ship.load(), "kind": "failure"})
    with pytest.raises(SystemExit) as stopped:
        ship.refuse_if_stopped("classify")
    assert stopped.value.code == 3


@pytest.mark.parametrize("stage", sorted(ship.STAGES))
def test_every_stage_prints_its_own_usage(stage, capsys):
    with pytest.raises(SystemExit) as helped:
        ship.parser().parse_args([stage, "--help"])
    assert helped.value.code == 0
    out = capsys.readouterr().out
    assert ship.STAGES[stage][0] in out
    assert "Example:" in out
    assert "Exit codes:" in out


def pull(**fields):
    """A `gh pr view` answer for a ready, labelled, green, clean pull request, with fields replaced."""
    data = {
        "labels": [{"name": "risk: low"}],
        "state": "OPEN",
        "isDraft": False,
        "headRefOid": "abc123def456",
        "mergeStateStatus": "CLEAN",
        "mergeable": "MERGEABLE",
        "statusCheckRollup": [{"name": "CI OK", "status": "COMPLETED", "conclusion": "SUCCESS"}],
    }
    return {**data, **fields}


def merger(monkeypatch, tmp_path, data, changes, orchestrator=False):
    """Run `merge 7` against a fake GitHub; returns the commands sent to `gh pr merge`."""
    sent = []

    def fake_gh(*args):
        if args[:2] == ("pr", "merge"):
            sent.append(args)
            return 0, "merged"
        return 0, json.dumps(data)

    monkeypatch.setattr(ship, "gh", fake_gh)
    monkeypatch.setattr(ship, "slug_repo", lambda: "o/r")
    monkeypatch.setattr(ship, "root", lambda: tmp_path)
    monkeypatch.setattr(ship, "run", lambda *a, **k: (0, ""))
    monkeypatch.setattr(ship, "git", lambda *a, **k: data["headRefOid"])
    monkeypatch.setattr(ship, "collect", lambda head=None: changes)
    monkeypatch.setattr(ship, "ORCHESTRATOR", orchestrator)
    monkeypatch.setattr(ship, "state_file", lambda: tmp_path / "ship.json")
    monkeypatch.setattr(ship, "branch", lambda: "docs/x")
    return sent


SMALL = {"docs/guides/g.md": ship.Change("docs/guides/g.md", "M", added=[(1, "x y z")], removed=[(1, "a")])}
BIG = {"src/heinzel/a.py": ship.Change("src/heinzel/a.py", "M", added=[(1, "x")])}


def merge(args=7):
    return ship.stage_merge(ship.argparse.Namespace(pr=args))


def test_a_green_low_pull_request_merges_without_the_orchestrator_at_its_exact_head(monkeypatch, tmp_path, capsys):
    sent = merger(monkeypatch, tmp_path, pull(), SMALL)
    merge()
    assert sent == [("pr", "merge", "7", "-R", "o/r", "--squash", "--match-head-commit", "abc123def456")]
    assert json.loads(capsys.readouterr().out.splitlines()[0])["ok"] is True


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (pull(labels=[]), 2),
        (pull(isDraft=True), 2),
        (pull(state="MERGED"), 2),
        (pull(mergeStateStatus="BEHIND"), 4),
        (pull(mergeStateStatus="BLOCKED"), 4),
        (pull(statusCheckRollup=[{"name": "CI OK", "status": "IN_PROGRESS", "conclusion": None}]), 4),
        (pull(statusCheckRollup=[{"name": "CI OK", "status": "COMPLETED", "conclusion": "FAILURE"}]), 4),
        (pull(statusCheckRollup=[]), 4),
    ],
)
def test_a_pull_request_that_is_not_ready_is_not_merged(monkeypatch, tmp_path, data, code):
    sent = merger(monkeypatch, tmp_path, data, SMALL)
    with pytest.raises(SystemExit) as stopped:
        merge()
    assert stopped.value.code == code
    assert sent == []


def test_a_labelled_pull_request_whose_diff_is_not_small_is_never_merged_here(monkeypatch, tmp_path, capsys):
    sent = merger(monkeypatch, tmp_path, pull(), BIG, orchestrator=True)
    with pytest.raises(SystemExit) as stopped:
        merge()
    assert stopped.value.code == 3
    assert sent == []
    assert json.loads(capsys.readouterr().out.splitlines()[0])["lane"] == "escalate"


def test_reset_keeps_the_pull_request_record(tmp_path, monkeypatch):
    monkeypatch.setattr(ship, "state_file", lambda: tmp_path / "ship.json")
    monkeypatch.setattr(ship, "branch", lambda: "docs/x")
    monkeypatch.setattr(ship, "root", lambda: tmp_path)
    monkeypatch.setattr(ship, "ORCHESTRATOR", True)
    ship.save({"failures": [{"stage": "wait"}], "stopped": True, "pr": 12, "url": "u", "checked": "sig"})
    ship.cmd_reset(ship.argparse.Namespace())
    state = ship.load()
    assert (state["pr"], state["url"], state["checked"]) == (12, "u", "sig")
    assert state["failures"] == []
    assert state["stopped"] is False


@pytest.mark.parametrize("stage", sorted(ship.STAGES))
def test_every_argument_of_every_stage_is_described(stage):
    sub = next(a for a in ship.parser()._actions if a.dest == "command")
    for action in sub.choices[stage]._actions:
        assert action.help, f"{stage}: {action.dest} has no help text"
