"""Failure-only observations of the interpreter selected for an isolated tool."""

import os
import stat
from pathlib import Path


def interpreter_diagnostics(prefix: Path) -> str:
    """Report the actual resolved interpreter and bounded parent ownership, without executing it."""
    try:
        selected = (prefix / "bin/python").resolve(strict=True)
        lines = [f"selected tool python: {selected}", f"current uid: {os.getuid()}"]
        for parent in list(selected.parents)[:32]:
            info = parent.stat()
            lines.append(f"python ancestor: {parent} uid={info.st_uid} mode={stat.S_IMODE(info.st_mode):04o}")
        return "\n".join(lines)
    except (OSError, RuntimeError) as exc:
        return f"tool python inspection failed: {type(exc).__name__}"
