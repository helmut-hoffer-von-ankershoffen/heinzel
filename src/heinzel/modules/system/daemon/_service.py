"""Service of the daemon module: install the OS unit, and tick: run the jobs that are due and write receipts.

The OS supervises and the daemon ticks (ADR 0020): launchd or systemd starts `hz system daemon tick` every few
minutes, a short-lived process that survives `hz system update`, crashes and sleep by construction.

The service reads only user-scope configuration (site and user files and the environment, not a project's
`hz.toml`), so the unit, the receipts and the logs are the same wherever `hz` runs, and a repository can't move them.
"""

import json
import logging
import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pydantic import JsonValue
from pydantic_core import to_jsonable_python

from heinzel import CatchUp, Check, Config, HeinzelError, JobLet, Kernel, Notifications, Settings, Status
from heinzel.kernel import KERNEL_SECTION
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
from heinzel.modules.system.daemon._runtime import (
    Outcome,
    Runner,
    TickLog,
    TimeLimit,
    describe,
    lock,
    private_dir,
    run,
    tail,
    within,
    write_private,
)
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

if TYPE_CHECKING:
    import threading

logger = logging.getLogger(__name__)
DEFAULT_INTERVAL = 5
"""Minutes between ticks unless `install --interval` says otherwise."""
STALE_TICKS = 3
"""A daemon that missed this many intervals in a row is reported as degraded."""
PLATFORMS = ("darwin", "linux")
"""launchd and systemd read unit files; they hold no secrets."""
MODULE_SECTION = "system.daemon"


@dataclass(frozen=True, slots=True)
class Job:
    """A registered job under its id, `<module>:<job>`."""

    id: str
    let: JobLet


