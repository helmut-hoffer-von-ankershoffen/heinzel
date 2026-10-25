"""Retention of drafts and their records: files older than `[heinzel] retention_days` expire (ADR 0021).

Only files hz wrote are considered: names matching `DRAFT_NAME` in a contract's directory below
`<data dir>/workflow/drafts/`, whose stamp is the render's time in UTC. Anything else there is left alone.

The walk never follows a symbolic link below the data directory: `workflow` and `drafts` must be real directories,
each step opens the next with `O_NOFOLLOW` relative to the directory it already holds, and files are unlinked
relative to their directory's descriptor. A link put anywhere below the data directory, or swapped in while the walk
runs, can't make it delete something outside. Directories are never removed: a render may be writing into one.

A contract directory or file that can't be read or deleted is a problem of its own: the walk reports it and goes on,
so one bad entry never keeps every other expired file.
"""

import errno
import os
import stat
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from heinzel import HeinzelError
from heinzel.modules.heinzel._models import DRAFT_NAME, DRAFT_STAMP, DRAFTS

NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | NOFOLLOW


@dataclass(frozen=True, slots=True)
class Expired:
    """A draft or record older than the cutoff."""

    path: Path
    created: datetime
    bytes: int
    record: bool


@dataclass(slots=True)
class Sweep:
    """What a walk found (and deleted, unless only listing), oldest first, and what it couldn't handle."""

    found: list[Expired] = field(default_factory=list[Expired])
    problems: list[str] = field(default_factory=list[str])

    @property
    def drafts(self) -> int:
        """How many of the files are drafts."""
        return sum(not e.record for e in self.found)

    @property
    def records(self) -> int:
        """How many of the files are records."""
        return len(self.found) - self.drafts

    @property
    def bytes(self) -> int:
        """Their total size."""
        return sum(e.bytes for e in self.found)


def expired(data_dir: Path, cutoff: datetime, *, delete: bool) -> Sweep:
    """The drafts and records below `data_dir` created before `cutoff`; deleted too when `delete`.

    Idempotent: a file or directory that is already gone, also one removed by a concurrent prune, is not a problem.

    Raises:
        HeinzelError: If `workflow` or `drafts` below `data_dir` is a symbolic link, or a directory on the way to them
            can't be read; then nothing is walked.
    """
    sweep = Sweep()
    drafts_dir = data_dir.joinpath(*DRAFTS)
    with _drafts(data_dir) as root:
        if root is None:
            return sweep
        for entry in _entries(root):
            if not entry.is_dir(follow_symlinks=False):
                continue  # a link or a stray file at the top is not a contract's directory
            path = drafts_dir / entry.name
            try:
                with _opened(entry.name, root, path) as directory:
                    if directory is not None:
                        _sweep(directory, path, cutoff, sweep, delete=delete)
            except HeinzelError as exc:  # this directory's problem; the others are still pruned
                sweep.problems.append(str(exc))
    sweep.found.sort(key=lambda e: (e.created, str(e.path)))
    return sweep


def _sweep(directory: int, path: Path, cutoff: datetime, sweep: Sweep, *, delete: bool) -> None:
    for entry in _entries(directory):
        match = DRAFT_NAME.fullmatch(entry.name)
        if match is None or not entry.is_file(follow_symlinks=False):
            continue
        created = datetime.strptime(match["stamp"], DRAFT_STAMP).replace(tzinfo=UTC)
        if created >= cutoff:
            continue
        try:
            info = os.stat(entry.name, dir_fd=directory, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):  # pragma: no cover - swapped for a link after scandir looked
                continue
            if delete:
                os.unlink(entry.name, dir_fd=directory)
        except FileNotFoundError:
            continue  # pruned meanwhile; the goal is that it is gone
        except OSError as exc:  # this file's problem; the others are still pruned
            sweep.problems.append(f"Can't prune {path / entry.name}: {exc.strerror or exc}")
            continue
        record = match["suffix"] == ".record.json"
        sweep.found.append(Expired(path=path / entry.name, created=created, bytes=info.st_size, record=record))


def _entries(directory: int) -> list[os.DirEntry[str]]:
    """A directory's entries by name, read before any is deleted, the iterator closed.

    Raises:
        HeinzelError: If it can't be listed.
    """
    try:
        with os.scandir(directory) as entries:
            return sorted(entries, key=lambda e: e.name)
    except OSError as exc:
        msg = f"Can't list a drafts directory: {exc.strerror or exc}"
        raise HeinzelError(msg) from exc


@contextmanager
def _drafts(data_dir: Path) -> Generator[int | None]:
    """The drafts directory, reached without following links below the data directory.

    Yields:
        Its descriptor, or None if it doesn't exist yet.

    Raises:
        HeinzelError: If `workflow` or `drafts` is a symbolic link or isn't a readable directory.
    """
    # The configured data directory itself may be a link (a moved home, say); only what hz creates below it may not.
    with _opened(str(data_dir), None, data_dir, follow=True) as base:
        if base is None:
            yield None
            return
        module, drafts = DRAFTS
        with _opened(module, base, data_dir / module) as parent:
            if parent is None:
                yield None
                return
            with _opened(drafts, parent, data_dir / module / drafts) as found:
                yield found


@contextmanager
def _opened(name: str, parent: int | None, path: Path, *, follow: bool = False) -> Generator[int | None]:
    """A directory opened relative to `parent`, without following a symbolic link unless `follow`.

    Yields:
        Its descriptor, closed afterwards, or None if it doesn't exist.

    Raises:
        HeinzelError: If it is a symbolic link, not a directory, or can't be opened.
    """
    flags = DIRECTORY_FLAGS & ~NOFOLLOW if follow else DIRECTORY_FLAGS
    try:
        descriptor = os.open(name, flags, dir_fd=parent)
    except FileNotFoundError:
        descriptor = None
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR, errno.EMLINK}:
            msg = f"{path} is a symbolic link or not a directory; drafts are pruned in real directories only"
        else:
            msg = f"Can't read {path}: {exc.strerror or exc}"
        raise HeinzelError(msg) from exc
    if descriptor is None:
        yield None
        return
    try:
        yield descriptor
    finally:
        os.close(descriptor)
