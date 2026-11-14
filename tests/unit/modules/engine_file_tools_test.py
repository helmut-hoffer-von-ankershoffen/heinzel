"""The engine runner's read-only file tools stay inside the working directory and within their limits (ADR 0159)."""

import os
import time
from pathlib import Path
from typing import Any

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
    (tree / "many.txt").write_text("hit\n" * 10, encoding="utf-8")
    for n in range(4):
        (tree / f"f{n}.txt").write_text("hit\n", encoding="utf-8")
    files = ConfinedFiles(tree)

    assert files.grep("hit", path="many.txt").endswith("(stopped at 3 matches)")
    assert files.glob("*.txt") == "f0.txt\nf1.txt\n(stopped at 2 files)"


def test_every_entry_visited_counts_matching_or_not(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_file_tools, "MAX_VISITED", 10)
    for n in range(200):
        (tree / f"b{n}.bin").write_bytes(b"\x00")
    opened: list[int] = []
    real = os.open

    def counting(*args: Any, **kwargs: Any) -> int:
        opened.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(_file_tools.os, "open", counting)
    files = ConfinedFiles(tree)
    assert files.glob("*.nomatch") == "(no files match)\n(stopped after 10 entries)"
    globbed = len(opened)
    opened.clear()
    assert files.grep("x").endswith("(stopped after 10 entries)")
    assert globbed < 20
    assert len(opened) < 20


def test_a_huge_offset_stops_at_the_byte_budget(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_file_tools, "MAX_SCAN_BYTES", 1024 * 1024)
    big = tree / "big.log"
    with big.open("wb") as out:
        out.write(b"text line\n" * 1000)
        out.truncate(64 * 1024**3)  # sparse: the rest is one endless line of NUL bytes
    started = time.monotonic()

    with pytest.raises(FileToolError, match="read its limit of 1 MiB"):
        ConfinedFiles(tree).read("big.log", offset=10**9)
    assert time.monotonic() - started < 5


def test_a_hard_link_is_refused_and_skipped(tree: Path) -> None:
    os.link(tree.parent / "secret.txt", tree / "inner.txt")
    files = ConfinedFiles(tree)

    with pytest.raises(FileToolError, match="hard link"):
        files.read("inner.txt")
    assert "inner.txt" not in files.glob("*")
    assert "secret" not in files.grep("hello")
    assert "secret" not in files.grep("hello", path="inner.txt")


def test_git_is_never_read(tree: Path) -> None:
    with pytest.raises(FileToolError, match=r"\.git"):
        ConfinedFiles(tree).read(".git/config")
    with pytest.raises(FileToolError, match=r"\.git"):
        ConfinedFiles(tree).grep("hello", path=".git")
    with pytest.raises(FileToolError, match=r"\.git"):
        ConfinedFiles(tree).read(".GIT/config")  # macOS's disks ignore case
    (tree / "sub").mkdir()
    (tree / "sub" / ".Git").mkdir()
    (tree / "sub" / ".Git" / "config").write_text("hello in Git\n", encoding="utf-8")
    assert "Git" not in ConfinedFiles(tree).grep("hello")


def test_an_absolute_path_may_use_the_root_as_given(tmp_path: Path, tree: Path) -> None:
    alias = tmp_path / "alias"
    alias.symlink_to(tree)

    assert ConfinedFiles(alias).read(str(alias / "README.md")).startswith("     1\t# Title")
    assert ConfinedFiles(alias).read(str(tree / "README.md")).startswith("     1\t# Title")


