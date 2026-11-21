"""The named ports through which code that updates or restarts Heinzelmännchen reaches the world.

Protocols only; they import nothing of ours but the spec types they return. A real adapter lives in an `_adapter*.py`
of the context that owns it, a fake in `heinzel.testing`, and one contract test per port holds both to the same
behaviour. A plugin gets a port injected by type, like a module gets a service (ADR 0026: this file is public).
"""

from __future__ import annotations

from typing import IO, TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import subprocess
    from collections.abc import Iterator, Mapping, Sequence
    from contextlib import AbstractContextManager
    from datetime import datetime
    from pathlib import Path

    from heinzel.kernel import EdgeRelease, ReleaseFacts

__all__ = [
    "Clock",
    "InstallationState",
    "ProcessRunner",
    "ReleaseSource",
    "RunningProcess",
    "ServiceJob",
    "ServiceManager",
    "ServiceStatus",
]


@runtime_checkable
class Clock(Protocol):
    """Time, as a test can advance it: wall clock, a monotonic reading, sleeping and a deadline."""

    def now(self) -> datetime:
        """The current time, timezone-aware, in UTC."""
        ...

    def monotonic(self) -> float:
        """Seconds on a clock that never goes back, for measuring how long something took."""
        ...

    def sleep(self, seconds: float) -> None:
        """Wait `seconds`; a fake advances its own time instead."""
        ...

    def deadline(self, seconds: float) -> float:
        """The monotonic reading `seconds` from now: wait until `monotonic()` reaches it."""
        ...


@runtime_checkable
class RunningProcess(Protocol):
    """A child that was started and is still being watched; the part of `subprocess.Popen` callers use."""

    pid: int
    stdout: IO[str] | None

    def poll(self) -> int | None:
        """The exit code, or None while the child runs."""
        ...

    def wait(self, timeout: float | None = None) -> int:
        """Wait for the child and return its exit code; raises `subprocess.TimeoutExpired` when it takes too long."""
        ...

    def send_signal(self, sig: int) -> None:
        """Send the child a signal (SIGINT asks a server to shut down in order)."""
        ...

    def terminate(self) -> None:
        """Ask the child to stop."""
        ...

    def kill(self) -> None:
        """Make the child stop."""
        ...


@runtime_checkable
class ProcessRunner(Protocol):
    """Starting programs: run one to its end, spawn one and watch it, or replace this process by one."""

    def run(
        self, command: Sequence[str], *, timeout: float, env: Mapping[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run `command` to its end with captured text output and no input; never raises on a non-zero exit.

        Raises:
            OSError: If the program can't be started.
            subprocess.TimeoutExpired: If it runs longer than `timeout` seconds.
        """
        ...

    def spawn(
        self, command: Sequence[str], *, env: Mapping[str, str] | None = None, capture: bool = False
    ) -> RunningProcess:
        """Start `command` without waiting; with `capture` its standard output is readable as text.

        Raises:
            OSError: If the program can't be started.
        """
        ...

    def exec(self, command: Sequence[str], *, env: Mapping[str, str] | None = None) -> None:
        """Replace this process by `command`; a fake records it and returns."""
        ...


class ServiceJob(Protocol):
    """One job a service manager owns: what to call it."""

    @property
    def label(self) -> str:
        """The launchd label or systemd unit name."""
        ...


class ServiceStatus(Protocol):
    """What a service manager says about a job."""

    @property
    def loaded(self) -> bool:
        """Whether the manager knows the job."""
        ...

    @property
    def pid(self) -> int | None:
        """The process id while the job runs."""
        ...

    @property
    def version(self) -> str | None:
        """The Heinzelmännchen version the job's process reports; None when it doesn't say or isn't running."""
        ...


@runtime_checkable
class ServiceManager(Protocol):
    """launchd, systemd or a foreground process that nothing supervises."""

    def load(self, job: ServiceJob) -> None:
        """Register `job` and start it.

        Raises:
            OSError: If the manager refuses.
        """
        ...

    def kickstart(self, job: ServiceJob, *, force: bool = False) -> None:
        """Restart `job` now; with `force` even when it is running (`launchctl kickstart -k`, `systemctl restart`).

        Without `force` a job that is not running stays stopped (`systemctl try-restart`).

        Raises:
            OSError: If the manager refuses or the job is not loaded.
        """
        ...

    def status(self, job: ServiceJob) -> ServiceStatus:
        """Whether `job` is loaded, its pid and the version it runs."""
        ...


@runtime_checkable
class ReleaseSource(Protocol):
    """Where releases come from: the newest, the facts about one, whether it passed acceptance."""

    def latest_version(self, timeout: float) -> str:
        """The version of the latest release."""
        ...

    def highest_version(self, timeout: float, *, prereleases: bool = False) -> str:
        """The highest published version among the newest releases, in version order rather than by date."""
        ...

    def edge_release(self, timeout: float) -> EdgeRelease:
        """The newest edge release; asked only after someone chose the edge channel."""
        ...

    def release_facts(self, version: str, timeout: float) -> ReleaseFacts:
        """When `version` was published and which commit its tag names."""
        ...

    def accepted(self, version: str, timeout: float) -> bool:
        """Whether `version` passed its acceptance run."""
        ...

    def requires_python(self, version: str, timeout: float) -> str | None:
        """The `Requires-Python` of `version`, or None when the source can't tell."""
        ...


@runtime_checkable
class InstallationState(Protocol):
    """The state folder of one installation: where it is, who holds its lock, what the journal says."""

    @property
    def folder(self) -> Path:
        """The state folder; created on first use, readable and writable by this user only."""
        ...

    def lock(self, name: str, wait: float | None = None) -> AbstractContextManager[None]:
        """Hold the exclusive lock `name` in the state folder; a second holder waits, in any process.

        With `wait`, it waits at most that many seconds and then raises `TimeoutError`; without, as long as it takes.
        """
        ...

    def journal(self, name: str) -> Iterator[str]:
        """The lines of journal `name`, oldest first; none if it was never written."""
        ...

    def append(self, name: str, line: str) -> None:
        """Add one line to journal `name`, durably: a reader in another process sees it at once."""
        ...

    def home_file(self, relative: str) -> str | None:
        """The text of a file the installation keeps under the person's home folder (the desktop app's receipt).

        None when it does not exist or cannot be read. `relative` may not leave the home folder by its own path (`..`
        or an absolute path); a symlink under home (a dotfiles `~/.config`) is followed wherever it points.
        """
        ...
