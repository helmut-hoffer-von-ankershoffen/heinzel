"""Read-only file tools for the engine runner, confined to one working directory (ADR 0159).

What the Claude Code runner allows read-only (`Read`, `Grep`, `Glob`), as plain functions hz runs itself:

- `read`: lines of one text file, numbered, from an offset, at most `MAX_READ_LINES` lines and `MAX_READ_BYTES` bytes;
- `grep`: lines matching a regular expression in the text files under a folder, at most `MAX_MATCHES`;
- `glob`: the files under a folder whose path matches a pattern, at most `MAX_ENTRIES`.

Every path is relative to the working directory, or absolute inside it. Nothing escapes it: a `..` component is
refused before anything is opened, and every component is opened relative to its parent with `O_NOFOLLOW`, so a
symlink anywhere on the way is refused, even one created between a check and the open. A walk never descends into a
symlink and skips `.git`. Only regular files are read: a FIFO, a device or a socket is refused without blocking. A
file is read as a stream, never whole, so a huge file costs at most the bytes returned.

Stricter than Claude Code: a symlink that stays inside the directory is refused too (it can't be told apart from
one that leaves without following it).
"""

import fnmatch
import os
import re
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

MAX_READ_LINES = 2000
MAX_READ_BYTES = 256 * 1024
MAX_LINE_CHARS = 2000
MAX_MATCHES = 200
MAX_ENTRIES = 1000
MAX_SCANNED_FILES = 5000
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


class ConfinedFiles:
    """Read-only access to the files under one directory, and nothing outside it."""

    def __init__(self, root: Path) -> None:
        """Confine to `root`, which must be an existing directory; a symlink to it is resolved once, here.

        Raises:
            FileToolError: If `root` isn't a directory.
        """
        real = Path(os.path.realpath(root))
        if not real.is_dir():
            msg = f"{root} is not a directory"
            raise FileToolError(msg)
        self.root = real

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
            relative = os.path.relpath(os.path.normpath(text), self.root)
            if relative == ".." or relative.startswith(f"..{os.sep}"):
                msg = f"{path} is outside the working directory"
                raise FileToolError(msg)
            text = relative
        parts = [part for part in text.split("/") if part not in {"", "."}]
        if ".." in parts:
            msg = f"{path} goes up with '..'; give a path inside the working directory"
            raise FileToolError(msg)
        return parts

    def _open_directory(self, parts: list[str]) -> int:
        """A descriptor of the directory `parts` below the root, opened component by component without symlinks.

        Raises:
            FileToolError: If a component is missing, a symlink or not a directory.
        """
        fd = os.open(self.root, _DIRECTORY)
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
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            msg = f"{path} is not a regular file"
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
        with os.fdopen(self._open_file(path), "rb") as stream:
            if b"\x00" in stream.read(BINARY_PROBE_BYTES):
                msg = f"{path} is a binary file"
                raise FileToolError(msg)
            stream.seek(0)
            lines: list[str] = []
            used = 0
            more = False
            for number, raw in enumerate(_lines(stream), start=1):
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
        found: list[str] = []
        more = False
        for entry in self._walk(path):
            os.close(entry.fd)
            if _matches(entry.relative, pattern):
                if len(found) >= MAX_ENTRIES:
                    more = True
                    break
                found.append(entry.relative)
        if not found:
            return "(no files match)"
        return "\n".join(sorted(found)) + (f"\n(stopped at {MAX_ENTRIES} files)" if more else "")

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
        matches: list[str] = []
        scanned = 0
        for entry in self._walk(path):
            with os.fdopen(entry.fd, "rb") as stream:
                if (glob is None or _matches(entry.relative, glob)) and _searchable(stream):
                    scanned += 1
                    matches.extend(_search(stream, expression, entry.relative, MAX_MATCHES - len(matches)))
            if len(matches) >= MAX_MATCHES:
                return "\n".join(matches) + f"\n(stopped at {MAX_MATCHES} matches)"
            if scanned >= MAX_SCANNED_FILES:
                return ("\n".join(matches) or "(no matches)") + f"\n(stopped after {MAX_SCANNED_FILES} files)"
        return "\n".join(matches) or "(no matches)"

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
            if stat.S_ISREG(info.st_mode):
                yield _Entry("/".join(parts), fd)
                return
            os.close(fd)
            if not stat.S_ISDIR(info.st_mode):
                return
        top = self._open_directory(parts)
        try:
            yield from _files(top, "/".join(parts), 0)
        finally:
            os.close(top)


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


def _files(directory: int, prefix: str, depth: int) -> Iterator[_Entry]:
    """Regular files below the open `directory`, depth first, sorted by name; never through a symlink.

    Yields:
        Each file, open for reading; the caller closes it.
    """
    if depth > MAX_DEPTH:
        return
    with os.scandir(directory) as scan:
        entries = sorted(scan, key=lambda e: e.name)
    for entry in entries:
        relative = f"{prefix}/{entry.name}" if prefix else entry.name
        if entry.is_symlink():
            continue
        if entry.is_dir(follow_symlinks=False):
            if entry.name in SKIPPED_DIRECTORIES:
                continue
            try:
                child = os.open(entry.name, _DIRECTORY, dir_fd=directory)
            except OSError:
                continue
            try:
                yield from _files(child, relative, depth + 1)
            finally:
                os.close(child)
        elif entry.is_file(follow_symlinks=False):
            try:
                fd = os.open(entry.name, _FILE, dir_fd=directory)
            except OSError:
                continue
            if stat.S_ISREG(os.fstat(fd).st_mode):
                yield _Entry(relative, fd)
            else:
                os.close(fd)


def _lines(stream: BinaryIO) -> Iterator[bytes]:
    """Lines of a binary stream, each cut at a bounded length, so one huge line never fills memory.

    Yields:
        Each line, at most `MAX_LINE_CHARS * 4` bytes of it.
    """
    readline = stream.readline
    while True:
        raw = readline(MAX_LINE_CHARS * 4)
        if not raw:
            return
        if not raw.endswith(b"\n"):  # a longer line: drop the rest of it
            while (rest := readline(64 * 1024)) and not rest.endswith(b"\n"):
                pass
        yield raw


def _searchable(stream: BinaryIO) -> bool:
    """Whether grep reads this file: within its size limit and not binary; leaves the stream at its start."""
    if os.fstat(stream.fileno()).st_size > MAX_GREP_FILE_BYTES or b"\x00" in stream.read(BINARY_PROBE_BYTES):
        return False
    stream.seek(0)
    return True


def _search(stream: BinaryIO, expression: re.Pattern[str], relative: str, room: int) -> list[str]:
    """Up to `room` matching lines of one file, as `file:line: text`."""
    found: list[str] = []
    for number, raw in enumerate(_lines(stream), start=1):
        line = _text(raw)
        if expression.search(line):
            found.append(f"{relative}:{number}: {line}")
            if len(found) >= room:
                break
    return found


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
