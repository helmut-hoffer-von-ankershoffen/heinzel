"""The engine runner's read-only file tools stay inside the working directory and within their limits (ADR 0159)."""

import os
from pathlib import Path

import pytest

from heinzel.modules.ai.access import _file_tools
from heinzel.modules.ai.access._file_tools import ConfinedFiles, FileToolError

pytestmark = pytest.mark.unit


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "work"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "app.py").write_text("import os\n\ndef main():\n    return 42\n", encoding="utf-8")
    (root / "README.md").write_text("# Title\nhello world\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("hello from git\n", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("hello secret\n", encoding="utf-8")
    return root


def test_read_numbers_lines_from_an_offset(tree: Path) -> None:
    files = ConfinedFiles(tree)

    assert files.read("src/pkg/app.py") == "     1\timport os\n     2\t\n     3\tdef main():\n     4\t    return 42"
    assert files.read("src/pkg/app.py", offset=3, limit=1) == (
        "     3\tdef main():\n(more lines follow; read on with offset 4)"
    )
    assert files.read(str(tree / "README.md")) == "     1\t# Title\n     2\thello world"
    assert "no lines from 9" in files.read("README.md", offset=9)


@pytest.mark.parametrize(
    "path",
    ["../secret.txt", "src/../../secret.txt", "src/pkg/../../../secret.txt", "..", "a/\x00b"],
)
def test_read_refuses_dot_dot_and_nul(tree: Path, path: str) -> None:
    with pytest.raises(FileToolError):
        ConfinedFiles(tree).read(path)


def test_read_refuses_an_absolute_path_outside(tree: Path) -> None:
    files = ConfinedFiles(tree)

    with pytest.raises(FileToolError, match="outside the working directory"):
        files.read(str(tree.parent / "secret.txt"))
    with pytest.raises(FileToolError, match="outside the working directory"):
        files.read("/etc/hosts")
    with pytest.raises(FileToolError, match="outside the working directory"):
        files.grep("hello", path=str(tree.parent))


def test_symlinks_are_never_followed(tree: Path) -> None:
    (tree / "leak.txt").symlink_to(tree.parent / "secret.txt")
    (tree / "outside").symlink_to(tree.parent)
    (tree / "inside.md").symlink_to(tree / "README.md")
    files = ConfinedFiles(tree)

    for path in ("leak.txt", "outside/secret.txt", "inside.md"):
        with pytest.raises(FileToolError, match="symlink"):
            files.read(path)
    with pytest.raises(FileToolError, match="symlink"):
        files.grep("hello", path="outside")
    assert "secret" not in files.grep("hello")
    assert files.glob("*") == "README.md\nsrc/pkg/app.py"


def test_a_symlinked_root_is_resolved_once(tmp_path: Path, tree: Path) -> None:
    link = tmp_path / "link"
    link.symlink_to(tree)

    assert ConfinedFiles(link).read("README.md").startswith("     1\t# Title")


def test_root_must_be_a_directory(tmp_path: Path) -> None:
    with pytest.raises(FileToolError, match="not a directory"):
        ConfinedFiles(tmp_path / "missing")


def test_only_regular_files_are_read(tree: Path) -> None:
    os.mkfifo(tree / "pipe")
    files = ConfinedFiles(tree)

    with pytest.raises(FileToolError, match="not a regular file"):
        files.read("pipe")  # O_NONBLOCK: never waits for a writer
    with pytest.raises(FileToolError, match="not a regular file"):
        files.read("src")
    with pytest.raises(FileToolError, match="does not exist"):
        files.read("nope.txt")
    with pytest.raises(FileToolError, match="not a directory"):
        files.read("README.md/x")
    with pytest.raises(FileToolError, match="not the working directory"):
        files.read(".")
    with pytest.raises(FileToolError, match="start at 1"):
        files.read("README.md", offset=0)
    assert "pipe" not in files.glob("*")


def test_binary_files_are_refused_and_skipped(tree: Path) -> None:
    (tree / "blob.bin").write_bytes(b"hello\x00world")
    files = ConfinedFiles(tree)

    with pytest.raises(FileToolError, match="binary"):
        files.read("blob.bin")
    assert "blob.bin" not in files.grep("hello")


def test_a_huge_file_returns_only_the_capped_bytes(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_file_tools, "MAX_READ_BYTES", 1000)
    line = "x" * 99 + "\n"
    (tree / "huge.txt").write_text(line * 200_000, encoding="utf-8")  # 20 MB
    (tree / "long.txt").write_text("y" * 5_000_000 + "\nnext\n", encoding="utf-8")
    files = ConfinedFiles(tree)

    text = files.read("huge.txt")
    assert text.count("\n") == 10  # ten lines of 99 characters, then the note
    assert text.endswith("read on with offset 11)")
    long = files.read("long.txt")
    assert len(long.split("\n")[0]) == len("     1\t") + _file_tools.MAX_LINE_CHARS
    assert long.endswith("read on with offset 2)")  # one cut line is always shown, then the byte cap
    assert files.read("long.txt", offset=2) == "     2\tnext"
    assert "huge.txt" not in files.grep("x")  # over the grep size limit


def test_grep_finds_lines_with_paths_and_skips_git(tree: Path) -> None:
    files = ConfinedFiles(tree)

    assert files.grep("hello") == "README.md:2: hello world"
    assert files.grep("HELLO", ignore_case=True) == "README.md:2: hello world"
    assert files.grep("return", path="src") == "src/pkg/app.py:4:     return 42"
    assert files.grep("return", path="src/pkg/app.py") == "src/pkg/app.py:4:     return 42"
    assert files.grep("def|import", glob="*.py") == "src/pkg/app.py:1: import os\nsrc/pkg/app.py:3: def main():"
    assert files.grep("nothing-here") == "(no matches)"
    with pytest.raises(FileToolError, match="invalid regular expression"):
        files.grep("(")
    with pytest.raises(FileToolError, match="1 to 500"):
        files.grep("")
    with pytest.raises(FileToolError, match="1 to 500"):
        files.grep("x", glob="y" * 501)


def test_grep_and_glob_stop_at_their_caps(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_file_tools, "MAX_MATCHES", 3)
    monkeypatch.setattr(_file_tools, "MAX_ENTRIES", 2)
    monkeypatch.setattr(_file_tools, "MAX_SCANNED_FILES", 2)
    (tree / "many.txt").write_text("hit\n" * 10, encoding="utf-8")
    for n in range(4):
        (tree / f"f{n}.txt").write_text("hit\n", encoding="utf-8")
    files = ConfinedFiles(tree)

    assert files.grep("hit", path="many.txt").endswith("(stopped at 3 matches)")
    assert files.grep("hit").endswith("(stopped after 2 files)")
    assert files.glob("*.txt") == "f0.txt\nf1.txt\n(stopped at 2 files)"


def test_glob_matches_names_and_paths(tree: Path) -> None:
    files = ConfinedFiles(tree)

    assert files.glob("*.py") == "src/pkg/app.py"
    assert files.glob("src/**/*.py") == "src/pkg/app.py"
    assert files.glob("**/*.md") == "README.md"
    assert files.glob("*.rs") == "(no files match)"
    assert files.glob("*", path="src") == "src/pkg/app.py"


def test_walk_stops_at_the_depth_limit(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_file_tools, "MAX_DEPTH", 1)
    deep = tree / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (deep / "deep.txt").write_text("hit\n", encoding="utf-8")

    assert "deep.txt" not in ConfinedFiles(tree).glob("*")
