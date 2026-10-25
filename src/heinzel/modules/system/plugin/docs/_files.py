"""Generated documentation stays inside regular directories and never edits a linked or custom file."""

import fcntl
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from heinzel import HeinzelError
from heinzel.modules.system.plugin.docs._render import GENERATED

MAX_RECORDING_BYTES = 500 * 1024
"""A recording fits the repository's unchanged large-file hook, including its text alternative."""


@contextmanager
def _parent(path: Path, *, create: bool) -> Iterator[int | None]:
    absolute = path.absolute()
    descriptor = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in absolute.parts[1:-1]:
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    yield None
                    return
                # Another writer may create it; opening the result still refuses a link.
                with suppress(FileExistsError):
                    os.mkdir(part, mode=0o755, dir_fd=descriptor)
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def _file(path: Path, text: str, *, write: bool, marker: bool) -> bool:
    with _parent(path, create=write) as parent:
        if parent is None:
            return True
        flags = (os.O_RDWR if write else os.O_RDONLY) | os.O_NOFOLLOW | os.O_NONBLOCK
        created = False
        try:
            if write:
                try:
                    descriptor = os.open(path.name, flags | os.O_CREAT | os.O_EXCL, 0o644, dir_fd=parent)
                    created = True
                except FileExistsError:
                    descriptor = os.open(path.name, flags, dir_fd=parent)
            else:
                descriptor = os.open(path.name, flags, dir_fd=parent)
        except FileNotFoundError:
            return True
        with os.fdopen(descriptor, "r+" if write else "r", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX if write else fcntl.LOCK_SH)
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                msg = "Documentation output cannot use a link or a nonregular file"
                raise HeinzelError(msg)
            # Reading is bounded by the candidate already in memory; a huge unrelated file is never loaded whole.
            current = stream.read(max(len(text), 1024) + 1)
            if marker and not (created and not current) and GENERATED not in current.splitlines()[:2]:
                msg = f"Refusing to overwrite custom documentation {path.name}; move it or choose another directory"
                raise HeinzelError(msg)
            stale = current != text
            if write and stale:
                # Keep the checked descriptor: replacing a directory entry cannot redirect this write elsewhere.
                stream.seek(0)
                stream.write(text)
                stream.truncate()
                stream.flush()
                os.fsync(stream.fileno())
            return stale


def write_pages(files: dict[str, str], directory: Path, *, check: bool) -> tuple[list[str], int]:
    """Check the complete output plan before writing through anchored no-follow descriptors.

    Raises:
        HeinzelError: If an output path is unsafe, linked, nonregular, unreadable or would overwrite custom content.
    """
    try:
        stale: list[str] = []
        for relative, text in files.items():
            if Path(relative).is_absolute() or ".." in Path(relative).parts:
                msg = "A documentation output path is unsafe; check the plugin's distribution and theme names"
                raise HeinzelError(msg)
            if _file(directory / relative, text, write=False, marker=not check):
                stale.append(relative)
        if not check:
            for relative in stale:
                _file(directory / relative, files[relative], write=True, marker=True)
        return stale, len(files) - len(stale)
    except (OSError, UnicodeError) as error:
        msg = "Documentation output cannot follow a symlink or use unreadable paths; choose a regular output directory"
        raise HeinzelError(msg) from error


def read_recording(path: Path) -> bytes | None:
    """Read an optional recording through anchored no-follow descriptors, with a strict size bound.

    Raises:
        HeinzelError: If the asset is linked, nonregular, unreadable or larger than the recording limit.
    """
    try:
        with _parent(path, create=False) as parent:
            if parent is None:
                return None
            try:
                descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            except FileNotFoundError:
                return None
            with os.fdopen(descriptor, "rb") as stream:
                metadata = os.fstat(stream.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                    msg = "Recording inputs cannot use a link or a nonregular file"
                    raise HeinzelError(msg)
                if metadata.st_size > MAX_RECORDING_BYTES:
                    msg = "Recording input exceeds its size limit; shorten or resize the scene"
                    raise HeinzelError(msg)
                content = stream.read(MAX_RECORDING_BYTES + 1)
                if len(content) > MAX_RECORDING_BYTES:
                    msg = "Recording input changed beyond its size limit while being read"
                    raise HeinzelError(msg)
                return content
    except OSError as error:
        msg = "Recording inputs cannot follow a symlink or use unreadable paths"
        raise HeinzelError(msg) from error
