"""DaemonService with a fake clock, a fake runner and temporary directories: never the real launchd, systemd or home."""

import json
import os
import stat
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from heinzel import Check, CompiledJobs, HeinzelError, JobLet, Kernel, Module, Notifications, Status, __version__
from heinzel.modules.system.daemon import DaemonService, DaemonSettings
from heinzel.modules.system.daemon._ledger import Ledger
from heinzel.modules.system.daemon._models import Disposition, Wake, unit_dir
from heinzel.modules.system.daemon._runtime import Outcome, lock, within
from heinzel.modules.system.daemon._service import MAX_SERVICE_MANAGER_OUTPUT, _launchd_pid, _reported_pid
from heinzel.modules.system.daemon._units import LABEL

pytestmark = pytest.mark.unit

BERLIN = ZoneInfo("Europe/Berlin")
T0 = datetime(2026, 10, 5, 10, 2, tzinfo=BERLIN)


class Clock:
    """Like production's `datetime.now().astimezone()`: a fixed UTC offset, not the zone's rules."""

    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        local = self.now.astimezone(BERLIN)
        offset = local.utcoffset()
        assert offset is not None
        return local.replace(tzinfo=timezone(offset))

    def set(self, hour: int, minute: int = 0, day: int | None = None, month: int | None = None) -> None:
        local = self.now.astimezone(BERLIN)
        self.now = local.replace(
            month=month or local.month, day=day or local.day, hour=hour, minute=minute, tzinfo=BERLIN
        )


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
    def build(
        *modules: Module, platform="darwin", runner=None, clock=None, time_limit=within, notifier=True
    ) -> DaemonService:
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
            zone=BERLIN,
            notifier_installed=lambda: notifier,
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


def test_install_hints_at_the_notifier_for_login_items_while_it_is_missing(make):
    assert make(notifier=False).install(timedelta(minutes=10)).hint == (
        "Install the notifier to show Heinzelmännchen with its icon in Login Items: hz system notify install"
    )
    assert make(notifier=True).install(timedelta(minutes=10)).hint is None
    assert make(platform="linux", notifier=False).install(timedelta(minutes=10)).hint is None


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

    @broken.job(schedule="0 0 31 2 *")
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


def test_compiled_jobs_run_beside_declared_ones_under_their_modules_name(make, calls):
    module = Module("contracts", "Compiles jobs from data.")

    def compiled() -> str:
        calls.append("compiled")
        return "drafted"

    module.service(
        CompiledJobs,
        lambda: CompiledJobs(jobs=(JobLet(name="digest", func=compiled, summary="Digest.", schedule="0 * * * *"),)),
    )
    service = make(module)
    report = service.tick()
    assert [(run.job, run.disposition) for run in report.runs] == [("contracts:digest", Disposition.RENDERED)]
    assert calls == ["compiled"]
    assert [job.job for job in service.jobs()] == ["contracts:digest"]


def test_a_failing_compiler_doesnt_stop_the_declared_jobs(make, jobs, calls):
    module = Module("contracts", "Compiles jobs from data, badly.")

    def broken() -> CompiledJobs:
        msg = "a contract broke the compiler"
        raise RuntimeError(msg)

    module.service(CompiledJobs, broken)
    service = make(jobs, module)
    report = service.tick()
    assert sorted(calls) == ["every-slot", "hourly with Kernel", "on-time"]
    assert {run.disposition for run in report.runs} == {Disposition.RENDERED}
    check = next(c for c in service.health() if c.name == "compiled jobs")
    assert check.status is Status.DEGRADED
    assert "a contract broke the compiler" in check.detail


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
    clock.set(10, 6)
    report = make(jobs, clock=clock).tick()
    assert [run.job for run in report.runs] == []  # 10:00 fell before the previous tick


