"""DaemonService with a fake clock, a fake runner and temporary directories: never the real launchd, systemd or home."""

import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from heinzel import Check, HeinzelError, Kernel, Module, Status
from heinzel.modules.system.daemon import DaemonService, DaemonSettings
from heinzel.modules.system.daemon._ledger import Ledger
from heinzel.modules.system.daemon._models import Disposition, Wake, unit_dir
from heinzel.modules.system.daemon._runtime import lock, within
from heinzel.modules.system.daemon._units import LABEL

pytestmark = pytest.mark.unit

BERLIN = ZoneInfo("Europe/Berlin")
T0 = datetime(2026, 10, 5, 10, 2, tzinfo=BERLIN)


class Clock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def set(self, hour: int, minute: int = 0) -> None:
        self.now = self.now.replace(hour=hour, minute=minute)


class Runner:
    """Records commands; `uv tool dir --bin` answers `bin_dir`, the rest exit with `codes[verb]` (0 by default)."""

    def __init__(self, bin_dir: Path, codes: dict[str, int] | None = None) -> None:
        self.bin_dir = bin_dir
        self.codes = codes or {}
        self.commands: list[list[str]] = []

    def __call__(self, command):
        self.commands.append(list(command))
        if command[1:] == ["tool", "dir", "--bin"]:
            return subprocess.CompletedProcess(command, self.codes.get("uv", 0), f"{self.bin_dir}\n", "uv broke")
        verb = next(part for part in command[1:] if not part.startswith("-"))
        return subprocess.CompletedProcess(command, self.codes.get(verb, 0), "", f"{verb} failed")

    def verbs(self) -> list[str]:
        return [" ".join(c[1:]) for c in self.commands if c[1:] != ["tool", "dir", "--bin"]]


class Refuses:
    def __call__(self, command):
        msg = f"ran {command}"
        raise AssertionError(msg)


@pytest.fixture
def calls() -> list[str]:
    return []


@pytest.fixture
def jobs(calls) -> Module:
    module = Module("example", "Jobs for tests.")

    @module.job(schedule="0 * * * *")
    def hourly(kernel: Kernel) -> str:
        """Run every hour."""
        calls.append(f"hourly with {type(kernel).__name__}")
        return "done"

    @module.job(schedule="0 * * * *", catch_up="all", name="every-slot")
    def every_slot() -> None:
        """Run once per hourly slot."""
        calls.append("every-slot")

    @module.job(schedule="0 * * * *", catch_up="skip", name="on-time")
    def on_time() -> None:
        """Run only on time."""
        calls.append("on-time")

    return module


@pytest.fixture
def bin_dir(tmp_path) -> Path:
    path = tmp_path / "bin"
    path.mkdir()
    (path / "hz").touch()
    return path


@pytest.fixture
def make(make_kernel, settings, tmp_path, bin_dir):
    def build(*modules: Module, platform="darwin", runner=None, clock=None, time_limit=within) -> DaemonService:
        return DaemonService(
            make_kernel(**{m.name: m for m in modules}),
            settings,
            DaemonSettings(unit_dir=tmp_path / "units"),
            runner=runner or Runner(bin_dir),
            platform=platform,
            uid=lambda: 501,
            clock=clock or Clock(),
            which=lambda name: f"/bin/{name}",
            time_limit=time_limit,
        )

    return build


# Installing


def test_install_on_macos_writes_and_bootstraps_the_launch_agent(make, bin_dir, tmp_path):
    runner = Runner(bin_dir)
    result = make(runner=runner).install(timedelta(minutes=10))
    plist = tmp_path / "units" / f"{LABEL}.plist"
    assert result.files == {plist: plist.read_text(encoding="utf-8")}
    assert f"<string>{bin_dir / 'hz'}</string>" in plist.read_text(encoding="utf-8")
    assert runner.verbs() == [f"bootout gui/501/{LABEL}", f"bootstrap gui/501 {plist}"]
    assert result.commands == [["/bin/launchctl", "bootstrap", "gui/501", str(plist)]]
    assert (result.interval_minutes, result.dry_run) == (10, False)


def test_install_on_linux_writes_and_enables_the_timer(make, bin_dir, tmp_path):
    runner = Runner(bin_dir)
    make(platform="linux", runner=runner).install(timedelta(minutes=5))
    assert (tmp_path / "units" / "hz-daemon.service").is_file()
    assert "OnCalendar=*:0/5" in (tmp_path / "units" / "hz-daemon.timer").read_text(encoding="utf-8")
    assert runner.verbs() == ["--user daemon-reload", "--user enable --now hz-daemon.timer"]


