"""The real adapters of the `ServiceManager` port: launchd, systemd and a foreground process nothing supervises.

The command lines are exactly the ones the web service and the daemon handover ran before the port existed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import subprocess
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

    from heinzel.ports import ProcessRunner, RunningProcess, ServiceJob

__all__ = ["ForegroundServiceManager", "Job", "LaunchdServiceManager", "Status", "SystemdServiceManager"]

STOP_SECONDS = 5.0


@dataclass(frozen=True)
class Job:
    """A job by its launchd label or systemd unit; `definition` is the plist launchd loads."""

    label: str
    definition: Path | None = None


@dataclass(frozen=True)
class Status:
    """What a manager says about a job."""

    loaded: bool
    pid: int | None = None
    version: str | None = None


def _refusal(what: str, result: subprocess.CompletedProcess[str]) -> str:
    detail = (result.stderr or result.stdout).strip()
    return f"The service manager could not {what} ({detail or f'exit {result.returncode}'})."


class LaunchdServiceManager:
    """launchd for one user session: `gui/<uid>`."""

    def __init__(
        self,
        runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]],
        *,
        uid: int,
        launchctl: str = "launchctl",
    ) -> None:
        """Send every command through `runner`; `launchctl` is the executable it resolves."""
        self._run, self._uid, self._launchctl = runner, uid, launchctl

    def load(self, job: ServiceJob) -> None:
        """`launchctl bootstrap gui/<uid> <plist>`.

        Raises:
            OSError: If the job has no plist or launchd refuses.
        """
        definition = getattr(job, "definition", None)
        if definition is None:
            msg = f"{job.label} has no plist to load"
            raise OSError(msg)
        result = self._run([self._launchctl, "bootstrap", f"gui/{self._uid}", str(definition)])
        if result.returncode != 0:
            msg = _refusal("load the job", result)
            raise OSError(msg)

    def kickstart(self, job: ServiceJob, *, force: bool = False) -> None:
        """`launchctl kickstart [-k] gui/<uid>/<label>`.

        Raises:
            OSError: If launchd refuses.
        """
        flags = ["-k"] if force else []
        result = self._run([self._launchctl, "kickstart", *flags, f"gui/{self._uid}/{job.label}"])
        if result.returncode != 0:
            msg = _refusal("restart the job", result)
            raise OSError(msg)

    def status(self, job: ServiceJob) -> Status:
        """Loaded when `launchctl print` knows the job; its pid when it says one."""
        result = self._run([self._launchctl, "print", f"gui/{self._uid}/{job.label}"])
        if result.returncode != 0:
            return Status(loaded=False)
        pid = next((int(w.split("=")[1]) for w in result.stdout.split("\n") if w.strip().startswith("pid = ")), None)
        return Status(loaded=True, pid=pid)


class SystemdServiceManager:
    """systemd in the user's own instance: `systemctl --user`."""

    def __init__(
        self, runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]], *, systemctl: str = "systemctl"
    ) -> None:
        """Send every command through `runner`; `systemctl` is the executable it resolves."""
        self._run, self._systemctl = runner, systemctl

    def load(self, job: ServiceJob) -> None:
        """`systemctl --user enable --now <unit>`.

        Raises:
            OSError: If systemd refuses.
        """
        result = self._run([self._systemctl, "--user", "enable", "--now", job.label])
        if result.returncode != 0:
            msg = _refusal("load the job", result)
            raise OSError(msg)

    def kickstart(self, job: ServiceJob, *, force: bool = False) -> None:
        """`systemctl --user restart <unit>`, or `try-restart` when it must not start a stopped job.

        Raises:
            OSError: If systemd refuses.
        """
        verb = "restart" if force else "try-restart"
        result = self._run([self._systemctl, "--user", verb, job.label])
        if result.returncode != 0:
            msg = _refusal("restart the job", result)
            raise OSError(msg)

    def status(self, job: ServiceJob) -> Status:
        """Loaded when the unit is known; its main pid when it runs."""
        result = self._run([self._systemctl, "--user", "show", "--property=LoadState,MainPID", job.label])
        fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        loaded = result.returncode == 0 and fields.get("LoadState") == "loaded"
        pid = int(fields.get("MainPID", "0") or 0) or None
        return Status(loaded=loaded, pid=pid)


class ForegroundServiceManager:
    """Jobs that are plain child processes of this one, started and restarted through a `ProcessRunner`."""

    def __init__(
        self, commands: Mapping[str, Sequence[str]], runner: ProcessRunner, *, version: str | None = None
    ) -> None:
        """`commands` says what each label runs; `version` is what the children report, if anyone knows."""
        self._commands, self._runner, self._version = commands, runner, version
        self._children: dict[str, RunningProcess] = {}

    def load(self, job: ServiceJob) -> None:
        """Start the job's command.

        Raises:
            OSError: If the label is unknown or the program does not start.
        """
        if job.label not in self._commands:
            msg = f"{job.label} has no command"
            raise OSError(msg)
        self._children[job.label] = self._runner.spawn(self._commands[job.label])

    def kickstart(self, job: ServiceJob, *, force: bool = False) -> None:
        """Stop a running child and start a new one; a stopped job starts only with `force`.

        Raises:
            OSError: If the job was never loaded.
        """
        child = self._children.get(job.label)
        if child is None:
            msg = f"{job.label} is not loaded"
            raise OSError(msg)
        running = child.poll() is None
        if running:
            child.terminate()
            child.wait(STOP_SECONDS)
        if running or force:
            self._children[job.label] = self._runner.spawn(self._commands[job.label])

    def status(self, job: ServiceJob) -> Status:
        """Loaded when the child exists; its pid while it runs."""
        child = self._children.get(job.label)
        if child is None:
            return Status(loaded=False)
        return Status(loaded=True, pid=child.pid if child.poll() is None else None, version=self._version)

    def stop_all(self) -> None:
        """End every child, for a caller that is going away."""
        for child in self._children.values():
            if child.poll() is None:
                child.kill()
                child.wait(STOP_SECONDS)