def test_a_new_job_after_a_long_pause_runs_only_the_latest_interval(make, jobs, calls):
    clock = Clock()
    make(clock=clock).tick()  # a heartbeat at 10:02, then the machine is off for hours
    clock.set(15, 3)
    report = make(jobs, clock=clock).tick()
    assert {(run.job, run.as_of.hour) for run in report.runs} == {
        ("example:hourly", 15),
        ("example:every-slot", 15),
        ("example:on-time", 15),
    }


def test_a_last_run_in_the_future_means_the_clock_went_back(make, jobs, calls):
    clock = Clock()
    clock.set(15, 1)
    service = make(jobs, clock=clock)
    service.tick()
    clock.set(11, 1)
    assert {run.as_of.hour for run in service.tick(dry_run=True).runs} == {11}
    report = service.tick()
    assert {(run.job, run.as_of.hour) for run in report.runs} == {
        ("example:hourly", 11),
        ("example:every-slot", 11),
        ("example:on-time", 11),
    }
    assert any("the clock went back" in line.message and line.level == "WARNING" for line in service.logs(20))


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


def test_a_job_that_times_out_is_final_and_skipped_while_it_still_runs(make, calls):
    slow = Module("slow", "Slow jobs.")

    @slow.job(schedule="0 * * * *", retries=3)
    def stuck() -> None:
        """Overruns."""

    still_running = threading.Event()
    thread = threading.Thread(target=still_running.wait, daemon=True)
    thread.start()
    seen = []

    def time_limit(func, seconds):
        seen.append(seconds)
        return Outcome(error="timed out after 900s and was abandoned; it may still have side effects", abandoned=thread)

    clock = Clock()
    service = make(slow, clock=clock, time_limit=time_limit)
    [first] = service.tick().runs
    assert (first.disposition, seen) == (Disposition.FAILED, [900.0])  # no retry after a timeout
    assert "abandoned" in (first.error or "")
    clock.set(11, 1)
    [second] = service.tick().runs
    assert (second.disposition, second.error) == (
        Disposition.SKIPPED,
        "its previous run timed out and is still running",
    )
    assert seen == [900.0]
    still_running.set()
    thread.join()
    clock.set(12, 1)
    assert service.tick().runs[0].disposition is Disposition.FAILED
    assert seen == [900.0, 900.0]


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
    assert service.tick(dry_run=True).paused
    assert not service.resume().paused
    assert service.tick().runs


def test_a_tick_while_another_holds_the_lock_runs_nothing(make, jobs, calls, settings):
    service = make(jobs)
    with lock(settings.state_dir / "daemon" / "tick.lock"):
        report = service.tick()
    assert (report.locked, report.runs, calls) == (True, [], [])


def test_a_job_with_an_invalid_schedule_fails_without_a_receipt(make, calls):
    broken = Module("broken", "Broken jobs.")

    @broken.job(schedule="61 * * * *")
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


# Containment


def test_a_damaged_receipt_chain_fails_only_its_job(make, jobs, calls, settings):
    clock = Clock()
    service = make(jobs, clock=clock)
    service.tick()
    receipts = settings.data_dir / "receipts"
    hourly = next(p for p in receipts.glob("*.json") if '"example:hourly"' in p.read_text(encoding="utf-8"))
    hourly.write_text(hourly.read_text(encoding="utf-8").replace("rendered", "failed"), encoding="utf-8")
    (receipts / "garbage.json").write_text("{", encoding="utf-8")
    calls.clear()
    clock.set(11, 1)
    outcome = {run.job: (run.disposition, run.error or "") for run in service.tick().runs}
    assert outcome["example:hourly"][0] is Disposition.FAILED
    assert "changed after it was written" in outcome["example:hourly"][1]
    assert outcome["example:every-slot"][0] is Disposition.RENDERED
    assert sorted(calls) == ["every-slot", "on-time"]
    assert "example:hourly" in service.status().failing
    assert any("garbage.json is unreadable" in line.message for line in service.logs(20))
    plan = {run.job: run.disposition for run in service.tick(dry_run=True).runs}
    assert plan["example:hourly"] is Disposition.FAILED


