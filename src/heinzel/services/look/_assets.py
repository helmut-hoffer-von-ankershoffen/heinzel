"""Read local look assets without following symlinks or blocking on special files."""

import os
import stat
from pathlib import Path


def _read_regular(path: Path, limit: int, where: str) -> bytes:
    """Up to `limit + 1` bytes of `path`, through one descriptor that follows no symlink and must be a regular file.

    Raises:
        ValueError: If `path` is a symlink, a FIFO or anything else that isn't a regular file, or can't be read.
    """
    try:
        # One descriptor for the check and the read: no symlink swapped in, no FIFO to block on, a regular file.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                msg = f"{where} isn't a file"
                raise ValueError(msg)
            return handle.read(limit + 1)
    except OSError as exc:
        msg = f"{where} can't be read: {exc}"
        raise ValueError(msg) from exc


# Preserve the published qualified name when the old path is serialized.
_read_regular.__module__ = "heinzel.kernel.look._themes"
