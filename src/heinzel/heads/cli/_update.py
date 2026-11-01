"""The update notice (ADR 0031): an hourly background check, and a line on stderr when a newer release is cached.

The check runs at most once an hour, on a background thread with a short timeout, and the command never waits for
more than `BUDGET` seconds of it. It stamps the cache before asking, so a check that fails, times out or is cut short
when the command ends also waits an hour: an offline machine doesn't ask on every command. The notice comes from the
cache only, after human output, on stderr, so pipes stay clean; never after JSON, in MCP or REST, or in CI.
"""

import os
import threading
import time
from dataclasses import dataclass
from datetime import timedelta

from rich.console import Console
from rich.text import Text

from heinzel import __version__
from heinzel.heads.cli._render import TerminalSafe
from heinzel.kernel import HeinzelError, Kernel, ReleaseCache, ReleaseSource

MAX_AGE = timedelta(hours=1)
"""How long an answer, or a failure, is good for."""
TIMEOUT = 2.0
"""Seconds the package source may take per network operation (connecting, reading)."""
BUDGET = 2.0
"""Seconds a command waits, at most, after its own work for a check still running; then the check is dropped."""
WHATS_NEW = "What's new: hz system changelog --newer"
"""Where the notice and the changelog header point people before they update."""
CI_VARIABLES = ("CI", "CONTINUOUS_INTEGRATION", "BUILD_NUMBER", "GITHUB_ACTIONS", "GITLAB_CI", "TF_BUILD")
"""Environment variables CI systems set; no check and no notice when one of them is."""


def enabled(kernel: Kernel) -> bool:
    """Whether the check and the notice are on: the `update_check` setting is, and no CI variable is set."""
    return kernel.settings.update_check and not any(os.environ.get(name) for name in CI_VARIABLES)


def is_terminal(console: Console) -> bool:
    """Whether `console` writes to a terminal, asked when printing; the seam tests fake."""
    return console.is_terminal


def notice(kernel: Kernel) -> str | None:
    """The notice that a newer release is available, from the cache only; None if there is none or it is off."""
    if not enabled(kernel):
        return None
    latest = ReleaseCache(kernel.settings.cache_dir).newer_than(__version__, kernel.settings.update_channel)
    return f"hz {latest} is available (installed {__version__}): hz system update\n{WHATS_NEW}" if latest else None


def print_notice(kernel: Kernel, console: Console) -> None:
    """Print the notice on `console` (the CLI's stderr), if it writes to a terminal and there is one to show."""
    if is_terminal(console) and (text := notice(kernel)):
        console.print(f"[yellow]{text}[/yellow]", highlight=False)


def changelog_header(kernel: Kernel) -> str | None:
    """The header of `hz system changelog` when a newer release is cached: installed, available, how to see more."""
    if not enabled(kernel) or not (
        latest := ReleaseCache(kernel.settings.cache_dir).newer_than(__version__, kernel.settings.update_channel)
    ):
        return None
    return f"You're on hz {__version__}; hz {latest} is available: hz system update. {WHATS_NEW}"


def print_changelog_header(kernel: Kernel, console: Console, result: object) -> None:
    """Print the changelog header on `console` (the CLI's stdout), if it writes to a terminal and there is one.

    Only above the installed changelog: `--newer` shows the newer releases themselves.
    """
    if isinstance(result, str) and is_terminal(console) and (text := changelog_header(kernel)):
        console.print(TerminalSafe(Text(text, style="yellow")), highlight=False)


@dataclass
class Check:
    """A running background check; `wait` gives it what is left of the budget."""

    thread: threading.Thread
    started: float

    def wait(self) -> None:
        """Wait until the check is done or the budget is spent, whichever comes first."""
        self.thread.join(max(0.0, self.started + BUDGET - time.monotonic()))


def start_check(kernel: Kernel) -> Check | None:
    """Ask the package source in the background if the check is on and the cache is older than `MAX_AGE`.

    None if no check was started: off, fresh, or no module provides a `ReleaseSource`.
    """
    if not enabled(kernel):
        return None
    cache = ReleaseCache(kernel.settings.cache_dir)
    if cache.is_fresh(MAX_AGE):
        return None
    try:
        source = kernel.resolve(ReleaseSource)
    except HeinzelError:
        return None
    cache.stamp(error=cache.error)  # before asking: a check cut short still waits an hour
    # A daemon thread: the command never waits longer than the budget, even if DNS hangs past the socket timeout.
    thread = threading.Thread(target=cache.refresh, args=(source, TIMEOUT), name="hz-update-check", daemon=True)
    thread.start()
    return Check(thread, time.monotonic())