def test_an_idempotency_function_that_raises_fails_only_its_job(make, calls):
    mixed = Module("mixed", "Mixed jobs.")

    def broken_key(slot: datetime) -> str:
        msg = "no key"
        raise RuntimeError(msg)

    @mixed.job(schedule="0 * * * *", idempotency=broken_key)
    def keyless() -> None:
        """Has a broken key."""
        calls.append("keyless")

    @mixed.job(schedule="0 * * * *")
    def fine() -> None:
        """Runs."""
        calls.append("fine")

    outcome = {run.job: (run.disposition, run.error) for run in make(mixed).tick().runs}
    assert outcome == {
        "mixed:keyless": (Disposition.FAILED, "RuntimeError: no key"),
        "mixed:fine": (Disposition.RENDERED, None),
    }
    assert calls == ["fine"]


def test_a_manual_run_never_cancels_the_scheduled_one(make, calls):
    daily = Module("daily", "Once a day.")

    @daily.job(schedule="0 * * * *", idempotency=lambda slot: slot.date().isoformat())
    def digest() -> None:
        """At most once a day."""
        calls.append("digest")

    clock = Clock()
    clock.set(9, 30)
    service = make(daily, clock=clock)
    [manual] = service.tick(job="daily:digest").runs
    assert manual.wake is Wake.MANUAL
    clock.set(10, 2)
    [scheduled] = service.tick().runs
    assert (scheduled.wake, scheduled.disposition) == (Wake.SCHEDULE, Disposition.RENDERED)
    assert calls == ["digest", "digest"]


def test_the_receipt_keeps_what_the_job_returned(make, jobs, settings):
    make(jobs).tick()
    chain = Ledger(settings.data_dir / "receipts").chain("example:hourly")
    assert chain[0].result == "done"


# Daylight saving time: cron means wall-clock time in the user's zone, whatever offset the clock reports


def daily_module(calls: list[str], schedule: str) -> Module:
    module = Module("daily", "Daily jobs.")

    @module.job(schedule=schedule, catch_up="all")
    def report() -> None:
        """Report daily."""
        calls.append("report")

    return module


def test_catch_up_across_the_change_to_winter_time_keeps_the_wall_clock_time(make, calls, settings):
    clock = Clock(datetime(2026, 10, 24, 9, 1, tzinfo=BERLIN))
    service = make(daily_module(calls, "0 9 * * *"), clock=clock)
    service.tick()  # 24 October 09:00 summer time, stored as +02:00
    clock.set(10, 0, day=27)
    runs = service.tick().runs
    assert [run.as_of.isoformat() for run in runs] == [
        "2026-10-25T09:00:00+01:00",
        "2026-10-26T09:00:00+01:00",
        "2026-10-27T09:00:00+01:00",
    ]
    stored = Ledger(settings.data_dir / "receipts").chain("daily:report")
    assert [r.as_of.utcoffset() for r in stored] == [timedelta(hours=2)] + [timedelta(hours=1)] * 3


def test_a_time_that_happens_twice_when_the_clocks_go_back_runs_once(make, calls):
    clock = Clock(datetime(2026, 10, 24, 2, 31, tzinfo=BERLIN))
    service = make(daily_module(calls, "30 2 * * *"), clock=clock)
    service.tick()
    clock.now = datetime(2026, 10, 25, 4, 0, tzinfo=BERLIN)
    [run] = service.tick().runs
    assert run.as_of.isoformat() == "2026-10-25T02:30:00+02:00"  # the first 02:30
    assert calls == ["report", "report"]


def test_a_time_that_does_not_exist_when_the_clocks_go_forward_runs_shifted_by_the_gap(make, calls):
    clock = Clock(datetime(2027, 3, 27, 2, 31, tzinfo=BERLIN))
    service = make(daily_module(calls, "30 2 * * *"), clock=clock)
    service.tick()
    clock.now = datetime(2027, 3, 28, 12, 0, tzinfo=BERLIN)
    [run] = service.tick().runs
    assert run.as_of.isoformat() == "2027-03-28T03:30:00+02:00"


