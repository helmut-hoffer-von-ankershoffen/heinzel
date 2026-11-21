"""Fakes of the ports in `heinzel.ports`, for tests that must not wait, spawn, ask the network or touch a manager.

Imported through `heinzel.testing`. Each fake is held to the same contract as its real adapter
(`tests/unit/ports_contract_test.py`), so a test that passes on a fake cannot pass where the real one would fail.
"""

from __future__ import annotations

import contextlib
import itertools
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from heinzel.kernel import EdgeRelease, ReleaseFacts

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterator, Mapping, Sequence
    from pathlib import Path

    from heinzel.ports import RunningProcess, ServiceJob

__all__ = [
    "FakeClock",
    "FakeJob",
    "FakeProcess",
    "FakeReleaseSource",
    "FakeServiceManager",
    "FakeStatus",
    "MemoryInstallationState",
    "ScriptedProcessRunner",
]


class FakeClock:
    """A clock the test moves: `sleep` advances it at once, nothing waits."""

    def __init__(self, start: datetime | None = None) -> None:
        """Start at `start` (a fixed instant by default) and at monotonic zero."""
        self._now = start or datetime(2026, 1, 1, tzinfo=UTC)
        self._monotonic = 0.0
        self.sleeps: list[float] = []
        """Every wait that was asked for, in order."""

    def now(self) -> datetime:
        """The fake wall time."""
        return self._now

    def monotonic(self) -> float:
        """The fake monotonic reading."""
        return self._monotonic

    def advance(self, seconds: float) -> None:
        """Let `seconds` pass for everyone who asks this clock."""
        self._monotonic += seconds
        self._now += timedelta(seconds=seconds)

    def sleep(self, seconds: float) -> None:
        """Record the wait and advance by it."""
        self.sleeps.append(seconds)
        self.advance(max(0.0, seconds))

    def deadline(self, seconds: float) -> float:
        """The monotonic reading `seconds` from now."""
        return self._monotonic + seconds


@dataclass
class FakeProcess:
    """A scripted child: what it printed, how it ended, and what was done to it."""

    pid: int
    stdout: object | None = None
    returncode: int | None = None
    signals: list[int] = field(default_factory=list[int])
    killed: bool = False

    def poll(self) -> int | None:
        """The exit code once the child ended."""
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        """The exit code; a child that was never ended times out like a real one.

        Raises:
            subprocess.TimeoutExpired: If the child was not ended.
        """
        if self.returncode is None:
            command = "fake"
            raise subprocess.TimeoutExpired(command, timeout or 0)
        return self.returncode

    def send_signal(self, sig: int) -> None:
        """Record the signal; any signal ends the child."""
        self.signals.append(sig)
        self.returncode = -sig

    def terminate(self) -> None:
        """End the child."""
        self.send_signal(15)

    def kill(self) -> None:
        """End the child at once."""
        self.killed = True
        self.returncode = -9


