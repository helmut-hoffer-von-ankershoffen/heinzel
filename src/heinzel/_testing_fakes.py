"""Fakes of the ports in `heinzel.ports`, for tests that must not wait, spawn, ask the network or touch a manager.

Imported through `heinzel.testing`. Each fake is held to the same contract as its real adapter
(`tests/unit/ports_contract_test.py`), so a test that passes on a fake cannot pass where the real one would fail.
"""

from __future__ import annotations

import contextlib
import importlib
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

    from heinzel.modules.system.updater._announced import Announcement
    from heinzel.ports import RunningProcess, ServiceJob

__all__ = [
    "FakeClock",
    "FakeInstallation",
    "FakeJob",
    "FakeProcess",
    "FakeReleaseSource",
    "FakeServer",
    "FakeServiceManager",
    "FakeStatus",
    "MemoryInstallationState",
    "ScriptedProcessRunner",
    "install_fake_release",
    "installation",
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

    def __init__(self, folder: Path, home_files: Mapping[str, str] | None = None) -> None:
        """Use `folder` as the state folder's path (its content is not touched); `home_files` are files under home."""
        self._folder = folder
        self._home_files = dict(home_files or {})
        self._locks: dict[str, threading.RLock] = {}
        self._journals: dict[str, list[str]] = {}
        self._guard = threading.Lock()

    @property
    def folder(self) -> Path:
        """The state folder's path."""
        return self._folder

    def lock(self, name: str, wait: float | None = None) -> contextlib.AbstractContextManager[None]:
        """The lock `name`; a second holder waits, with `wait` at most that long (then `TimeoutError`)."""
        with self._guard:
            lock = self._locks.setdefault(name, threading.RLock())

        @contextlib.contextmanager
        def held() -> Generator[None]:
            if not lock.acquire(timeout=-1 if wait is None else wait):
                msg = "the lock is still held by another holder"
                raise TimeoutError(msg)
            try:
                yield
            finally:
                lock.release()

        return held()

    def journal(self, name: str) -> Iterator[str]:
        """The lines of journal `name`."""
        return iter(list(self._journals.get(name, [])))

    def home_file(self, relative: str) -> str | None:
        """The text the test put under home at `relative`, or None."""
        return self._home_files.get(relative)

    def append(self, name: str, line: str) -> None:
        """Add a line to journal `name`."""
        with self._guard:
            self._journals.setdefault(name, []).append(line)


@dataclass
class FakeServer:
    """One `hz ui serve` process on the installation: how it was started, what it runs, how old it is."""

    kind: str
    """`launchd` (the supervised web job), `desktop` (spawned by the desktop app) or `stale` (left running)."""
    version: str
    started_days_ago: float = 0.0
    job: FakeJob | None = None
    """The manager's job of a `launchd` server."""


class FakeInstallation:
    """One installation, several servers: they share one state folder and see the same installed version."""

    def __init__(self, folder: Path, servers: Sequence[FakeServer], installed: str, web_label: str = "web") -> None:
        """Build the shared ports: a clock, a manager, a release source and the state folder every server uses."""
        self.state = MemoryInstallationState(folder)
        self.clock = FakeClock()
        self.manager = FakeServiceManager(installed)
        self.releases = FakeReleaseSource(installed)
        self.installed = installed
        self.servers = list(servers)
        for server in self.servers:
            if server.kind == "launchd":
                server.job = FakeJob(web_label)
                self.manager.upgrade(server.version)  # the job started on the version it reports
                self.manager.load(server.job)
        self.manager.upgrade(installed)

    def upgrade(self, version: str) -> None:
        """Another path (the daemon, the CLI, a footer in another window) installs `version` under every server.

        Running processes keep what they imported; a restart picks the installed version up.
        """
        self.installed = version
        self.releases.publish(version)
        self.manager.upgrade(version)

    def running(self, server: FakeServer) -> str:
        """The version `server` runs now: its own until it restarts."""
        if server.job is not None:
            version = self.manager.status(server.job).version
            return version or server.version
        return server.version


def installation(
    folder: Path, *, servers: Sequence[Mapping[str, object] | str], installed: str | None = None, web_label: str = "web"
) -> FakeInstallation:
    """One installation with several servers of different versions on one state folder.

    `servers` lists them as `{"kind": "launchd" | "desktop" | "stale", "version": "0.9.51", "started_days_ago": 5}` or
    as a bare kind (running the installed version). `installed` is what is installed under them, by default the highest
    version any of them runs; `web_label` names the supervised job. A web job, a desktop-spawned server and a stale
    one on one state folder is what hit the Mini on 2026-11-21.
    """
    versions = [str(s["version"]) for s in servers if not isinstance(s, str) and "version" in s]
    from packaging.version import Version  # noqa: PLC0415 - only this builder orders versions

    top = installed or (max(versions, key=Version) if versions else "0.0.0")
    built = [
        FakeServer(s, top)
        if isinstance(s, str)
        else FakeServer(
            str(s["kind"]),
            str(s.get("version", top)),
            float(s.get("started_days_ago", 0)),  # type: ignore[arg-type]
        )
        for s in servers
    ]
    return FakeInstallation(folder, built, top, web_label)


def install_fake_release(folder: Path) -> None:  # noqa: C901 - one patch of the running installation
    """Install the fake newer release of the automatic-update end-to-end test (ADR 0116); nothing is fetched.

    `tests/fixtures/auto_update/fake_release.py` calls it when `python -m heinzel` starts in that test (the module is
    free of pytest, so the tool-installed hz can import it). Once the test writes `release` in `folder`, the newest
    stable release is 99.0.0 and a plugin has 9.0.0 (which needs no other plugin), until an update "installs" them; the
    server's clock runs ahead by the seconds in `clock` there; the installation counts as a uv tool on a release tag.
    """
    from heinzel import __version__  # noqa: PLC0415 - these fakes patch the running installation, so it is imported late

    _schedule = importlib.import_module("heinzel.modules.system.updater._schedule")
    from heinzel.kernel import CorePin, Installation, PluginNews, PluginRelease, ReleaseFacts  # noqa: PLC0415 - these fakes patch the running installation, so it is imported late

    _service = importlib.import_module("heinzel.modules.system._service")
    from heinzel.modules.system._models import AnnouncedUpdate  # noqa: PLC0415 - these fakes patch the running installation, so it is imported late

    _plugin_releases = importlib.import_module("heinzel.modules.system.updater._plugin_releases")
    _update = importlib.import_module("heinzel.modules.system.updater._update")

    long_ago = datetime(2026, 1, 1, tzinfo=UTC)  # old enough for any minimum age
    applied = folder / "applied"

    def released() -> bool:
        return (folder / "release").exists() and not applied.exists()

    seen: list[bool] = []
    """hz 99.0.0 was answered: the plugin's news comes no sooner, so one check never finds the plugin alone (the
    server asks about hz first, then the plugins, and the test may publish the release between the two)."""

    def latest_version(self: object, timeout: float) -> str:  # noqa: ARG001
        if released():
            seen.append(True)
            return "99.0.0"
        return __version__

    def newer(self: object, timeout: float) -> dict[str, str]:  # noqa: ARG001
        return {"heinzel-example": "9.0.0"} if released() and seen else {}

    def newer_releases(self: object, timeout: float) -> dict[str, PluginRelease]:
        return {name: PluginRelease(version, long_ago) for name, version in newer(self, timeout).items()}

    def release_facts(self: object, version: str, timeout: float) -> ReleaseFacts:  # noqa: ARG001
        return ReleaseFacts(version, long_ago, None)

    def announceable(self: object, timeout: float, old_enough: Callable[[PluginRelease], bool]) -> PluginNews:
        """The fake plugin release needs nothing, so it moves alone; its closure has its own unit tests."""
        return PluginNews({name: r for name, r in newer_releases(self, timeout).items() if old_enough(r)})

    def release(self: object, name: str, version: str, timeout: float) -> PluginRelease:  # noqa: ARG001
        return PluginRelease(version, long_ago)

    def apply_announced(self: object, announced: Announcement, **kwargs: Callable[[str], bool]) -> AnnouncedUpdate:  # noqa: ARG001
        """Instead of uv: record what would be installed, then the real trial start of this hz."""
        what = f"{announced.core} {dict(announced.plugins)}"
        applied.write_text(f"fake update applied: {what}\n", encoding="utf-8")
        started = kwargs["trial"](__version__)
        return AnnouncedUpdate(
            updated=started, after=__version__, restored=not started, refused=not started, message="Fake update."
        )

    def accepted(self: object, version: str, timeout: float) -> bool:  # noqa: ARG001
        return True

    def now() -> datetime:
        try:
            ahead = float((folder / "clock").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            ahead = 0.0
        return datetime.now(UTC) + timedelta(seconds=ahead)

    def core_pin(self: object) -> CorePin:  # noqa: ARG001
        return CorePin("release", "heinzel", ref="v0.1.0", git="https://github.com/org/heinzel")

    _service.GitHubReleases.latest_version = latest_version
    _plugin_releases.PyPIPluginReleases.newer = newer
    _plugin_releases.PyPIPluginReleases.newer_releases = newer_releases
    _service.GitHubReleases.release_facts = release_facts
    _service.GitHubReleases.accepted = accepted
    _update.UpdateService.apply_announced = apply_announced
    _plugin_releases.PyPIPluginReleases.release = release
    _plugin_releases.PyPIPluginReleases.announceable = announceable
    setattr(_schedule, "_now", now)  # noqa: B010 - a name this module does not declare
    setattr(_schedule, "FIRST_CHECK", (timedelta(seconds=1), timedelta(seconds=2)))  # noqa: B010 - a name this module does not declare
    setattr(_schedule, "CHECK_EVERY", timedelta(seconds=2))  # noqa: B010 - a name this module does not declare
    setattr(_schedule, "JITTER", timedelta(0))  # noqa: B010 - a name this module does not declare

    Installation.core_pin = core_pin  # type: ignore[method-assign]