def test_install_dry_run_writes_and_loads_nothing(make, bin_dir, tmp_path):
    runner = Runner(bin_dir)
    result = make(runner=runner).install(timedelta(minutes=5), dry_run=True)
    assert result.dry_run
    assert result.commands == []
    assert runner.verbs() == []
    assert not (tmp_path / "units").exists()
    assert "StartCalendarInterval" in next(iter(result.files.values()))


def test_install_refuses_intervals_that_do_not_divide_an_hour(make):
    with pytest.raises(HeinzelError, match="divide an hour"):
        make().install(timedelta(minutes=7))
    with pytest.raises(HeinzelError, match="whole minutes"):
        make().install(timedelta(seconds=90))


def test_install_needs_the_uv_tool_shim(make, bin_dir):
    (bin_dir / "hz").unlink()
    with pytest.raises(HeinzelError, match="uv tool install"):
        make().install(timedelta(minutes=5))
    assert make().install(timedelta(minutes=5), dry_run=True).executable == bin_dir / "hz"


def test_install_reports_failures_of_uv_and_launchctl(make, bin_dir):
    with pytest.raises(HeinzelError, match="uv tool dir --bin"):
        make(runner=Runner(bin_dir, {"uv": 2})).install(timedelta(minutes=5))
    with pytest.raises(HeinzelError, match="bootstrap failed"):
        make(runner=Runner(bin_dir, {"bootstrap": 5})).install(timedelta(minutes=5))


def test_install_needs_a_supported_platform_and_its_tools(make, make_kernel, settings, tmp_path, bin_dir):
    with pytest.raises(HeinzelError, match="not win32"):
        make(platform="win32").install(timedelta(minutes=5))
    service = DaemonService(
        make_kernel(), settings, DaemonSettings(unit_dir=tmp_path), runner=Runner(bin_dir), which=lambda _: None
    )
    with pytest.raises(HeinzelError, match="`uv` is not on PATH"):
        service.install(timedelta(minutes=5))


def test_uninstall_without_an_installation_does_nothing(make):
    result = make(runner=Refuses()).uninstall()
    assert (result.removed, result.commands) == ([], [])


@pytest.mark.parametrize(
    ("platform", "verbs"),
    [
        ("darwin", [f"bootout gui/501/{LABEL}"]),
        ("linux", ["--user disable --now hz-daemon.timer", "--user daemon-reload"]),
    ],
)
def test_uninstall_unloads_and_removes_the_unit(make, bin_dir, tmp_path, platform, verbs):
    make(platform=platform).install(timedelta(minutes=5))
    runner = Runner(bin_dir, {"bootout": 3})  # not loaded: still removed
    service = make(platform=platform, runner=runner)
    result = service.uninstall()
    assert runner.verbs() == verbs
    assert result.removed
    assert not any((tmp_path / "units").iterdir())
    assert service.status().interval_minutes is None


# Inspecting


def test_status_of_an_uninstalled_daemon_runs_no_command(make):
    service = make(runner=Refuses())
    status = service.status()
    assert (status.installed, status.loaded, status.paused, status.last_tick, status.failing) == (
        False,
        False,
        False,
        None,
        [],
    )
    assert service.health() == [Check(name="daemon", status=Status.UP, detail=status_detail(service))]


def status_detail(service: DaemonService) -> str:
    return service.health()[0].detail


def test_health_of_a_loaded_daemon_that_ticks(make, jobs):
    clock = Clock()
    service = make(jobs, clock=clock)
    service.install(timedelta(minutes=5))
    service.tick()
    status = service.status()
    assert (status.installed, status.loaded, status.interval_minutes, status.jobs) == (True, True, 5, 3)
    assert status.last_tick == T0
    assert [(c.name, c.status) for c in service.health()] == [("daemon", Status.UP), ("last tick", Status.UP)]


def test_a_freshly_installed_daemon_is_healthy_until_it_misses_ticks(make):
    clock = Clock()
    service = make(clock=clock)
    service.install(timedelta(minutes=5))
    assert {c.name: c.status for c in service.health()}["last tick"] is Status.UP
    clock.set(10, 30)
    checks = {c.name: c for c in service.health()}
    assert checks["last tick"].status is Status.DEGRADED
    assert checks["last tick"].detail == "no tick since installation"


def test_health_flags_a_unit_that_is_not_loaded(make, bin_dir):
    make().install(timedelta(minutes=5))
    [check] = make(runner=Runner(bin_dir, {"print": 113})).health()
    assert (check.name, check.status) == ("daemon", Status.DEGRADED)


def test_health_flags_a_daemon_that_stopped_ticking(make, jobs):
    clock = Clock()
    service = make(jobs, clock=clock)
    service.install(timedelta(minutes=5))
    service.tick()
    clock.set(11)
    checks = {c.name: c for c in service.health()}
    assert checks["last tick"].status is Status.DEGRADED
    assert "no tick since" in checks["last tick"].detail