# Files


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_receipts_state_and_logs_are_private_and_units_readable(make, jobs, settings, tmp_path):
    previous = os.umask(0o022)
    try:
        service = make(jobs)
        service.install(timedelta(minutes=5))
        service.tick()
        service.pause()
    finally:
        os.umask(previous)
    receipts = settings.data_dir / "receipts"
    assert mode(receipts) == 0o700
    assert {mode(p) for p in receipts.glob("*.json")} == {0o600}
    assert mode(settings.state_dir / "daemon") == 0o700
    assert {mode(p) for p in (settings.state_dir / "daemon").iterdir()} == {0o600}
    assert mode(settings.log_dir) == 0o700
    assert mode(settings.log_dir / "daemon.jsonl") == 0o600
    assert mode(tmp_path / "units" / f"{LABEL}.plist") == 0o600
    assert mode(tmp_path / "units") != 0o700  # the LaunchAgents directory is left as it is


def test_the_service_reads_user_settings_not_a_project_hz_toml(make_kernel, tmp_path, monkeypatch):
    project = tmp_path / "repo"
    project.mkdir()
    elsewhere = tmp_path / "elsewhere"
    (project / "hz.toml").write_text(f'[hz]\ndata_dir = "{elsewhere}"\n', encoding="utf-8")
    monkeypatch.chdir(project)
    monkeypatch.delenv("HZ_DATA_DIR")  # only the project file would set it
    service = DaemonService.for_user(make_kernel())
    assert not service.receipts_dir.is_relative_to(elsewhere)


# Notices (ADR 0041)


class FakeNotifications(Notifications):
    def __init__(self, error: Exception | None = None) -> None:
        self.posts = 0
        self.error = error

    def post_native(self) -> int:
        self.posts += 1
        if self.error is not None:
            raise self.error
        return 2


def notifying(fake: FakeNotifications) -> Module:
    module = Module("notices", "Provides notifications for tests.")
    module.service(Notifications, lambda: fake)
    return module


def test_every_tick_ends_by_popping_up_due_notices(make, jobs):
    fake = FakeNotifications()
    service = make(jobs, notifying(fake))
    service.tick()
    assert fake.posts == 1
    assert "posted 2 notice(s)" in service.log_path.read_text(encoding="utf-8")
    service.tick(dry_run=True)
    assert fake.posts == 1  # a dry run pops up nothing


def test_a_failing_notifier_never_fails_the_tick(make, jobs):
    service = make(jobs, notifying(FakeNotifications(OSError("disk full"))))
    report = service.tick()
    assert all(run.disposition is not Disposition.FAILED for run in report.runs)
    assert "posting notices failed: OSError: disk full" in service.log_path.read_text(encoding="utf-8")


def test_manual_tick_cannot_attest_the_daemon_and_restart_stays_unknown_until_a_service_tick(make, monkeypatch):
    service = make()
    assert (service.status().version, service.status().version_mismatch) == (None, False)
    service.install(timedelta(minutes=5))
    assert service.status().installed_version == __version__
    assert (service.status().version, service.status().version_mismatch) == (None, False)
    service.tick()
    assert (service.status().version, service.status().version_mismatch) == (None, False)
    monkeypatch.setattr(service, "_service_manager_pid", lambda: os.getpid() + 1)
    service.tick()
    assert (service.status().version, service.status().version_mismatch) == (None, False)
    monkeypatch.setattr(service, "_service_manager_pid", os.getpid)
    service.tick()
    assert (
        service.status().version,
        service.status().installed_version,
        service.status().version_mismatch,
    ) == (__version__, __version__, False)