def test_a_folder_swapped_for_a_symlink_after_the_check_is_refused(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = ConfinedFiles(tree)
    checked = files._parts

    def racing(path: str) -> list[str]:
        parts = checked(path)
        (tree / "src").rename(tree / "src.old")
        (tree / "src").symlink_to(tree.parent)
        return parts

    monkeypatch.setattr(files, "_parts", racing)
    with pytest.raises(FileToolError, match="symlink"):
        files.read("src/secret.txt")


def test_the_root_is_held_open_so_a_moved_folder_doesn_t_redirect(tmp_path: Path, tree: Path) -> None:
    files = ConfinedFiles(tree)
    tree.rename(tmp_path / "moved")
    tree.mkdir()
    (tree / "README.md").write_text("planted\n", encoding="utf-8")

    assert files.read("README.md").startswith("     1\t# Title")


def test_a_call_runs_in_a_child_that_is_stopped_at_its_deadline(tree: Path) -> None:
    (tree / "evil.txt").write_text("a" * 40 + "!\n", encoding="utf-8")
    files = ConfinedFiles(tree)
    started = time.monotonic()

    answer = _file_tools.run(files, "grep", {"pattern": "(a+)+$", "path": "evil.txt"}, seconds=1)

    assert answer.startswith("Refused: grep took longer than 1s")
    assert time.monotonic() - started < 5
    assert _file_tools.run(files, "grep", {"pattern": "hello"}) == "README.md:2: hello world"
    assert _file_tools.run(files, "read", {"path": "../secret.txt"}).startswith("Refused: ../secret.txt goes up")
    assert _file_tools.run(files, "write", {"path": "x"}) == "Refused: no tool write"
    assert _file_tools.run(files, "read", {"nope": 1}) == "Refused: read doesn't take those arguments"


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


def test_a_file_name_that_isnt_utf8_doesnt_fail_the_whole_answer(tree: Path) -> None:
    try:
        (tree / os.fsdecode(b"bad-\xff.md")).write_text("hello\n", encoding="utf-8")
    except OSError:
        pytest.skip("this file system refuses names that aren't UTF-8 (APFS)")
    answer = _file_tools.run(ConfinedFiles(tree), "glob", {"pattern": "*.md"})
    assert "README.md" in answer
    assert "bad-" in answer


def test_a_swapped_parent_folder_doesn_t_redirect_the_tools(tmp_path: Path) -> None:
    """The root is held open from the start, so swapping a folder above it changes nothing (ADR 0159, A4)."""
    parent = tmp_path / "parent"
    root = parent / "work"
    root.mkdir(parents=True)
    (root / "README.md").write_text("original\n", encoding="utf-8")
    files = ConfinedFiles(root)
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "work").mkdir(parents=True)
    (elsewhere / "work" / "README.md").write_text("planted\n", encoding="utf-8")

    parent.rename(tmp_path / "parent.old")
    parent.symlink_to(elsewhere)

    try:
        assert files.read("README.md") == "     1\toriginal"
        assert files.read(str(root / "README.md")) == "     1\toriginal"  # the old spelling still means the held folder
        assert files.grep("planted|original") == "README.md:1: original"
        assert _file_tools.run(files, "read", {"path": "README.md"}) == "     1\toriginal"  # the child gets the fd
    finally:
        files.close()
    files.close()  # again is a no-op


@pytest.mark.parametrize("wide", ["root", "home", "home-alias", "home-other-case"])
def test_the_whole_disk_or_home_is_refused_by_identity(tmp_path: Path, wide: str) -> None:
    """Refused by what the opened folder is, not by its name: another spelling or a symlink still counts."""
    home = Path.home()
    if wide == "home-other-case":
        other = Path(str(home).swapcase())
        if not other.is_dir():
            pytest.skip("a case-sensitive disk: the other case is another folder")
        folder = other
    elif wide == "home-alias":
        folder = tmp_path / "alias"
        folder.symlink_to(home)
    else:
        folder = Path("/") if wide == "root" else home

    with pytest.raises(FileToolError, match="whole disk, your home or a folder above it"):
        ConfinedFiles(folder)
    assert _file_tools.too_wide(folder.stat())
    assert not _file_tools.too_wide(tmp_path.stat())


@pytest.mark.parametrize(
    "above",
    ["/Users", "/Users/", "/home", "/System", "/System/Volumes", "/System/Volumes/Data", "/System/Volumes/Data/Users"],
)
def test_every_folder_above_home_or_the_data_volume_is_refused(above: str) -> None:
    """`/Users` or `/home` holds every user's home, and the data volume holds the whole disk (ADR 0159, A3)."""
    folder = Path(above)
    if not folder.is_dir():
        pytest.skip(f"{above} doesn't exist here")
    if above in {"/Users", "/Users/", "/home"} and folder not in Path.home().parents:
        pytest.skip(f"{above} isn't above the home folder here")

    with pytest.raises(FileToolError, match="whole disk, your home or a folder above it"):
        ConfinedFiles(folder)