def test_health_flags_pause_and_failing_jobs(make, calls):
    broken = Module("broken", "Broken jobs.")

    @broken.job(schedule="not cron")
    def invalid() -> None:
        """Never runs."""

    service = make(broken)
    service.pause()
    checks = {c.name: c.status for c in service.health()}
    assert checks == {"daemon": Status.UP, "paused": Status.DEGRADED, "jobs": Status.DEGRADED}
    assert service.status().failing == ["broken:invalid"]


def test_status_on_an_unsupported_platform(make):
    assert make(platform="win32", runner=Refuses()).status().installed is False


def test_jobs_lists_schedules_next_slots_and_last_runs(make, jobs):
    clock = Clock()
    service = make(jobs, clock=clock)
    service.tick()
    by_id = {job.job: job for job in service.jobs()}
    assert set(by_id) == {"example:hourly", "example:every-slot", "example:on-time"}
    hourly = by_id["example:hourly"]
    assert (hourly.summary, hourly.schedule, hourly.scope, hourly.timeout_seconds) == (
        "Run every hour.",
        "0 * * * *",
        "personal",
        900.0,
    )
    assert hourly.next_run == T0.replace(hour=11, minute=0)
    assert (hourly.last_disposition, hourly.last_as_of) == (Disposition.RENDERED, T0.replace(minute=0))


def test_logs_show_what_ticks_did(make, jobs):
    service = make(jobs)
    service.tick()
    messages = [line.message for line in service.logs(10)]
    assert messages[-1] == "tick done: 3 run(s)"
    assert "example:hourly rendered" in messages


# Ticking


def test_the_first_tick_runs_the_slot_that_just_fell_due(make, jobs, calls):
    report = make(jobs).tick()
    assert sorted(calls) == ["every-slot", "hourly with Kernel", "on-time"]
    assert {(run.job, run.as_of, run.wake, run.disposition) for run in report.runs} == {
        (job, T0.replace(minute=0), Wake.SCHEDULE, Disposition.RENDERED)
        for job in ("example:hourly", "example:every-slot", "example:on-time")
    }


def test_a_new_job_never_replays_its_history(make, jobs, calls):
    clock = Clock()
    clock.set(10, 30)
    assert make(jobs, clock=clock).tick().runs == []
    assert calls == []


def test_nothing_is_due_twice(make, jobs, calls):
    service = make(jobs)
    service.tick()
    calls.clear()
    assert service.tick().runs == []
    assert calls == []


def test_after_sleep_latest_runs_once_all_runs_every_slot_and_skip_runs_none(make, jobs, calls):
    clock = Clock()
    service = make(jobs, clock=clock)
    service.tick()
    calls.clear()
    clock.set(13, 30)
    report = service.tick()
    outcome = [(run.job, run.as_of.hour, run.disposition) for run in report.runs]
    assert outcome == [
        ("example:hourly", 13, Disposition.RENDERED),
        ("example:every-slot", 11, Disposition.RENDERED),
        ("example:every-slot", 12, Disposition.RENDERED),
        ("example:every-slot", 13, Disposition.RENDERED),
        ("example:on-time", 13, Disposition.SKIPPED),
    ]
    assert report.runs[-1].error == "missed slot; catch_up is skip"
    assert calls.count("every-slot") == 3
    assert "on-time" not in calls


def test_a_job_without_receipts_starts_at_the_previous_tick(make, jobs, calls):
    clock = Clock()
    service = make(clock=clock)
    service.tick()  # leaves a heartbeat at 10:02
    clock.set(12, 3)
    report = make(jobs, clock=clock).tick()
    every_slot = [run.as_of.hour for run in report.runs if run.job == "example:every-slot"]
    assert every_slot == [11, 12]


def test_receipts_chain_and_record_the_slot(make, jobs, settings):
    clock = Clock()
    service = make(jobs, clock=clock)
    service.tick()
    clock.set(12, 1)
    service.tick()
    chain = Ledger(settings.data_dir / "receipts").chain("example:every-slot")
    assert [r.as_of.hour for r in chain] == [10, 11, 12]
    assert [r.prev for r in chain[1:]] == [r.id for r in chain[:-1]]
    assert chain[0].idempotency_key == f"example:every-slot@{T0.replace(minute=0).isoformat()}"


def test_failures_are_retried_within_the_tick(make, calls):
    flaky = Module("flaky", "Flaky jobs.")
    attempts = iter([RuntimeError("first"), None])

    @flaky.job(schedule="0 * * * *", retries=1)
    def sometimes() -> None:
        """Fail once."""
        calls.append("try")
        if error := next(attempts):
            raise error

    @flaky.job(schedule="0 * * * *", retries=2)
    def never() -> None:
        """Always fail."""
        calls.append("never")
        msg = "feed unreachable"
        raise HeinzelError(msg)

    report = make(flaky).tick()
    outcome = {run.job: (run.disposition, run.error) for run in report.runs}
    assert outcome == {
        "flaky:sometimes": (Disposition.RENDERED, None),
        "flaky:never": (Disposition.FAILED, "feed unreachable"),
    }
    assert (calls.count("try"), calls.count("never")) == (2, 3)


