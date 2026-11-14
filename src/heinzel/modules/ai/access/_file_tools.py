"""Read-only file tools for the engine runner, confined to one working directory (ADR 0159).

What the Claude Code runner allows read-only (`Read`, `Grep`, `Glob`), as plain functions hz runs itself:

- `read`: lines of one text file, numbered, from an offset, at most `MAX_READ_LINES` lines and `MAX_READ_BYTES` bytes;
- `grep`: lines matching a regular expression in the text files under a folder, at most `MAX_MATCHES`;
- `glob`: the files under a folder whose path matches a pattern, at most `MAX_ENTRIES`.

Every path is relative to the working directory, or absolute inside it (as given or resolved). Nothing escapes it:
a `..` component is refused before anything is opened, and every component is opened relative to its parent, from
a descriptor of the directory held since the session started, with `O_NOFOLLOW`, so a symlink anywhere on the way
is refused, even one created between a check and the open. A file with more than one hard link is refused too: it
may be a file outside the directory. A walk never descends into a symlink and skips `.git`. Only regular files are
read: a FIFO, a device or a socket is refused without blocking.

Every call is bounded: it reads at most `MAX_SCAN_BYTES`, visits at most `MAX_VISITED` folder entries, and runs in
a child process (`run`) that is killed after `TOOL_SECONDS`. The model picks the regular expression, and Python's
`re` can take exponential time on one while it holds the interpreter lock; in a child, that costs only the child.

Stricter than Claude Code: a symlink that stays inside the directory is refused too (it can't be told apart from
one that leaves without following it), and so is a hard-linked file.

This file imports only the standard library: the child runs it as a script, isolated (`python -I`).
"""

import fnmatch
import json
import os
import re
import stat
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

MAX_READ_LINES = 2000
MAX_READ_BYTES = 256 * 1024
MAX_LINE_CHARS = 2000
MAX_MATCHES = 200
MAX_ENTRIES = 1000
MAX_VISITED = 20_000
"""Folder entries one call may visit, files and folders alike, matching or not."""
MAX_SCAN_BYTES = 64 * 1024 * 1024
"""Bytes one call may read, skipped lines included."""
TOOL_SECONDS = 20.0
"""How long one call may take before its child process is killed."""
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_GREP_FILE_BYTES = 2 * 1024 * 1024
MAX_DEPTH = 32
MAX_PATTERN_CHARS = 500
BINARY_PROBE_BYTES = 8192
SKIPPED_DIRECTORIES = frozenset({".git"})
TOOL_NAMES = {"Read": "read", "Grep": "grep", "Glob": "glob"}
"""The read-only tools a profile may name, in Claude Code's naming, and the engine's own name for each."""

_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)


class FileToolError(ValueError):
    """A path or pattern the tools refuse; the message is safe to show the model."""


@dataclass(frozen=True, slots=True)
class _Entry:
    relative: str
    fd: int


class _Budget:
    """What one call may still read and visit."""

    def __init__(self) -> None:
        self.bytes = MAX_SCAN_BYTES
        self.entries = MAX_VISITED

    def read(self, size: int) -> None:
        self.bytes -= size
        if self.bytes < 0:
            msg = f"this call read its limit of {MAX_SCAN_BYTES // (1024 * 1024)} MiB; narrow it"
            raise FileToolError(msg)


