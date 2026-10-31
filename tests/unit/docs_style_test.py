"""The docs style check (scripts/check_docs.py): each rule on a small fixture, and the repository itself."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check_docs.py"
spec = importlib.util.spec_from_file_location("check_docs", SCRIPT)
assert spec is not None
assert spec.loader is not None
check_docs = importlib.util.module_from_spec(spec)
sys.modules["check_docs"] = check_docs
spec.loader.exec_module(check_docs)

TREE = {"heinzel": {"add": {}, "list": {}}, "system": {"doctor": {}}}


def rules(tmp_path: Path, name: str, text: str, tree: dict | None = None) -> list[str]:
    """The rules that fire on one document."""
    root = tmp_path / f"repo{len(list(tmp_path.iterdir()))}"
    path = root / name
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    return [f.rule for f in check_docs.collect(root, TREE if tree is None else tree)]


@pytest.mark.parametrize("word", ["simply", "just", "seamless", "powerful", "robust", "leverage", "in order to"])
def test_filler_fails(tmp_path, word) -> None:
    assert rules(tmp_path, "INTENT.md", f"We {word} do it.\n") == ["filler"]


def test_just_in_time_and_code_are_fine(tmp_path):
    assert rules(tmp_path, "INTENT.md", "A just-in-time fix and `simply` in code.\n") == []


def test_claims_only_in_their_scope(tmp_path):
    assert rules(tmp_path, "docs/guides/a.md", "It sends mail.\n") == ["claims"]
    assert rules(tmp_path, "docs/ARCHITECTURE.md", "It sends mail.\n") == []


def test_residue_and_generated_files(tmp_path):
    assert rules(tmp_path, "docs/A.md", "A retrofit and a migration, see PR #3.\n") == ["residue"] * 3
    assert rules(tmp_path, "docs/CLI_REFERENCE.md", "A migration.\n") == []


def test_dates_ignore_front_matter_and_code(tmp_path):
    assert rules(
        tmp_path, "docs/A.md", "---\ndate: 2026-01-02\n---\nOn 2026-01-02 it ran.\n```\n2026-01-03\n```\n"
    ) == ["date"]


def test_versions_in_scope_only(tmp_path):
    assert rules(
        tmp_path, "docs/guides/a.md", "Install 0.1.0 now; not 10.0.0.1.\n```sh\nuv tool install x==0.2.0\n```\n"
    ) == [
        "version",
        "version",
    ]
    assert rules(tmp_path, "docs/A.md", "Version 0.1.0.\n") == []


def test_install_commands_do_not_pin(tmp_path):
    commit = "a" * 40
    text = f"```sh\nhz plugin add x@{commit}\nhz plugin add y#subdirectory=z\n```\n```sh\ngit checkout {commit}\n```\n"
    assert rules(tmp_path, "docs/A.md", text, {"plugin": {"add": {}}}) == ["install", "install"]


def test_wide_tables_warn(tmp_path):
    wide = "| a | b | c | d | e |\n| - | - | - | - | - |\n| 1 | 2 | 3 | 4 | 5 |\n"
    path = tmp_path / "README.md"
    path.write_text(wide, encoding="utf-8")
    findings = check_docs.collect(tmp_path, TREE)
    assert [(f.rule, f.warning) for f in findings] == [("table", True)]


def test_paragraph_length(tmp_path):
    def sentences(n: int) -> str:
        return " ".join(f"Sentence {i} says a thing." for i in range(n)) + "\n"

    (tmp_path / "docs").mkdir()
    for n in (3, 4, 6):
        (tmp_path / "docs" / f"p{n}.md").write_text(sentences(n), encoding="utf-8")
    (tmp_path / "docs" / "list.md").write_text("- " + sentences(8), encoding="utf-8")
    found = {f.path: f.warning for f in check_docs.collect(tmp_path, TREE)}
    assert found == {"docs/p4.md": True, "docs/p6.md": False}


def test_stale_commands(tmp_path):
    text = (
        "Run `hz heinzel add my-name`, `hz heinzel gone`, `hz system doctor --json` and `hz nope`.\n"
        "```sh\nhz heinzel list\nhz old thing\n```\n"
    )
    assert rules(tmp_path, "docs/A.md", text) == ["command"] * 3


def test_pipes_in_rendered_pages(tmp_path):
    (tmp_path / "a.html").write_text(
        "<html><head><title>a | b | c</title></head><body><p>| x | y |</p><code>| p | q |</code>"
        "<table><tr><td>| m | n |</td></tr></table><p>one | pipe</p></body></html>",
        encoding="utf-8",
    )
    assert [f.rule for f in check_docs.site_findings(tmp_path)] == ["pipes"]


def test_copy_findings_read_strings_and_descriptions(tmp_path):
    (tmp_path / "web/src").mkdir(parents=True)
    (tmp_path / "web/src/i18n.ts").write_text('const en = {\n  "a.b":\n    "It is powerful.",\n};\n', encoding="utf-8")
    src = tmp_path / "src/heinzel"
    src.mkdir(parents=True)
    (src / "m.py").write_text(
        "class S(ModuleSettings):\n    x: str = Field(description='It sends mail.')\n"
        "class Other:\n    y: str = Field(description='It sends mail.')\n",
        encoding="utf-8",
    )
    found = [(f.path, f.rule) for f in check_docs.collect(tmp_path, TREE)]
    assert found == [("web/src/i18n.ts", "filler"), ("src/heinzel/m.py", "claims")]


def test_allowlist_and_baseline(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/A.md").write_text("Simply a migration.\n", encoding="utf-8")
    (tmp_path / "docs/.docs-check-allow.toml").write_text(
        '[[allow]]\nrule = "residue"\npath = "docs/*"\nreason = "test"\n', encoding="utf-8"
    )
    findings = check_docs.collect(tmp_path, TREE)
    assert check_docs.report(findings, tmp_path, strict=False, verbose=False) == 1
    (tmp_path / "docs/.docs-check-baseline.txt").write_text("docs/A.md:filler\ndocs/B.md:date\n", encoding="utf-8")
    assert check_docs.report(findings, tmp_path, strict=False, verbose=False) == 0


def test_allowlist_entries_need_a_reason(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/.docs-check-allow.toml").write_text('[[allow]]\nrule = "date"\npath = "x"\n', encoding="utf-8")
    with pytest.raises(SystemExit):
        check_docs.load_allowlist(tmp_path)


def test_the_repository_passes():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--no-commands"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout
    assert "stale" not in result.stdout
