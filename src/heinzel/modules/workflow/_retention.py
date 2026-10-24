"""Retention of drafts and their records: files older than `[workflow] retention_days` expire (ADR 0021).

Only files hz wrote are considered: `<stamp>[-<n>].md`, `.json` and `.record.json` in a contract's directory below
`<data dir>/workflow/drafts/`, where the stamp is the render's time in UTC. Anything else there is left alone.

The walk never follows a symbolic link: `workflow` and `drafts` below the data directory must be real directories,
each step opens the next with `O_NOFOLLOW` relative to the directory it already holds, and files are unlinked
relative to their directory's descriptor. A link put anywhere below the data directory, or swapped in while the walk
runs, can't make it delete something outside. Directories are never removed: a render may be writing into one.
"""

import errno
import os
import re
import stat
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from heinzel import HeinzelError

STAMPED = re.compile(r"(?P<stamp>\d{8}T\d{6}Z)(?:-[1-9]\d*)?(?P<suffix>\.record\.json|\.md|\.json)")
"""The names `WorkflowService._save` gives drafts and records."""
STAMP = "%Y%m%dT%H%M%SZ"
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | NOFOLLOW


@dataclass(frozen=True, slots=True)
class Expired:
    """A draft or record older than the cutoff."""

    path: Path
    created: datetime
    bytes: int
    record: bool


def expired(drafts_dir: Path, cutoff: datetime, *, delete: bool) -> list[Expired]:
    """The drafts and records created before `cutoff`, oldest first; deleted too when `delete`.

    Idempotent: a file or directory that is already gone, also one removed by a concurrent prune, is not an error.

    Raises:
        HeinzelError: If a directory on the way is a symbolic link or can't be read, or a file can't be deleted.
    """
    found: list[Expired] = []
    with _drafts(drafts_dir) as root:
        if root is None:
            return []
        for entry in _entries(root):
            if not entry.is_dir(follow_symlinks=False):
                continue  # a link or a stray file at the top is not a contract's directory
            with _opened(entry.name, root, drafts_dir / entry.name) as directory:
                if directory is not None:
                    found += _expired_in(directory, drafts_dir / entry.name, cutoff, delete=delete)
    return sorted(found, key=lambda e: (e.created, str(e.path)))


def _expired_in(directory: int, path: Path, cutoff: datetime, *, delete: bool) -> list[Expired]:
    found: list[Expired] = []
    for entry in _entries(directory):
        match = STAMPED.fullmatch(entry.name)
        if match is None or not entry.is_file(follow_symlinks=False):
            continue
        created = datetime.strptime(match["stamp"], STAMP).replace(tzinfo=UTC)
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
        except OSError as exc:
            msg = f"Can't prune {path / entry.name}: {exc.strerror or exc}"
            raise HeinzelError(msg) from exc
        record = match["suffix"] == ".record.json"
        found.append(Expired(path=path / entry.name, created=created, bytes=info.st_size, record=record))
    return found


def _entries(directory: int) -> list[os.DirEntry[str]]:
    """A directory's entries by name, read before any is deleted, the iterator closed."""
    with os.scandir(directory) as entries:
        return sorted(entries, key=lambda e: e.name)


@contextmanager
def _drafts(drafts_dir: Path) -> Generator[int | None]:
    """The drafts directory, reached without following links below the data directory.

    Yields:
        Its descriptor, or None if it doesn't exist yet.

    Raises:
        HeinzelError: If `workflow` or `drafts` is a symbolic link or isn't a readable directory.
    """
    data_dir = drafts_dir.parent.parent
    # The configured data directory itself may be a link (a moved home, say); only what hz creates below it may not.
    with _opened(str(data_dir), None, data_dir, follow=True) as base:
        if base is None:
            yield None
            return
        with _opened(drafts_dir.parent.name, base, drafts_dir.parent) as module:
            if module is None:
                yield None
                return
            with _opened(drafts_dir.name, module, drafts_dir) as drafts:
                yield drafts


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
