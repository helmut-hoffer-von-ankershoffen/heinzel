"""Service of the daemon module: install the OS unit, and tick: run the jobs that are due and write receipts.

The OS supervises and the daemon ticks (ADR 0020): launchd or systemd starts `hz system daemon tick` every few
minutes, a short-lived process that survives `hz system update`, crashes and sleep by construction.
"""

import json
import logging
import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

from heinzel import CatchUp, Check, HeinzelError, JobLet, Kernel, Settings, Status
from heinzel.modules.system.daemon import _schedule as schedule
from heinzel.modules.system.daemon._ledger import Ledger
from heinzel.modules.system.daemon._models import (
    DaemonSettings,
    DaemonStatus,
    Disposition,
    Installed,
    JobInfo,
    JobRun,
    LogLine,
    TickReport,
    Toggled,
    Uninstalled,
    Wake,
)
from heinzel.modules.system.daemon._runtime import Runner, TickLog, TimeLimit, lock, run, tail, within
from heinzel.modules.system.daemon._units import (
    LABEL,
    SYSTEMD_NAME,
    check_interval,
    launchd_plist,
    plist_path,
    systemd_paths,
    systemd_service,
    systemd_timer,
)

DEFAULT_INTERVAL = 5
"""Minutes between ticks unless `install --interval` says otherwise."""
STALE_TICKS = 3
"""A daemon that missed this many intervals in a row is reported as degraded."""
PLATFORMS = ("darwin", "linux")


def _now() -> datetime:
    return datetime.now().astimezone()


@dataclass(frozen=True, slots=True)
class Job:
    """A registered job under its id, `<module>:<job>`."""

    id: str
    let: JobLet