class ConfinedFiles:
    """Read-only access to the files under one directory, and nothing outside it."""

    def __init__(self, root: Path, root_fd: int | None = None) -> None:
        """Confine to `root`, which must be an existing directory, held open from here on.

        A symlink to it is resolved once, here; absolute paths may use either spelling. `root_fd` is a descriptor
        of it already open, as the child process gets it.

        Raises:
            FileToolError: If `root` isn't a directory.
        """
        real = Path(os.path.realpath(root))
        if root_fd is None:
            try:
                root_fd = os.open(real, _DIRECTORY)
            except OSError:
                msg = f"{root} is not a directory"
                raise FileToolError(msg) from None
        self.root = real
        self.given = Path(root)
        self.root_fd = root_fd
        self._aliases = tuple(dict.fromkeys([str(real), os.path.normpath(Path(root).absolute())]))
        self._budget = _Budget()

    def close(self) -> None:
        """Let go of the directory."""
        os.close(self.root_fd)

    def _parts(self, path: str) -> list[str]:
        """The components of `path` below the root; never `..`, never outside.

        Raises:
            FileToolError: If the path has a NUL, a `..` component, or is absolute outside the working directory.
        """
        if "\x00" in path:
            msg = "a path can't contain a NUL character"
            raise FileToolError(msg)
        text = path.strip() or "."
        if Path(text).is_absolute():
            inside = [
                relative
                for alias in self._aliases
                if not (relative := os.path.relpath(os.path.normpath(text), alias)).startswith("..")
            ]
            if not inside:
                msg = f"{path} is outside the working directory"
                raise FileToolError(msg)
            text = inside[0]
        parts = [part for part in text.split("/") if part not in {"", "."}]
        if ".." in parts:
            msg = f"{path} goes up with '..'; give a path inside the working directory"
            raise FileToolError(msg)
        if any(part.casefold() in SKIPPED_DIRECTORIES for part in parts):  # case-blind, as macOS's disks are
            msg = f"{path} is inside .git, which the file tools don't read (it may hold credentials)"
            raise FileToolError(msg)
        return parts

    def _open_directory(self, parts: list[str]) -> int:
        """A descriptor of the directory `parts` below the root, opened component by component without symlinks.

        Raises:
            FileToolError: If a component is missing, a symlink or not a directory.
        """
        fd = os.open(".", _DIRECTORY, dir_fd=self.root_fd)
        try:
            for part in parts:
                child = _open_at(part, _DIRECTORY, fd, "/".join(parts))
                os.close(fd)
                fd = child
        except BaseException:
            os.close(fd)
            raise
        return fd

    def _open_file(self, path: str) -> int:
        """A descriptor of the regular file at `path`, opened without following any symlink.

        Raises:
            FileToolError: If it is missing, behind a symlink, or not a regular file.
        """
        parts = self._parts(path)
        if not parts:
            msg = "give the path of a file, not the working directory"
            raise FileToolError(msg)
        parent = self._open_directory(parts[:-1])
        try:
            fd = _open_at(parts[-1], _FILE, parent, path)
        finally:
            os.close(parent)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
            os.close(fd)
            why = (
                "has more than one hard link, which the file tools don't follow"
                if stat.S_ISREG(info.st_mode)
                else ("is not a regular file")
            )
            msg = f"{path} {why}"
            raise FileToolError(msg)
        return fd

    def read(self, path: str, offset: int = 1, limit: int = MAX_READ_LINES) -> str:
        """Lines `offset` (1-based) onwards of the text file at `path`, numbered, within the size limits.

        Raises:
            FileToolError: If the path is refused, the file is binary, or the range is invalid.
        """
        if offset < 1 or limit < 1:
            msg = "offset and limit start at 1"
            raise FileToolError(msg)
        limit = min(limit, MAX_READ_LINES)
        self._budget = _Budget()
        with os.fdopen(self._open_file(path), "rb") as stream:
            if b"\x00" in stream.read(BINARY_PROBE_BYTES):
                msg = f"{path} is a binary file"
                raise FileToolError(msg)
            stream.seek(0)
            lines: list[str] = []
            used = 0
            more = False
            for number, raw in enumerate(self._lines(stream), start=1):
                if number < offset:
                    continue
                line = _text(raw)
                if len(lines) >= limit or (lines and used + len(line) > MAX_READ_BYTES):
                    more = True
                    break
                used += len(line)
                lines.append(f"{number:>6}\t{line}")
        if not lines:
            return f"({path} has no lines from {offset} on)"
        note = f"\n(more lines follow; read on with offset {offset + len(lines)})" if more else ""
        return "\n".join(lines) + note

    def glob(self, pattern: str = "*", path: str = ".") -> str:
        """The files under `path` whose relative path (with `/`) or name matches `pattern`, sorted, one per line.

        Raises:
            FileToolError: If the path or pattern is refused.
        """
        _check_pattern(pattern)
        self._budget = _Budget()
        found: list[str] = []
        more = False
        for entry in self._walk(path):
            os.close(entry.fd)
            if _matches(entry.relative, pattern):
                if len(found) >= MAX_ENTRIES:
                    more = True
                    break
                found.append(entry.relative)
        if self._budget.entries <= 0:
            more_note = f"\n(stopped after {MAX_VISITED} entries)"
        else:
            more_note = f"\n(stopped at {MAX_ENTRIES} files)" if more else ""
        return ("\n".join(sorted(found)) or "(no files match)") + more_note

    def grep(self, pattern: str, path: str = ".", glob: str | None = None, *, ignore_case: bool = False) -> str:
        """`file:line: text` for each line matching the regular expression `pattern` in the text files under `path`.

        Raises:
            FileToolError: If the path, the glob or the regular expression is refused.
        """
        _check_pattern(pattern)
        if glob is not None:
            _check_pattern(glob)
        try:
            expression = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        except re.error as exc:
            msg = f"invalid regular expression: {exc}"
            raise FileToolError(msg) from None
        self._budget = _Budget()
        matches: list[str] = []
        for entry in self._walk(path):
            with os.fdopen(entry.fd, "rb") as stream:
                if (glob is None or _matches(entry.relative, glob)) and self._searchable(stream):
                    matches.extend(self._search(stream, expression, entry.relative, MAX_MATCHES - len(matches)))
            if len(matches) >= MAX_MATCHES:
                return "\n".join(matches) + f"\n(stopped at {MAX_MATCHES} matches)"
        stopped = f"\n(stopped after {MAX_VISITED} entries)" if self._budget.entries <= 0 else ""
        return ("\n".join(matches) or "(no matches)") + stopped

    def _walk(self, path: str) -> Iterator[_Entry]:
        """Each regular file under `path` (or `path` itself), open, with its path relative to the root.

        The caller closes each descriptor. Symlinks are skipped, never followed; so are `.git` and anything deeper
        than `MAX_DEPTH`.

        Yields:
            Each file, open for reading.

        Raises:
            FileToolError: If the path is refused.
        """
        parts = self._parts(path)
        if parts:
            parent = self._open_directory(parts[:-1])
            try:
                fd = _open_at(parts[-1], _FILE, parent, path)
            finally:
                os.close(parent)
            info = os.fstat(fd)
            if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                yield _Entry("/".join(parts), fd)
                return
            os.close(fd)
            if not stat.S_ISDIR(info.st_mode):
                return
        top = self._open_directory(parts)
        try:
            yield from self._files(top, "/".join(parts), 0)
        finally:
            os.close(top)

    def _files(self, directory: int, prefix: str, depth: int) -> Iterator[_Entry]:
        """Regular files below the open `directory`, depth first, sorted by name; never through a symlink.

        Every entry seen counts against the call's budget; a hard-linked file is skipped.

        Yields:
            Each file, open for reading; the caller closes it.
        """
        if depth > MAX_DEPTH:
            return
        entries = []
        with os.scandir(directory) as scan:
            for entry in scan:
                self._budget.entries -= 1
                if self._budget.entries < 0:
                    break
                entries.append(entry)
        for entry in sorted(entries, key=lambda e: e.name):
            if self._budget.entries < 0:
                return
            yield from self._visit(directory, entry, f"{prefix}/{entry.name}" if prefix else entry.name, depth)

    def _visit(self, directory: int, entry: os.DirEntry[str], relative: str, depth: int) -> Iterator[_Entry]:
        """One entry of a walk: a folder walked into, or a single-linked regular file opened; nothing else.

        Yields:
            Each file, open for reading; the caller closes it.
        """
        if entry.is_symlink():
            return
        if entry.is_dir(follow_symlinks=False):
            if entry.name.casefold() in SKIPPED_DIRECTORIES:
                return
            try:
                child = os.open(entry.name, _DIRECTORY, dir_fd=directory)
            except OSError:
                return
            try:
                yield from self._files(child, relative, depth + 1)
            finally:
                os.close(child)
        elif entry.is_file(follow_symlinks=False):
            try:
                fd = os.open(entry.name, _FILE, dir_fd=directory)
            except OSError:
                return
            info = os.fstat(fd)
            if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                yield _Entry(relative, fd)
            else:
                os.close(fd)

    def _lines(self, stream: BinaryIO) -> Iterator[bytes]:
        """Lines of a binary stream, each cut at a bounded length, every byte read counted against the budget.

        Yields:
            Each line, at most `MAX_LINE_CHARS * 4` bytes of it.
        """
        readline = stream.readline
        while True:
            raw = readline(MAX_LINE_CHARS * 4)
            self._budget.read(len(raw))
            if not raw:
                return
            if not raw.endswith(b"\n"):  # a longer line: drop the rest of it
                while rest := readline(64 * 1024):
                    self._budget.read(len(rest))
                    if rest.endswith(b"\n"):
                        break
            yield raw

    def _searchable(self, stream: BinaryIO) -> bool:
        """Whether grep reads this file: within its size limit and not binary; leaves the stream at its start."""
        if os.fstat(stream.fileno()).st_size > MAX_GREP_FILE_BYTES:
            return False
        probe = stream.read(BINARY_PROBE_BYTES)
        self._budget.read(len(probe))
        if b"\x00" in probe:
            return False
        stream.seek(0)
        return True

    def _search(self, stream: BinaryIO, expression: re.Pattern[str], relative: str, room: int) -> list[str]:
        """Up to `room` matching lines of one file, as `file:line: text`."""
        found: list[str] = []
        for number, raw in enumerate(self._lines(stream), start=1):
            line = _text(raw)
            if expression.search(line):
                found.append(f"{relative}:{number}: {line}")
                if len(found) >= room:
                    break
        return found


