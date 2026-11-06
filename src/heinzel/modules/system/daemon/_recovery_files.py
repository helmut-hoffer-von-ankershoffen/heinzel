"""Physical file evidence and atomic no-replace moves for explicit daemon recovery."""

import ctypes
import hashlib
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Never

from pydantic import JsonValue

from heinzel import HeinzelError

LIMIT = 256 * 1024


def _refuse() -> Never:
    msg = "The recovery file or native archive authority could not be verified."
    raise HeinzelError(msg)


@dataclass(frozen=True, slots=True)
class RecoveryFile:
    """A bounded, owner-controlled file captured by path, bytes and inode metadata."""

    path: Path
    content: bytes
    identity: tuple[int, ...]

    @property
    def modified_ns(self) -> int:
        """The captured data modification time in nanoseconds."""
        return self.identity[-2]

    def facts(self) -> dict[str, JsonValue]:
        """The path and byte and identity evidence, without exposing file contents."""
        return {
            "path": str(self.path),
            "sha256": hashlib.sha256(self.content).hexdigest(),
            "identity": list(self.identity),
        }


def _file_identity(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def recovery_file(path: Path, *, private: bool = True) -> RecoveryFile:
    """Read one owned, physically reached file and verify that its evidence stays stable.

    Raises:
        HeinzelError: If ownership, permissions or the observed file are not safe.
        OSError: If the file could not be opened or read.
    """
    # Reuse the daemon's physical-parent, no-follow file boundary, not Path.read_bytes().
    from heinzel.modules.system.daemon._service import verified_handover_file  # noqa: PLC0415 - shared sibling boundary

    with verified_handover_file(path) as handle:
        info = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & (0o077 if private else 0o022)
            or info.st_nlink != 1
            or info.st_size > LIMIT
        ):
            _refuse()
        content = handle.read(LIMIT + 1)
        after = os.fstat(handle.fileno())

        if len(content) > LIMIT or _file_identity(info) != _file_identity(after):
            _refuse()
        return RecoveryFile(path, content, _file_identity(info))


def path_absent(path: Path) -> bool:
    """Require a missing entry, rejecting a dangling link.

    Raises:
        OSError: If the entry cannot be inspected.
    """
    try:
        path.lstat()
    except FileNotFoundError:
        return True
    return False


def rename_exclusive(source: Path, destination: Path, *, expected_source: RecoveryFile | None = None) -> None:
    """Atomically move without replacing any destination.

    Raises:
        HeinzelError: The expected source changed before the move.
        OSError: The exclusive native operation is unavailable or refuses the move.
    """
    # Darwin RENAME_EXCL=4 (xnu bsd/sys/stdio.h); Linux RENAME_NOREPLACE=1.
    # Linux supports protocol-double tests only; real legacy recovery remains macOS-only.
    if sys.platform == "darwin":
        library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        rename = library.renameatx_np
        flags = 4
    elif sys.platform == "linux":
        library = ctypes.CDLL(None, use_errno=True)
        rename = library.renameat2
        flags = 1
    else:
        _refuse()
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    source_fd = os.open(source.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        destination_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if expected_source is not None:
                info = os.stat(source.name, dir_fd=source_fd, follow_symlinks=False)
                if (
                    expected_source.path != source
                    or info.st_nlink != 1
                    or _file_identity(info) != expected_source.identity
                ):
                    msg = "The file changed immediately before the recovery move."
                    raise HeinzelError(msg)
            if rename(source_fd, os.fsencode(source.name), destination_fd, os.fsencode(destination.name), flags):
                raise OSError(ctypes.get_errno(), "Exclusive recovery move refused")
            os.fsync(destination_fd)
            os.fsync(source_fd)
        finally:
            os.close(destination_fd)
    finally:
        os.close(source_fd)