@dataclass(frozen=True, slots=True)
class Due:
    """A slot to handle in this tick: run it, or record why it is skipped."""

    slot: datetime
    wake: Wake
    skip: str | None = None


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
        clock: Callable[[], datetime] | None = None,
        which: Callable[[str], str | None] = shutil.which,
        time_limit: TimeLimit = within,
        zone: tzinfo | None = None,
    ) -> None:
        """Create the service; `for_user` builds it for the kernel, tests pass the rest.

        Args:
            kernel: Finds the jobs and injects their parameters.
            settings: Kernel settings: where receipts, state and logs live.
            daemon: Where the units go.
            runner: Runs launchctl, systemctl and uv.
            platform: `sys.platform`, to pick launchd or systemd.
            uid: The user id launchd's GUI domain is named after.
            clock: The current time, timezone-aware; now in `zone` by default.
            which: Finds executables.
            time_limit: Runs a job with a timeout.
            zone: The time zone cron expressions mean; the user's by default.
        """
        self._kernel = kernel
        self._runner = runner
        self._platform = platform
        self._uid = uid
        self._zone = zone or schedule.local_zone()
        self._clock = clock or (lambda: datetime.now(self._zone))
        self._which = which
        self._time_limit = time_limit
        self._abandoned: dict[str, threading.Thread] = {}
        self.compile_error: str | None = None
        """Why compiling jobs failed the last time they were listed; None when it worked."""
        self.unit_dir = daemon.unit_dir
        self.receipts_dir = settings.data_dir / "receipts"
        self.state_dir = settings.state_dir / "daemon"
        self.log_path = settings.log_dir / "daemon.jsonl"

    @classmethod
    def for_user(cls, kernel: Kernel) -> "DaemonService":
        """The service with user-scope settings: a project's `hz.toml` can't move the unit, receipts or logs."""
        config = Config.load(Path.home())  # as if run from the home directory, like the uninstall skill does
        return cls(kernel, config.settings(Settings, KERNEL_SECTION), config.settings(DaemonSettings, MODULE_SECTION))

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
        private_dir(self.log_path.parent)
        for path, content in files.items():
            write_private(path, content, shared_dir=True)  # 0600: launchd and systemd read user units as the user
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
        self._jobs()
        if self.compile_error is not None:
            checks.append(
                Check(
                    name="compiled jobs",
                    status=Status.DEGRADED,
                    detail=f"compiling jobs failed, so only declared jobs run: {self.compile_error}",
                    fix="See `hz system daemon logs`; `hz workflow check` lists problems in contracts",
                )
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
        """Every job the kernel knows, with its schedule, next slot and last run; a damaged ledger is a problem."""
        ledger = Ledger(self.receipts_dir)
        now = self._clock()
        infos: list[JobInfo] = []
        for job in self._jobs():
            problem = schedule.problem(job.let.schedule)
            try:
                last = ledger.last(job.id)
            except HeinzelError as exc:
                last, problem = None, str(exc)
            infos.append(
                JobInfo(
                    job=job.id,
                    summary=job.let.summary,
                    schedule=job.let.schedule,
                    scope=job.let.scope,
                    timeout_seconds=job.let.timeout.total_seconds(),
                    retries=job.let.retries,
                    catch_up=job.let.catch_up,
                    next_run=None
                    if schedule.problem(job.let.schedule)
                    else schedule.next_slot(job.let.schedule, now, self._zone),
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
        write_private(self.state_dir / "paused", "")
        return Toggled(paused=True)

    def resume(self) -> Toggled:
        """Turn the local kill switch off."""
        (self.state_dir / "paused").unlink(missing_ok=True)
        return Toggled(paused=False)

    # Ticking ---------------------------------------------------------------------------------------------------------

    def tick(self, *, dry_run: bool = False, job: str | None = None) -> TickReport:
        """Run the jobs that are due, or `job` now, and write a receipt for each; `dry_run` only says what is due.

        One job's failure, a damaged receipt chain included, fails that job only; the others still run.

        Raises:
            HeinzelError: If `job` names no registered job.
        """
        now = self._clock()
        jobs = self._jobs()
        if job is not None:
            jobs = [j for j in jobs if j.id == job]
            if not jobs:
                msg = f"No job {job!r}; `hz system daemon jobs` lists them."
                raise HeinzelError(msg)
        if dry_run:
            if self.paused:
                return TickReport(at=now, dry_run=True, paused=True, locked=False, runs=[])
            return TickReport(at=now, dry_run=True, paused=False, locked=False, runs=self._plan(jobs, job, now))
        with lock(self.state_dir / "tick.lock") as acquired:
            if not acquired:
                return TickReport(at=now, dry_run=False, paused=False, locked=True, runs=[])
            if self.paused:  # checked under the lock, so a `pause` that waited for a tick is honoured by the next
                return TickReport(at=now, dry_run=False, paused=True, locked=False, runs=[])
            log = TickLog(self.log_path, f"{now:%Y%m%dT%H%M%S}", self._clock)
            try:
                runs = self._run(jobs, job, now, log)
                self._write_state("heartbeat.json", {"last_tick": now.isoformat()})
                self._post_notices(log)
                log.write(logging.INFO, f"tick done: {len(runs)} run(s)")
            finally:
                log.close()
        return TickReport(at=now, dry_run=False, paused=False, locked=False, runs=runs)

    def _post_notices(self, log: TickLog) -> None:
        """Pop up due notices natively when no web app shows them (ADR 0043); a failure only costs the pop-up."""
        if not self._kernel.provides(Notifications):
            return
        try:
            posted = self._kernel.resolve(Notifications).post_native()
        except Exception as exc:  # noqa: BLE001 - isolation boundary: notices never fail a tick
            log.write(logging.WARNING, f"posting notices failed: {describe(exc)}")
            return
        if posted:
            log.write(logging.INFO, f"posted {posted} notice(s)")

    def _plan(self, jobs: list[Job], manual: str | None, now: datetime) -> list[JobRun]:
        ledger = Ledger(self.receipts_dir)
        planned: list[JobRun] = []
        for job in jobs:
            try:
                due = self._due(ledger, job, manual, now, None)
            except HeinzelError as exc:
                planned.append(_failed(job, now, str(exc)))
                continue
            planned.extend(
                JobRun(
                    job=job.id,
                    as_of=d.slot,
                    wake=d.wake,
                    disposition=Disposition.SKIPPED if d.skip else None,
                    error=d.skip,
                )
                for d in due
            )
        return planned

    def _run(self, jobs: list[Job], manual: str | None, now: datetime, log: TickLog) -> list[JobRun]:
        ledger = Ledger(self.receipts_dir)
        ledger.broken()  # reads the receipts, so unreadable files are known before any job runs
        for name in ledger.unreadable:
            log.write(logging.WARNING, f"receipt file {name} is unreadable and was ignored")
        runs: list[JobRun] = []
        for job in jobs:
            problem = schedule.problem(job.let.schedule)
            if problem and manual is None:
                log.write(logging.ERROR, problem, job=job.id)
                runs.append(_failed(job, now, problem))
                continue
            try:
                runs.extend(self._handle(ledger, job, due, log) for due in self._due(ledger, job, manual, now, log))
            except Exception as exc:  # noqa: BLE001 - one job's damage (its ledger, its idempotency function) is its own
                error = describe(exc)
                log.write(logging.ERROR, f"{job.id} failed: {error}", job=job.id, error=error)
                runs.append(_failed(job, now, error))
        return runs

    def _handle(self, ledger: Ledger, job: Job, due: Due, log: TickLog) -> JobRun:
        """Run or skip one slot of a job and write its receipt."""
        if due.wake is Wake.MANUAL:  # its own key space, so a manual run never cancels a scheduled one
            key = f"manual:{job.id}@{due.slot.isoformat()}"
        else:
            key = job.let.idempotency(due.slot) if job.let.idempotency else f"{job.id}@{due.slot.isoformat()}"
        skip = due.skip
        if skip is None and due.wake is Wake.SCHEDULE and ledger.rendered(job.id, key):
            skip = f"already rendered as {key}"
        previous = self._abandoned.get(job.id)
        if skip is None and previous is not None and previous.is_alive():
            skip = "its previous run timed out and is still running"
        outcome = Outcome(error=skip) if skip is not None else self._execute(job)
        if skip is not None:
            disposition = Disposition.SKIPPED
        else:
            disposition = Disposition.FAILED if outcome.error else Disposition.RENDERED
        receipt = ledger.append(
            job=job.id,
            wake=due.wake,
            disposition=disposition,
            as_of=due.slot,
            idempotency_key=key,
            error=outcome.error,
            written_at=self._clock(),
            result=_json(outcome.value) if disposition is Disposition.RENDERED else None,
        )
        level = logging.ERROR if disposition is Disposition.FAILED else logging.INFO
        log.write(level, f"{job.id} {disposition.value}", job=job.id, receipt=receipt.id, error=outcome.error)
        return JobRun(
            job=job.id,
            as_of=due.slot,
            wake=due.wake,
            disposition=disposition,
            error=outcome.error,
            receipt=receipt.id,
        )

    def _due(self, ledger: Ledger, job: Job, manual: str | None, now: datetime, log: TickLog | None) -> list[Due]:
        """The slots to handle now.

        Missed slots count from the job's last scheduled receipt. A job without one starts at the later of the
        previous tick and one interval ago, so a new job, or one whose receipts were moved aside, never replays its
        history, even after a long time without ticks. A last receipt more than an interval in the future means the
        clock went back; the job then starts again from one interval ago.

        Raises:
            HeinzelError: If the job's receipts are damaged.
        """
        if manual is not None:
            return [Due(now, Wake.MANUAL)]
        if schedule.problem(job.let.schedule):
            return []
        interval = timedelta(minutes=self._interval())
        last = ledger.last(job.id, Wake.SCHEDULE)
        start = max(filter(None, (self._last_tick(), now - interval)))
        if last is not None and last.as_of > now + interval:
            if log is not None:
                log.write(
                    logging.WARNING,
                    f"{job.id}: last run {last.as_of.isoformat()} is in the future; the clock went back",
                    job=job.id,
                )
            after = now - interval
        else:
            after = last.as_of if last is not None else start
        rule = job.let.schedule
        if job.let.catch_up is CatchUp.ALL:
            return [Due(slot, Wake.SCHEDULE) for slot in schedule.slots(rule, after, now, self._zone)]
        slot = schedule.latest(rule, after, now, self._zone)
        if slot is None:
            return []
        if job.let.catch_up is CatchUp.SKIP and now - slot > interval:
            return [Due(slot, Wake.SCHEDULE, "missed slot; catch_up is skip")]
        return [Due(slot, Wake.SCHEDULE)]

    def _execute(self, job: Job) -> Outcome:
        """Run a job through kernel injection with its timeout, retrying failures but never a timeout.

        A job that timed out may still be running, so running it again could do its work twice; its thread is
        remembered, and the job is skipped while it is alive.
        """
        outcome = Outcome()
        for _ in range(job.let.retries + 1):
            outcome = self._time_limit(lambda: self._kernel.call(job.let.func), job.let.timeout.total_seconds())
            if outcome.abandoned is not None:
                self._abandoned[job.id] = outcome.abandoned
                return outcome
            if outcome.error is None:
                return outcome
        return outcome

    # Helpers ---------------------------------------------------------------------------------------------------------

    def _jobs(self) -> list[Job]:
        declared = [(record.qualname, let) for record, let in self._kernel.lets(JobLet)]
        # Jobs compiled from data, such as workflow contracts (ADR 0021), compiled afresh on every call.
        self.compile_error = None
        try:
            compiled = self._kernel.compiled_jobs()
        except Exception as exc:
            self.compile_error = describe(exc)
            logger.exception("compiling jobs failed; running the declared jobs only")
            compiled = []
        return [
            Job(id=f"{module}:{let.name}", let=let)
            for module, let in (*declared, *compiled)
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
        write_private(self.state_dir / name, json.dumps(data, indent=2) + "\n")


def _failed(job: Job, now: datetime, error: str) -> JobRun:
    return JobRun(job=job.id, as_of=now, wake=Wake.SCHEDULE, disposition=Disposition.FAILED, error=error)


def _json(value: object) -> JsonValue:
    """A job's return value as JSON for its receipt; what JSON can't express is kept as its text."""
    return cast("JsonValue", to_jsonable_python(value, fallback=str))
