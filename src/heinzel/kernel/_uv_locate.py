"""Find uv where its installers put it, also when an app started from Finder or the Dock has launchd's minimal PATH.

macOS gives such an app `/usr/bin:/bin:/usr/sbin:/sbin`, which holds none of the places uv is installed to, so a
plain `shutil.which("uv")` fails although uv is there. The extra places are fixed directories under the home
directory and the system prefixes. Only existing directories with trusted ancestry are added to the process PATH.
"""

import os
import shutil
import stat
import sys
from collections import deque
from collections.abc import Iterable, Mapping
from pathlib import Path

UV_DOCS = "https://docs.astral.sh/uv/"
_MACOS_ADMIN_GID = 80
_MAX_SYMLINKS = 40


def standard_directories(environment: Mapping[str, str] | None = None) -> list[str]:
    """The directories uv's installers use: its standalone installer, Homebrew, cargo and the system prefix."""
    source = os.environ if environment is None else environment
    home = Path(source.get("HOME") or Path.home())
    configured = [source.get("UV_INSTALL_DIR", ""), source.get("XDG_BIN_HOME", ""), source.get("CARGO_HOME", "")]
    candidates = [
        configured[0],
        configured[1],
        str(home / ".local/bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        str(Path(configured[2]) / "bin") if configured[2] else "",
        str(home / ".cargo/bin"),
    ]
    return _unique(entry for entry in candidates if entry and _trusted(entry))


def search_path(environment: Mapping[str, str] | None = None) -> str:
    """The process PATH first, so a deliberate choice wins, then every standard uv directory it lacks."""
    source = os.environ if environment is None else environment
    current = [entry for entry in source.get("PATH", "").split(os.pathsep) if entry]
    entries = _unique([*current, *standard_directories(source)])
    return os.pathsep.join(entries) or os.defpath  # An empty PATH would search the working directory in subprocesses.


def find_uv(environment: Mapping[str, str] | None = None) -> str | None:
    """The uv executable on the augmented search path; None when it isn't installed in any of those places."""
    return shutil.which("uv", path=search_path(environment))


def missing_uv_message(environment: Mapping[str, str] | None = None) -> str:
    """Say that uv is missing and every directory that was searched for it, once."""
    searched = ", ".join(search_path(environment).split(os.pathsep))
    return f"uv is required to manage Heinzelmännchen's installation; it is not in {searched}. Install it: {UV_DOCS}"


def with_search_path(environment: Mapping[str, str]) -> dict[str, str]:
    """`environment` with PATH augmented, so uv and what it starts (python, git) are found from a Finder app too."""
    return {**environment, "PATH": search_path(environment)}


def _trusted(entry: str) -> bool:
    """Check a single absolute PATH entry, including every ancestor and link in its traversal."""
    path = Path(entry)
    if os.pathsep in entry or not path.is_absolute():
        return False
    try:
        return _trusted_directory(path)
    except (OSError, ValueError):
        return False


def _trusted_directory(path: Path) -> bool:
    owners = {0, os.geteuid()}
    pending = deque(path.parts[1:])
    directory = Path(path.anchor)
    info = directory.lstat()
    links_left = _MAX_SYMLINKS
    while pending:
        if not _safe_directory(info, owners, ancestor=True):
            return False
        child = directory / pending.popleft()
        child_info = child.lstat()
        if child_info.st_uid not in owners:
            return False
        if not stat.S_ISLNK(child_info.st_mode):
            directory, info = child, child_info
            continue

        # Walk the target from here (or from root) so even intermediate redirects get checked.
        links_left -= 1
        if links_left < 0:
            return False
        target = child.readlink()
        parts = target.parts
        if target.is_absolute():
            directory = Path(target.anchor)
            info = directory.lstat()
            parts = parts[1:]
        pending.extendleft(reversed(parts))
    return _safe_directory(info, owners)


def _safe_directory(info: os.stat_result, owners: set[int], *, ancestor: bool = False) -> bool:
    if not stat.S_ISDIR(info.st_mode) or info.st_uid not in owners:
        return False
    # A sticky ancestor protects an existing trusted child from replacement, but is unsafe on PATH itself.
    if ancestor and info.st_mode & stat.S_ISVTX:
        return True
    # Homebrew's writable admin group on macOS has system privileges already.
    trusted_admin_group = sys.platform == "darwin" and info.st_gid == _MACOS_ADMIN_GID
    return not (info.st_mode & stat.S_IWOTH or (info.st_mode & stat.S_IWGRP and not trusted_admin_group))


def _unique(entries: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for entry in entries:
        if entry not in seen:
            seen.append(entry)
    return seen