def _open_at(name: str, flags: int, directory: int, shown: str) -> int:
    """`name` opened relative to `directory` with `flags`.

    Raises:
        FileToolError: If it is missing, a symlink, or the wrong kind.
    """
    try:
        return os.open(name, flags, dir_fd=directory)
    except OSError as exc:  # ELOOP or ENOTDIR for a symlink, depending on the platform
        error = exc
    try:
        is_link = stat.S_ISLNK(os.stat(name, dir_fd=directory, follow_symlinks=False).st_mode)
    except OSError:
        is_link = False
    if is_link:
        msg = f"{shown} is a symlink, which the file tools don't follow"
    elif isinstance(error, FileNotFoundError):
        msg = f"{shown} does not exist"
    elif isinstance(error, NotADirectoryError):
        msg = f"{shown} is not a directory"
    else:
        msg = f"{shown}: {error.strerror}"
    raise FileToolError(msg)


def _text(raw: bytes) -> str:
    return raw.rstrip(b"\r\n").decode("utf-8", errors="replace")[:MAX_LINE_CHARS]


def _check_pattern(pattern: str) -> None:
    """Refuse an empty, overlong or NUL-carrying pattern.

    Raises:
        FileToolError: If the pattern is refused.
    """
    if not pattern or len(pattern) > MAX_PATTERN_CHARS or "\x00" in pattern:
        msg = f"a pattern needs 1 to {MAX_PATTERN_CHARS} characters and no NUL"
        raise FileToolError(msg)