def test_a_job_that_times_out_fails(make, jobs, calls):
    seen = []

    def time_limit(func, seconds):
        seen.append(seconds)
        return f"timed out after {seconds:g}s"

    report = make(jobs, time_limit=time_limit).tick()
    assert {run.error for run in report.runs} == {"timed out after 900s"}
    assert seen == [900.0, 900.0, 900.0]
    assert calls == []


def test_a_slot_whose_idempotency_key_was_rendered_is_skipped(make, calls):
    daily = Module("daily", "Once a day.")

    @daily.job(schedule="0 * * * *", catch_up="all", idempotency=lambda slot: slot.date().isoformat())
    def digest() -> None:
        """At most once a day."""
        calls.append("digest")

    clock = Clock()
    service = make(daily, clock=clock)
    service.tick()
    clock.set(11, 1)
    [run] = service.tick().runs
    assert (run.disposition, run.error) == (Disposition.SKIPPED, "already rendered as 2026-10-05")
    assert calls == ["digest"]


def test_tick_job_runs_one_job_now_without_moving_its_schedule(make, jobs, calls):
    clock = Clock()
    clock.set(10, 30)
    service = make(jobs, clock=clock)
    [run] = service.tick(job="example:hourly").runs
    assert (run.job, run.as_of, run.wake, run.disposition) == (
        "example:hourly",
        clock.now,
        Wake.MANUAL,
        Disposition.RENDERED,
    )
    clock.set(11, 1)
    assert [r.as_of.hour for r in service.tick().runs if r.job == "example:hourly"] == [11]


def test_tick_job_needs_a_registered_job(make, jobs):
    with pytest.raises(HeinzelError, match="No job 'example:nope'"):
        make(jobs).tick(job="example:nope")


def test_a_dry_run_shows_what_is_due_and_writes_nothing(make, jobs, calls, settings):
    service = make(jobs)
    report = service.tick(dry_run=True)
    assert report.dry_run
    assert {(run.job, run.disposition) for run in report.runs} == {
        ("example:hourly", None),
        ("example:every-slot", None),
        ("example:on-time", None),
    }
    assert calls == []
    assert not (settings.data_dir / "receipts").exists()
    assert service.status().last_tick is None


def test_a_paused_daemon_runs_nothing(make, jobs, calls):
    service = make(jobs)
    assert service.pause().paused
    report = service.tick()
    assert (report.paused, report.runs, calls) == (True, [], [])
    assert not service.resume().paused
    assert service.tick().runs


def test_a_tick_while_another_holds_the_lock_runs_nothing(make, jobs, calls, settings):
    service = make(jobs)
    with lock(settings.state_dir / "daemon" / "tick.lock"):
        report = service.tick()
    assert (report.locked, report.runs, calls) == (True, [], [])


def test_a_job_with_an_invalid_schedule_fails_without_a_receipt(make, calls):
    broken = Module("broken", "Broken jobs.")

    @broken.job(schedule="every monday")
    def invalid() -> None:
        """Runs only by hand."""
        calls.append("invalid")

    service = make(broken)
    [run] = service.tick().runs
    assert (run.disposition, run.receipt) == (Disposition.FAILED, None)
    assert "invalid schedule" in (run.error or "")
    assert service.tick(dry_run=True).runs == []
    assert service.tick(job="broken:invalid").runs[0].disposition is Disposition.RENDERED
    assert calls == ["invalid"]


def test_corrupt_state_files_are_ignored(make, settings):
    state = settings.state_dir / "daemon"
    state.mkdir(parents=True)
    (state / "heartbeat.json").write_text(json.dumps({"last_tick": "yesterday"}), encoding="utf-8")
    (state / "install.json").write_text("[1, 2]", encoding="utf-8")
    status = make().status()
    assert (status.last_tick, status.interval_minutes) == (None, None)


def test_status_asks_systemd_whether_the_timer_is_active(make, bin_dir):
    make(platform="linux").install(timedelta(minutes=5))
    runner = Runner(bin_dir, {"is-active": 3})
    status = make(platform="linux", runner=runner).status()
    assert (status.installed, status.loaded) == (True, False)
    assert runner.verbs() == ["--user is-active --quiet hz-daemon.timer"]


@pytest.mark.parametrize(
    ("platform", "tail"), [("darwin", ("Library", "LaunchAgents")), ("linux", ("systemd", "user"))]
)
def test_units_go_where_the_service_manager_looks(platform, tail):
    assert unit_dir(platform).parts[-2:] == tail