class DaemonService:
    """Installs, inspects and pauses the daemon, and runs ticks."""

    def __init__(  # noqa: PLR0913, PLR0917 - the seams tests replace, all defaulted
        self,
        kernel: Kernel,
        settings: Settings,
        daemon: DaemonSettings,
        runner: Runner = run,
        platform: str = sys.platform,
        uid: Callable[[], int] = os.getuid,
        clock: Callable[[], datetime] = _now,
        which: Callable[[str], str | None] = shutil.which,
        time_limit: TimeLimit = within,
    ) -> None:
        """Create the service; the kernel and settings are injected, the rest is for tests.

        Args:
            kernel: Finds the jobs and injects their parameters.
            settings: Kernel settings: where receipts, state and logs live.
            daemon: Where the units go.
            runner: Runs launchctl, systemctl and uv.
            platform: `sys.platform`, to pick launchd or systemd.
            uid: The user id launchd's GUI domain is named after.
            clock: The current time, aware of the local time zone.
            which: Finds executables.
            time_limit: Runs a job with a timeout and returns its error.
        """
        self._kernel = kernel
        self._runner = runner
        self._platform = platform
        self._uid = uid
        self._clock = clock
        self._which = which
        self._time_limit = time_limit
        self.unit_dir = daemon.unit_dir
        self.receipts_dir = settings.data_dir / "receipts"
        self.state_dir = settings.state_dir / "daemon"
        self.log_path = settings.log_dir / "daemon.jsonl"

    # Installing ------------------------------------------------------------------------------------------------------

    def install(self, interval: timedelta, *, dry_run: bool = False) -> Installed:
        """Write the unit for this platform and load it; `dry_run` only says what it would write.

        Raises:
            HeinzelError: On an unsupported platform or interval, without a uv tool installation, or if loading fails.
        """
        minutes = self._minutes(interval)
        executable = self._shim(dry_run=dry_run)
        files = self._unit_files(executable, minutes)
        commands = self._load_commands()
        if dry_run:
            return Installed(
                platform=self._platform,
                executable=executable,
                interval_minutes=minutes,
                files=files,
                commands=[],
                dry_run=True,
            )
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        for path, content in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        self._write_state(
            "install.json",
            {
                "interval_minutes": minutes,
                "executable": str(executable),
                "platform": self._platform,
                "installed_at": self._clock().isoformat(),
            },
        )
        if self._platform == "darwin":  # bootstrap fails on a loaded agent; unload the old one, if any, first
            self._runner(self._command("launchctl", "bootout", f"gui/{self._uid()}/{LABEL}"))
        for command in commands:
            result = self._runner(command)
            if result.returncode != 0:
                msg = f"`{' '.join(command)}` failed: {(result.stderr or result.stdout).strip()}"
                raise HeinzelError(msg)
        return Installed(
            platform=self._platform,
            executable=executable,
            interval_minutes=minutes,
            files=files,
            commands=commands,
            dry_run=False,
        )

    def uninstall(self) -> Uninstalled:
        """Unload and remove the unit; receipts, logs and the pause flag stay. Nothing to do is not an error.

        Raises:
            HeinzelError: On an unsupported platform.
        """
        paths = [path for path in self._unit_paths() if path.exists()]
        if not paths:
            return Uninstalled(removed=[], commands=[])
        if self._platform == "darwin":
            commands = [self._command("launchctl", "bootout", f"gui/{self._uid()}/{LABEL}")]
        else:
            commands = [self._command("systemctl", "--user", "disable", "--now", f"{SYSTEMD_NAME}.timer")]
        for command in commands:  # failing because it wasn't loaded is fine: the goal is that it isn't
            self._runner(command)
        for path in paths:
            path.unlink()
        if self._platform == "linux":
            reload = self._command("systemctl", "--user", "daemon-reload")
            self._runner(reload)
            commands.append(reload)
        (self.state_dir / "install.json").unlink(missing_ok=True)
        return Uninstalled(removed=paths, commands=commands)

    # Inspecting ------------------------------------------------------------------------------------------------------

    def status(self) -> DaemonStatus:
        """Installed, loaded, paused, last tick and failing jobs; runs launchctl or systemctl only when installed."""
        installed = self._platform in PLATFORMS and all(path.exists() for path in self._unit_paths())
        recorded = self._read_state("install.json")
        jobs = self.jobs()
        return DaemonStatus(
            platform=self._platform,
            installed=installed,
            loaded=installed and self._loaded(),
            paused=self.paused,
            interval_minutes=int(recorded["interval_minutes"]) if "interval_minutes" in recorded else None,
            executable=Path(str(recorded["executable"])) if "executable" in recorded else None,
            last_tick=self._last_tick(),
            jobs=len(jobs),
            failing=[job.job for job in jobs if job.problem or job.last_disposition is Disposition.FAILED],
        )

    def health(self) -> list[Check]:
        """The daemon's checks for `hz system doctor`; not installing it is a choice, not a problem."""
        status = self.status()
        if not status.installed:
            checks = [
                Check(name="daemon", status=Status.UP, detail="not installed; `hz system daemon install` runs jobs")
            ]
        elif not status.loaded:
            checks = [
                Check(
                    name="daemon",
                    status=Status.DEGRADED,
                    detail="installed but not loaded, so no ticks run",
                    fix="Run `hz system daemon install` again",
                )
            ]
        else:
            checks = [Check(name="daemon", status=Status.UP, detail=f"loaded, every {status.interval_minutes} minutes")]
            checks.append(self._freshness(status))
        if status.paused:
            checks.append(
                Check(name="paused", status=Status.DEGRADED, detail="ticks are paused", fix="hz system daemon resume")
            )
        if status.failing:
            checks.append(
                Check(
                    name="jobs",
                    status=Status.DEGRADED,
                    detail=f"failing: {', '.join(status.failing)}",
                    fix="See `hz system daemon jobs` and `hz system daemon logs`",
                )
            )
        return checks

    def jobs(self) -> list[JobInfo]:
        """Every job the kernel knows, with its schedule, next slot and last run."""
        ledger = Ledger(self.receipts_dir)
        now = self._clock()
        infos: list[JobInfo] = []
        for job in self._jobs():
            problem = schedule.problem(job.let.schedule)
            last = ledger.last(job.id)
            infos.append(
                JobInfo(
                    job=job.id,
                    summary=job.let.summary,
                    schedule=job.let.schedule,
                    scope=job.let.scope,
                    timeout_seconds=job.let.timeout.total_seconds(),
                    retries=job.let.retries,
                    catch_up=job.let.catch_up,
                    next_run=None if problem else schedule.next_slot(job.let.schedule, now),
                    last_disposition=last.disposition if last else None,
                    last_as_of=last.as_of if last else None,
                    problem=problem,
                )
            )
        return infos

    def logs(self, lines: int) -> list[LogLine]:
        """The last lines of the daemon's log, oldest first."""
        return tail(self.log_path, lines)

    # Pausing ---------------------------------------------------------------------------------------------------------

    @property
    def paused(self) -> bool:
        """Whether the local kill switch is on."""
        return (self.state_dir / "paused").exists()

    def pause(self) -> Toggled:
        """Turn the local kill switch on: ticks run no job until `resume`."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "paused").touch()
        return Toggled(paused=True)

    def resume(self) -> Toggled:
        """Turn the local kill switch off."""
        (self.state_dir / "paused").unlink(missing_ok=True)
        return Toggled(paused=False)

    # Ticking ---------------------------------------------------------------------------------------------------------

    def tick(self, *, dry_run: bool = False, job: str | None = None) -> TickReport:
        """Run the jobs that are due, or `job` now, and write a receipt for each; `dry_run` only says what is due.

        Raises:
            HeinzelError: If `job` names no registered job, or the ledger is damaged.
        """
        now = self._clock()
        jobs = self._jobs()
        if job is not None:
            jobs = [j for j in jobs if j.id == job]
            if not jobs:
                msg = f"No job {job!r}; `hz system daemon jobs` lists them."
                raise HeinzelError(msg)
        if self.paused:
            return TickReport(at=now, dry_run=dry_run, paused=True, locked=False, runs=[])
        if dry_run:
            return TickReport(at=now, dry_run=True, paused=False, locked=False, runs=self._plan(jobs, job, now))
        with lock(self.state_dir / "tick.lock") as acquired:
            if not acquired:
                return TickReport(at=now, dry_run=False, paused=False, locked=True, runs=[])
            log = TickLog(self.log_path, f"{now:%Y%m%dT%H%M%S}")
            try:
                runs = self._run(jobs, job, now, log)
                self._write_state("heartbeat.json", {"last_tick": now.isoformat()})
                log.write(now, logging.INFO, f"tick done: {len(runs)} run(s)")
            finally:
                log.close()
        return TickReport(at=now, dry_run=False, paused=False, locked=False, runs=runs)

    def _plan(self, jobs: list[Job], manual: str | None, now: datetime) -> list[JobRun]:
        ledger = Ledger(self.receipts_dir)
        planned: list[JobRun] = []
        for job in jobs:
            for slot, wake, skip in self._due(ledger, job, manual, now):
                planned.append(
                    JobRun(
                        job=job.id,
                        as_of=slot,
                        wake=wake,
                        disposition=Disposition.SKIPPED if skip else None,
                        error=skip,
                    )
                )
        return planned

    def _run(self, jobs: list[Job], manual: str | None, now: datetime, log: TickLog) -> list[JobRun]:
        ledger = Ledger(self.receipts_dir)
        runs: list[JobRun] = []
        for job in jobs:
            problem = schedule.problem(job.let.schedule)
            if problem and manual is None:
                log.write(now, logging.ERROR, problem, job=job.id)
                runs.append(
                    JobRun(job=job.id, as_of=now, wake=Wake.SCHEDULE, disposition=Disposition.FAILED, error=problem)
                )
                continue
            for slot, wake, reason in self._due(ledger, job, manual, now):
                key = job.let.idempotency(slot) if job.let.idempotency else f"{job.id}@{slot.isoformat()}"
                skip = reason
                if skip is None and wake is Wake.SCHEDULE and ledger.rendered(job.id, key):
                    skip = f"already rendered as {key}"
                error = skip if skip is not None else self._execute(job)
                disposition = (
                    Disposition.SKIPPED if skip is not None else Disposition.FAILED if error else Disposition.RENDERED
                )
                receipt = ledger.append(
                    job=job.id,
                    wake=wake,
                    disposition=disposition,
                    as_of=slot,
                    idempotency_key=key,
                    error=error,
                    written_at=self._clock(),
                )
                level = logging.ERROR if disposition is Disposition.FAILED else logging.INFO
                log.write(now, level, f"{job.id} {disposition.value}", job=job.id, receipt=receipt.id, error=error)
                runs.append(
                    JobRun(job=job.id, as_of=slot, wake=wake, disposition=disposition, error=error, receipt=receipt.id)
                )
        return runs

    def _due(
        self, ledger: Ledger, job: Job, manual: str | None, now: datetime
    ) -> list[tuple[datetime, Wake, str | None]]:
        """The slots to handle now, each with its wake cause and, for slots to skip, why.

        A job without schedule receipts starts at the previous tick (or one interval ago on the first tick), so a new
        job never replays its history, whatever its catch-up policy.
        """
        if manual is not None:
            return [(now, Wake.MANUAL, None)]
        if schedule.problem(job.let.schedule):
            return []
        interval = timedelta(minutes=self._interval())
        last = ledger.last(job.id, Wake.SCHEDULE)
        after = last.as_of if last is not None else self._last_tick() or now - interval
        if job.let.catch_up is CatchUp.ALL:
            return [(slot, Wake.SCHEDULE, None) for slot in schedule.slots(job.let.schedule, after, now)]
        slot = schedule.latest(job.let.schedule, after, now)
        if slot is None:
            return []
        if job.let.catch_up is CatchUp.SKIP and now - slot > interval:
            return [(slot, Wake.SCHEDULE, "missed slot; catch_up is skip")]
        return [(slot, Wake.SCHEDULE, None)]

    def _execute(self, job: Job) -> str | None:
        """Run a job through kernel injection with its timeout, retrying failures; the last error, or None."""
        error: str | None = None
        for _ in range(job.let.retries + 1):
            error = self._time_limit(lambda: self._kernel.call(job.let.func), job.let.timeout.total_seconds())
            if error is None:
                return None
        return error

    # Helpers ---------------------------------------------------------------------------------------------------------

    def _jobs(self) -> list[Job]:
        return [
            Job(id=f"{record.qualname}:{let.name}", let=let)
            for record, let in self._kernel.lets(JobLet)
            if self._kernel.available(let.func)
        ]

    def _last_tick(self) -> datetime | None:
        return self._timestamp("heartbeat.json", "last_tick")

    def _timestamp(self, name: str, key: str) -> datetime | None:
        value = self._read_state(name).get(key)
        try:
            return datetime.fromisoformat(str(value)) if value else None
        except ValueError:
            return None

    def _interval(self) -> int:
        return int(self._read_state("install.json").get("interval_minutes", DEFAULT_INTERVAL))

    def _freshness(self, status: DaemonStatus) -> Check:
        interval = timedelta(minutes=status.interval_minutes or DEFAULT_INTERVAL)
        since = status.last_tick or self._timestamp("install.json", "installed_at")
        if status.paused or since is None or self._clock() - since <= STALE_TICKS * interval:
            return Check(name="last tick", status=Status.UP, detail=str(status.last_tick or "none yet"))
        return Check(
            name="last tick",
            status=Status.DEGRADED,
            detail=f"no tick since {status.last_tick or 'installation'}",
            fix="See `hz system daemon logs`; on macOS check Login Items in System Settings",
        )

    def _minutes(self, interval: timedelta) -> int:
        minutes, remainder = divmod(int(interval.total_seconds()), 60)
        if remainder:
            msg = f"The interval must be whole minutes, not {interval}."
            raise HeinzelError(msg)
        try:
            check_interval(minutes)
        except ValueError as exc:
            raise HeinzelError(str(exc)) from exc
        return minutes

    def _shim(self, *, dry_run: bool) -> Path:
        """The stable `hz` shim in uv's tool bin directory, never the environment's own interpreter (ADR 0020).

        Raises:
            HeinzelError: If uv fails, or (unless `dry_run`) hz isn't installed as a uv tool.
        """
        result = self._runner(self._command("uv", "tool", "dir", "--bin"))
        if result.returncode != 0:
            msg = f"`uv tool dir --bin` failed: {result.stderr.strip()}"
            raise HeinzelError(msg)
        shim = Path(result.stdout.strip()) / "hz"
        if not shim.exists() and not dry_run:
            msg = (
                f"{shim} doesn't exist. The daemon runs the uv tool installation of hz: "
                "`uv tool install git+https://github.com/helmut-hoffer-von-ankershoffen/heinzel`."
            )
            raise HeinzelError(msg)
        return shim

    def _unit_paths(self) -> list[Path]:
        if self._platform == "darwin":
            return [plist_path(self.unit_dir)]
        if self._platform == "linux":
            return list(systemd_paths(self.unit_dir))
        msg = f"The daemon supports macOS (launchd) and Linux (systemd), not {self._platform}."
        raise HeinzelError(msg)

    def _unit_files(self, executable: Path, minutes: int) -> dict[Path, str]:
        paths = self._unit_paths()
        if self._platform == "darwin":
            return {paths[0]: launchd_plist(executable, minutes, self.log_path.parent)}
        return {paths[0]: systemd_service(executable), paths[1]: systemd_timer(minutes)}

    def _load_commands(self) -> list[list[str]]:
        if self._platform == "darwin":
            return [self._command("launchctl", "bootstrap", f"gui/{self._uid()}", str(plist_path(self.unit_dir)))]
        return [
            self._command("systemctl", "--user", "daemon-reload"),
            self._command("systemctl", "--user", "enable", "--now", f"{SYSTEMD_NAME}.timer"),
        ]

    def _loaded(self) -> bool:
        if self._platform == "darwin":
            command = self._command("launchctl", "print", f"gui/{self._uid()}/{LABEL}")
        else:
            command = self._command("systemctl", "--user", "is-active", "--quiet", f"{SYSTEMD_NAME}.timer")
        return self._runner(command).returncode == 0

    def _command(self, executable: str, *args: str) -> list[str]:
        """A command with its executable resolved on PATH.

        Raises:
            HeinzelError: If the executable isn't installed.
        """
        found = self._which(executable)
        if found is None:
            msg = f"`{executable}` is not on PATH; the daemon needs it on this platform."
            raise HeinzelError(msg)
        return [found, *args]

    def _read_state(self, name: str) -> dict[str, str | int]:
        path = self.state_dir / name
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return cast("dict[str, str | int]", data) if isinstance(data, dict) else {}

    def _write_state(self, name: str, data: dict[str, str | int]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self.state_dir / name
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