def _matches(relative: str, pattern: str) -> bool:
    """Whether a file matches a glob: by its whole relative path when the pattern has a `/`, else by its name.

    `**/` also matches no folder at all, so `**/*.py` finds `setup.py` at the top.
    """
    if "/" not in pattern:
        return fnmatch.fnmatchcase(relative.rsplit("/", 1)[-1], pattern)
    return fnmatch.fnmatchcase(relative, pattern) or (
        pattern.startswith("**/") and fnmatch.fnmatchcase(relative, pattern[3:])
    )


TOOLS = ("read", "grep", "glob")


def call(files: ConfinedFiles, tool: str, arguments: dict[str, Any]) -> str:
    """Run one tool on `files` in this process; a refusal is the answer, prefixed `Refused:`."""
    if tool not in TOOLS:
        return f"Refused: no tool {tool}"
    try:
        return str(getattr(files, tool)(**arguments))
    except FileToolError as exc:
        return f"Refused: {exc}"
    except TypeError:
        return f"Refused: {tool} doesn't take those arguments"


def run(files: ConfinedFiles, tool: str, arguments: dict[str, Any], seconds: float = TOOL_SECONDS) -> str:
    """Run one tool in a child process that gets only the directory's descriptor, and kill it after `seconds`.

    The child is this file run as an isolated script: no hz code, no environment, no other descriptor.
    """
    request = json.dumps({"root": str(files.given), "fd": files.root_fd, "tool": tool, "arguments": arguments})
    try:
        done = subprocess.run(  # noqa: S603 - this interpreter and this file, never a shell
            [sys.executable, "-I", "-S", __file__],
            input=request,
            capture_output=True,
            text=True,
            timeout=seconds,
            env={},
            pass_fds=(files.root_fd,),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"Refused: {tool} took longer than {seconds:g}s and was stopped; narrow the pattern or the path"
    if done.returncode != 0 or not done.stdout:
        return f"Refused: {tool} failed"
    return done.stdout[:MAX_OUTPUT_BYTES]


def _main() -> None:
    """The child: one tool call from standard input, its answer to standard output."""
    request = json.loads(sys.stdin.read())
    files = ConfinedFiles(Path(request["root"]), root_fd=int(request["fd"]))
    answer = call(files, request["tool"], request["arguments"])
    # The answer goes to the parent's pipe, never a terminal: the parent started this child with captured output.
    # A file name that isn't valid UTF-8 shows with a replacement character instead of failing the whole answer.
    with open(1, "w", encoding="utf-8", errors="replace", closefd=False) as pipe:
        pipe.write(answer)


if __name__ == "__main__":
    _main()