class ScriptedProcessRunner:
    """Answers commands from a script: the first rule whose prefix starts the command decides; none, exit 127."""

    def __init__(self) -> None:
        """Start with no rules and no history."""
        self._rules: list[tuple[tuple[str, ...], Callable[[Sequence[str]], subprocess.CompletedProcess[str]]]] = []
        self.calls: list[tuple[str, ...]] = []
        """Every command that was run, spawned or exec'd, in order."""
        self.spawned: list[FakeProcess] = []
        self._pids = itertools.count(1000)

    def on(self, *prefix: str, returncode: int = 0, stdout: str = "", stderr: str = "") -> ScriptedProcessRunner:
        """Answer commands that start with `prefix`; returns self so rules chain."""
        self._rules.append(
            (prefix, lambda command: subprocess.CompletedProcess(list(command), returncode, stdout, stderr))
        )
        return self

    def raises(self, *prefix: str, error: BaseException) -> ScriptedProcessRunner:
        """Make commands that start with `prefix` raise `error` (a missing program, a timeout)."""

        def fail(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            del command
            raise error

        self._rules.append((prefix, fail))
        return self

    def run(
        self, command: Sequence[str], *, timeout: float, env: Mapping[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        """The scripted answer for `command`."""
        del timeout, env
        self.calls.append(tuple(command))
        for prefix, answer in self._rules:
            if tuple(command[: len(prefix)]) == prefix:
                return answer(command)
        return subprocess.CompletedProcess(list(command), 127, "", "no scripted answer")

    def spawn(
        self, command: Sequence[str], *, env: Mapping[str, str] | None = None, capture: bool = False
    ) -> RunningProcess:
        """A scripted child whose output is what the matching rule's stdout says."""
        del env
        self.calls.append(tuple(command))
        process = FakeProcess(next(self._pids))
        if capture:
            import io  # noqa: PLC0415 - only a capturing spawn needs a stream

            text = ""
            for prefix, answer in self._rules:
                if tuple(command[: len(prefix)]) == prefix:
                    text = answer(command).stdout
                    break
            process.stdout = io.StringIO(text)
        self.spawned.append(process)
        return process  # type: ignore[return-value]

    def exec(self, command: Sequence[str], *, env: Mapping[str, str] | None = None) -> None:
        """Record the replacement; the test process lives on."""
        del env
        self.calls.append(tuple(command))


@dataclass(frozen=True)
class FakeJob:
    """A job by its label."""

    label: str


@dataclass(frozen=True)
class FakeStatus:
    """What the fake manager says about a job."""

    loaded: bool
    pid: int | None
    version: str | None


class FakeServiceManager:
    """Jobs in memory, each with a pid and the version of the code it runs; a restart gives a new pid."""

    def __init__(self, version: str = "0.0.0") -> None:
        """Every job that loads runs `version` until `upgrade` says otherwise."""
        self.version = version
        self._jobs: dict[str, tuple[int, str]] = {}
        self._pids = itertools.count(100)
        self.calls: list[tuple[str, str, bool]] = []
        """(verb, label, force) for each request, in order."""

    def upgrade(self, version: str) -> None:
        """New code is installed: a restart picks it up, a running process keeps what it has."""
        self.version = version

    def load(self, job: ServiceJob) -> None:
        """Register and start the job."""
        self.calls.append(("load", job.label, False))
        self._jobs[job.label] = (next(self._pids), self.version)

    def kickstart(self, job: ServiceJob, *, force: bool = False) -> None:
        """Restart a loaded job; a job that is not loaded is refused like the real managers refuse it.

        Raises:
            OSError: If the job is not loaded.
        """
        self.calls.append(("kickstart", job.label, force))
        if job.label not in self._jobs:
            msg = f"{job.label} is not loaded"
            raise OSError(msg)
        self._jobs[job.label] = (next(self._pids), self.version)

    def status(self, job: ServiceJob) -> FakeStatus:
        """Loaded, pid and version of the job."""
        if job.label not in self._jobs:
            return FakeStatus(loaded=False, pid=None, version=None)
        pid, version = self._jobs[job.label]
        return FakeStatus(loaded=True, pid=pid, version=version)

    def unload(self, job: ServiceJob) -> None:
        """Take a job away without a request (the user removed it)."""
        self._jobs.pop(job.label, None)


class FakeReleaseSource:
    """Releases the test publishes; asking never leaves the process."""

    def __init__(self, latest: str, *, published: datetime | None = None) -> None:
        """Publish `latest` as the only release, accepted, published at `published`."""
        self._facts: dict[str, ReleaseFacts] = {}
        self._accepted: set[str] = set()
        self._requires: dict[str, str] = {}
        self._latest = latest
        self._edge: EdgeRelease | None = None
        self.publish(latest, published=published)

    def publish(self, version: str, *, published: datetime | None = None, accepted: bool = True) -> None:
        """A new release appears and is the latest."""
        self._facts[version] = ReleaseFacts(
            version, published or datetime(2026, 1, 1, tzinfo=UTC), f"{version:0<40}"[:40]
        )
        if accepted:
            self._accepted.add(version)
        self._latest = version

    def requires(self, version: str, specifier: str) -> None:
        """Release `version` needs a Python matching `specifier`."""
        self._requires[version] = specifier

    def latest_version(self, timeout: float) -> str:
        """The release published last."""
        del timeout
        return self._latest

    def highest_version(self, timeout: float, *, prereleases: bool = False) -> str:
        """The highest published version, in version order."""
        del timeout, prereleases
        from packaging.version import Version  # noqa: PLC0415 - only this one method orders versions

        return str(max(Version(v) for v in self._facts))

    def edge_release(self, timeout: float) -> EdgeRelease:
        """The edge release the test set with `set_edge`.

        Raises:
            OSError: If none was set, like an unreachable pointer.
        """
        del timeout
        if self._edge is None:
            msg = "no edge release"
            raise OSError(msg)
        return self._edge

    def set_edge(self, version: str, *, sha256: str = "0" * 64) -> None:
        """Publish an edge release."""
        self._edge = EdgeRelease(version, f"https://example.invalid/heinzel-{version}.whl", sha256)

    def release_facts(self, version: str, timeout: float) -> ReleaseFacts:
        """The facts of a published release.

        Raises:
            OSError: If it was never published, like a missing tag.
        """
        del timeout
        if version not in self._facts:
            msg = f"no release {version}"
            raise OSError(msg)
        return self._facts[version]

    def accepted(self, version: str, timeout: float) -> bool:
        """Whether the release passed acceptance."""
        del timeout
        return version in self._accepted

    def requires_python(self, version: str, timeout: float) -> str | None:
        """The Python the release needs, if the test said."""
        del timeout
        return self._requires.get(version)


class MemoryInstallationState:
    """A state folder in memory: locks and journals shared by every "server" that is given the same instance."""

    def __init__(self, folder: Path) -> None:
        """Use `folder` as the state folder's path; its content is not touched."""
        self._folder = folder
        self._locks: dict[str, threading.RLock] = {}
        self._journals: dict[str, list[str]] = {}
        self._guard = threading.Lock()

    @property
    def folder(self) -> Path:
        """The state folder's path."""
        return self._folder

    def lock(self, name: str) -> contextlib.AbstractContextManager[None]:
        """The lock `name`; a second holder waits."""
        with self._guard:
            lock = self._locks.setdefault(name, threading.RLock())

        @contextlib.contextmanager
        def held() -> Generator[None]:
            with lock:
                yield

        return held()

    def journal(self, name: str) -> Iterator[str]:
        """The lines of journal `name`."""
        return iter(list(self._journals.get(name, [])))

    def append(self, name: str, line: str) -> None:
        """Add a line to journal `name`."""
        with self._guard:
            self._journals.setdefault(name, []).append(line)
