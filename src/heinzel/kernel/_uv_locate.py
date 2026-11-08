"""Find uv where its installers put it, also when an app started from Finder or the Dock has launchd's minimal PATH.

macOS gives such an app `/usr/bin:/bin:/usr/sbin:/sbin`, which holds none of the places uv is installed to, so a
plain `shutil.which("uv")` fails although uv is there. The extra places are fixed directories under the home
directory and the system prefixes: nothing here runs a shell or reads a PATH from a request.
"""

import os
import shutil
from collections.abc import Iterable, Mapping
from pathlib import Path

UV_DOCS = "https://docs.astral.sh/uv/"


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
    return _unique(entry for entry in candidates if entry and Path(entry).is_absolute())


def search_path(environment: Mapping[str, str] | None = None) -> str:
    """The process PATH first, so a deliberate choice wins, then every standard uv directory it lacks."""
    source = os.environ if environment is None else environment
    current = [entry for entry in source.get("PATH", "").split(os.pathsep) if entry]
    return os.pathsep.join(_unique([*current, *standard_directories(source)]))


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


def _unique(entries: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for entry in entries:
        if entry not in seen:
            seen.append(entry)
    return seen