def test_a_daemon_started_by_an_older_hz_is_flagged_until_current_service_tick(make, tmp_path, monkeypatch):
    service = make()
    service.install(timedelta(minutes=5))
    monkeypatch.setattr(service, "_service_manager_pid", os.getpid)
    service.tick()
    heartbeat = service.state_dir / "heartbeat.json"
    heartbeat.write_text(heartbeat.read_text().replace(__version__, "0.0.1"), encoding="utf-8")
    status = service.status()
    assert (status.version, status.version_mismatch) == ("0.0.1", True)
    restarted = service.restart()
    assert (restarted.restarted, restarted.version, restarted.interval_minutes) == (True, __version__, 5)
    assert (service.status().version, service.status().version_mismatch) == ("0.0.1", True)
    service.tick()
    assert (service.status().version, service.status().version_mismatch) == (__version__, False)


def test_launchd_service_pid_is_read_from_its_running_job(make, bin_dir, monkeypatch):
    service = make(runner=Runner(bin_dir))
    output = f'{{\n\t"Label" = "{LABEL}";\n\t"PID" = {os.getpid()};\n}}\n'
    commands = []

    def runner(command):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(
        service,
        "_runner",
        runner,
    )
    assert service._service_manager_pid() == os.getpid()
    assert commands[0][-2:] == ["list", LABEL]


def test_systemd_service_pid_is_read_from_main_pid(make, bin_dir, monkeypatch):
    service = make(platform="linux", runner=Runner(bin_dir))
    monkeypatch.setattr(
        service,
        "_runner",
        lambda command: subprocess.CompletedProcess(command, 0, "417\n", ""),
    )
    assert service._service_manager_pid() == 417


@pytest.mark.parametrize(
    "output",
    [
        f'{{\n"Label" = "{LABEL}";\n"PID" = "-";\n}}\n',
        f'{{\n"Label" = "{LABEL}";\n"PID" = 12345678901;\n}}\n',
        f'{{\n"Label" = "{LABEL}-lookalike";\n"PID" = 12;\n}}\n',
        f'{{\n"Label" = "{LABEL}";\n"PID" = 12;\n"PID" = 13;\n}}\n',
        "x" * (MAX_SERVICE_MANAGER_OUTPUT + 1),
    ],
    ids=["unrunning", "oversized-pid", "wrong-label", "duplicate-pid", "oversized-output"],
)
def test_launchd_pid_parser_fails_closed_on_malformed_or_unbounded_output(output):
    assert _launchd_pid(output, LABEL) is None


@pytest.mark.parametrize("output", ["0", "-1", "12\n99", "12345678901", "x" * (MAX_SERVICE_MANAGER_OUTPUT + 1)])
def test_systemd_pid_parser_fails_closed_on_malformed_or_unbounded_output(output):
    assert _reported_pid(output) is None


def test_failed_daemon_replacement_keeps_the_old_version_marker_fail_closed(make, tmp_path, bin_dir):
    service = make(runner=Runner(bin_dir))
    service.install(timedelta(minutes=5))
    state = service.state_dir / "install.json"
    state.write_text(state.read_text().replace(__version__, "0.0.1"), encoding="utf-8")

    with pytest.raises(HeinzelError, match="bootstrap failed"):
        make(runner=Runner(bin_dir, {"bootstrap": 5})).install(timedelta(minutes=5))

    assert json.loads(state.read_text(encoding="utf-8"))["version"] == "0.0.1"
    status = service.status()
    assert (status.version, status.installed_version, status.version_mismatch) == (None, "0.0.1", True)


def test_restarting_a_daemon_that_is_not_installed_does_nothing(make):
    assert make(runner=Refuses()).restart().restarted is False


def test_worker_thread_inherits_only_the_callers_context():
    from contextvars import ContextVar

    owner = ContextVar("test_owner", default="none")
    token = owner.set("first")
    try:
        assert within(owner.get, 1).value == "first"
        owner.set("second")
        assert within(owner.get, 1).value == "second"
    finally:
        owner.reset(token)
    assert within(owner.get, 1).value == "none"
