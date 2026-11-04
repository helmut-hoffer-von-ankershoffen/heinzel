"""The machine the prerequisite engine looks at: platform, search path and the only ways it starts programs (ADR 0112).

Everything that touches the real machine (a search path, a file mode, a child process, Homebrew's location) goes
through one `ToolHost`, so tests describe a machine instead of patching the engine, and no test can reach the
Homebrew of the machine it runs on.
"""

import os
import shutil
import sys
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path

from heinzel.kernel import ProcessExecute, ProcessOutcome, run_bounded_process, subprocess_environment

BREW_LOCATIONS = ("/opt/homebrew/bin/brew", "/usr/local/bin/brew", "/home/linuxbrew/.linuxbrew/bin/brew")
"""Where Homebrew installs itself; its directory is often missing from the `PATH` of a launchd or GUI process."""
APPLE_SHIMS = "/usr/bin/"
"""Apple's developer-tool stubs: running one without the Command Line Tools opens a GUI installer."""
OUTPUT_LIMIT = 65536


def _which(name: str, path: str) -> str | None:
    return shutil.which(name, path=path)


def _is_executable(path: str) -> bool:
    candidate = Path(path)
    return candidate.is_file() and os.access(candidate, os.X_OK)


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolHost:
    """A machine as the engine sees it; every field has a real default and a fake in tests."""

    platform: str = sys.platform
    environment: MutableMapping[str, str] = field(default_factory=lambda: os.environ)
    execute: ProcessExecute = run_bounded_process
    which: Callable[[str, str], str | None] = _which
    is_executable: Callable[[str], bool] = _is_executable
    brew_locations: tuple[str, ...] = BREW_LOCATIONS

    @classmethod
    def system(cls) -> "ToolHost":
        """The real machine; the one place a test suite replaces to keep the real Homebrew out of reach."""
        return cls()

    @property
    def search_path(self) -> str:
        """The `PATH` programs are looked up in, including directories added by `add_to_path`."""
        return self.environment.get("PATH") or os.defpath

    def add_to_path(self, directory: str) -> None:
        """Append a directory to `PATH` for this process and what it starts; nothing is written to any shell file."""
        entries = [entry for entry in self.search_path.split(os.pathsep) if entry]
        if directory not in entries:
            self.environment["PATH"] = os.pathsep.join([*entries, directory])

    def run(self, command: list[str], timeout: float, flags: tuple[tuple[str, str], ...] = ()) -> ProcessOutcome:
        """Run a command without a shell or input, from the home directory, with `flags` added to the environment.

        Raises:
            OSError: If the program cannot be started.
        """
        env = subprocess_environment(self.environment)
        env.update(flags)
        return self.execute(
            command,
            input=None,
            cwd=self._working_directory(),
            env=env,
            timeout=timeout,
            limit=OUTPUT_LIMIT,
        )

    def _working_directory(self) -> Path:
        """The home directory, or the root when it does not exist, so a broken HOME never hides a tool."""
        home = Path(self.environment.get("HOME") or Path.home())
        return home if home.is_dir() else Path(home.anchor or "/")

    def find_brew(self) -> str | None:
        """Homebrew's executable on `PATH` or in its standard locations; None when it is not installed."""
        found = self.which("brew", self.search_path)
        for candidate in ([found] if found else []) + list(self.brew_locations):
            if self.is_executable(candidate):
                return candidate
        return None
