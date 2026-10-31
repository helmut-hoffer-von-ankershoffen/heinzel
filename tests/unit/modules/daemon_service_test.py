"""DaemonService with a fake clock, a fake runner and temporary directories: never the real launchd, systemd or home."""

import json
import os
import select
import shutil
import stat
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from heinzel import (
    Check,
    CompiledJobs,
    CurrentUser,
    HeinzelError,
    JobLet,
    Kernel,
    Module,
    Notifications,
    Status,
    __version__,
)
from heinzel.modules.system.daemon import DaemonService, DaemonSettings
from heinzel.modules.system.daemon._ledger import Ledger
from heinzel.modules.system.daemon._models import Disposition, Wake, unit_dir
from heinzel.modules.system.daemon._runtime import Outcome, lock, within
from heinzel.modules.system.daemon._service import MAX_SERVICE_MANAGER_OUTPUT, _launchd_pid, _reported_pid
from heinzel.modules.system.daemon._units import LABEL, launchd_plist

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
            birth_fence=lambda: None,
            process_birth=lambda: None,
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


def test_health_explains_a_registered_daemon_whose_program_is_gone(make, bin_dir):
    service = make()
    service.install(timedelta(minutes=5))
    (bin_dir / "hz").unlink()
    (service.state_dir / "install.json").unlink()
    [check, *_] = service.health()
    assert (check.name, check.status) == ("daemon", Status.DEGRADED)
    assert check.fix == "Run `uvx --from heinzel hz system daemon uninstall`"


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


def test_a_job_that_times_out_keeps_lifecycle_locked_while_it_still_runs(make, calls):
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
    try:
        clock.set(11, 1)
        assert service.tick().locked
        import sys

        script = "import fcntl,sys; f=open(sys.argv[1], 'a'); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)"
        child = subprocess.run(
            [sys.executable, "-c", script, str(service.state_dir / "lifecycle.lock")],
            capture_output=True,
            check=False,
            timeout=5,
        )
        assert child.returncode != 0
        assert b"BlockingIOError" in child.stderr
        with (
            pytest.raises(HeinzelError, match="lifecycle is changing"),
            service.mutation_guard(lambda: False, lambda: "a" * 64),
        ):
            pytest.fail("mutation started while abandoned work was live")
        assert seen == [900.0]
    finally:
        still_running.set()
        thread.join(timeout=5)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with lock(service.state_dir / "lifecycle.lock") as acquired:
            if acquired:
                break
        time.sleep(0.01)
    else:
        pytest.fail("lifecycle lock remained held after abandoned work ended")
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


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_update_handover_keeps_uninstalled_unloaded_and_paused_services_off(make, bin_dir, platform):
    runner = Runner(bin_dir)
    service = make(platform=platform, runner=runner)
    absent = service.capture_handover()
    assert not absent.active
    assert not service.request_handover(absent).requested
    assert runner.commands == []
    service.install(timedelta(minutes=5))
    runner.commands.clear()
    runner.codes["print" if platform == "darwin" else "is-active"] = 3
    stopped = service.capture_handover()
    assert not stopped.active
    assert not service.request_handover(stopped).requested
    assert all("kickstart" not in cmd and "try-restart" not in cmd for cmd in runner.commands)
    runner.codes.clear()
    service.pause()
    runner.commands.clear()
    paused = service.capture_handover()
    assert not paused.active
    assert not service.request_handover(paused).requested
    assert runner.commands == []
    assert service.paused


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_update_handover_only_requests_unchanged_active_service_and_is_not_a_tick(make, bin_dir, platform):
    runner = Runner(bin_dir)
    service = make(platform=platform, runner=runner)
    service.install(timedelta(minutes=5))
    runner.commands.clear()
    before = service.capture_handover()
    assert before.active
    result = service.request_handover(before)
    assert result.requested
    assert "later service tick" in result.reason
    assert service.status().version is None
    commands = [" ".join(item) for item in runner.commands]
    expected = "kickstart" if platform == "darwin" else "try-restart"
    assert sum(expected in command for command in commands) == 1
    assert not any("bootstrap" in command or "enable" in command or "-k" in command for command in commands)
    assert not service.paused


@pytest.mark.parametrize("changed", ["unloaded", "paused", "unit", "marker"])
def test_update_handover_refuses_a_changed_service(make, bin_dir, changed):
    runner = Runner(bin_dir)
    service = make(runner=runner)
    service.install(timedelta(minutes=5))
    before = service.capture_handover()
    if changed == "unloaded":
        runner.codes["print"] = 3
    elif changed == "paused":
        service.pause()
    elif changed == "unit":
        service._unit_paths()[0].write_text("changed unit")
    else:
        (service.state_dir / "install.json").write_text("changed marker")
    runner.commands.clear()
    assert not service.request_handover(before).requested
    assert all("kickstart" not in command for command in runner.commands)


def test_update_handover_refuses_a_symlinked_unit(make, bin_dir, tmp_path):
    service = make(runner=Runner(bin_dir))
    service.install(timedelta(minutes=5))
    unit = service._unit_paths()[0]
    unit.unlink()
    foreign = tmp_path / "foreign"
    foreign.write_text("not a unit")
    unit.symlink_to(foreign)
    with pytest.raises(HeinzelError, match="could not be verified"):
        service.capture_handover()
    assert foreign.read_text() == "not a unit"


def test_handover_observes_only_fresh_manager_tick_and_actual_contributions(make, jobs, bin_dir, monkeypatch):
    service = make(jobs, runner=Runner(bin_dir))
    service.install(timedelta(minutes=5))
    service._boot_identity = lambda: "fixture-boot"
    service._continuity_fingerprint = lambda: "a" * 64
    fence = {"ticks": 100, "hz": 100, "ns_dev": 1, "ns_ino": 2}
    service._birth_fence = lambda: fence
    result = service.request_handover(service.capture_handover())
    assert result.requested
    assert result.request_id is not None
    assert service.handover_observation(result.request_id) is None
    monkeypatch.setattr(service, "_service_manager_pid", os.getpid)
    service._process_birth = lambda: {**fence, "ticks": 99}
    service.tick()
    assert service.handover_observation(result.request_id) is None  # old process cannot accept a new nonce
    service._process_birth = lambda: {**fence, "ticks": 101}
    service.tick()
    observed = service.handover_observation(result.request_id)
    assert observed is not None
    assert observed.version == __version__
    assert observed.fingerprint == "a" * 64
    assert any(row[0] == "example" for row in observed.contributions)
    assert service.handover_observation("b" * 64) is None
    service._continuity_fingerprint = lambda: "b" * 64
    assert service.handover_observation(result.request_id) is None
    assert service.status().version_mismatch
    service._continuity_fingerprint = lambda: "a" * 64
    assert not service.status().version_mismatch
    service._boot_identity = lambda: "another-boot"
    assert service.handover_observation(result.request_id) is None


@pytest.mark.parametrize("failure", ["manual", "wrong-boot", "missing-boot", "changed-fingerprint", "changed-request"])
def test_handover_remains_pending_when_fresh_tick_evidence_is_uncertain(make, bin_dir, monkeypatch, failure):
    service = make(runner=Runner(bin_dir))
    service.install(timedelta(minutes=5))
    service._boot_identity = lambda: "boot-one"
    service._continuity_fingerprint = lambda: "a" * 64
    fence = {"ticks": 100, "hz": 100, "ns_dev": 1, "ns_ino": 2}
    service._birth_fence = lambda: fence
    result = service.request_handover(service.capture_handover())
    assert result.request_id is not None
    service._process_birth = lambda: {**fence, "ticks": 101}
    monkeypatch.setattr(service, "_service_manager_pid", os.getpid if failure != "manual" else lambda: os.getpid() + 1)
    if failure == "wrong-boot":
        service._boot_identity = lambda: "boot-two"
    elif failure == "missing-boot":
        service._boot_identity = lambda: None
    elif failure == "changed-fingerprint":
        samples = iter(["a" * 64, "b" * 64])
        service._continuity_fingerprint = lambda: next(samples)
    elif failure == "changed-request":
        samples = iter(["a" * 64, "a" * 64])

        def replace_request():
            value = next(samples)
            if value and (service.state_dir / "handover-request.json").exists():
                request = service._read_state("handover-request.json")
                request["request_id"] = "c" * 64
                service._write_state("handover-request.json", request)
            return value

        service._continuity_fingerprint = replace_request
    service.tick()
    assert service.handover_observation(result.request_id) is None


def test_attestation_boundary_exposes_scoped_handover_and_rejects_forged_snapshot(make):
    from dataclasses import fields

    from heinzel.kernel import DaemonAttestation
    from heinzel.modules.system.daemon import _attestation, _installation_lifecycle

    service = make()
    assert [field.name for field in fields(DaemonAttestation)] == ["status"]
    assert _attestation(service).status().installed is False
    boundary = _installation_lifecycle(service)
    snapshot = boundary.capture()
    assert not boundary.request(snapshot).requested
    with pytest.raises(HeinzelError, match="observation is invalid"):
        boundary.request(object())
    assert boundary.observation("a" * 64) is None


@pytest.mark.parametrize(("platform", "verb"), [("darwin", "kickstart"), ("linux", "try-restart")])
def test_manager_failure_never_claims_a_successful_handover(make, bin_dir, platform, verb):
    runner = Runner(bin_dir)
    service = make(platform=platform, runner=runner)
    service.install(timedelta(minutes=5))
    before = service.capture_handover()
    runner.codes[verb] = 1
    with pytest.raises(HeinzelError, match="handover request failed"):
        service.request_handover(before)
    assert not service.paused


def test_boot_identity_reads_bounded_linux_file_without_running_a_command(tmp_path):
    from heinzel.modules.system.daemon._service import _platform_boot_identity

    boot = tmp_path / "boot-id"
    value = "12345678-1234-4234-8234-123456789abc"
    boot.write_text(value + "\n")
    assert _platform_boot_identity("linux", Refuses(), boot) == value
    for invalid in ("not-a-uuid", "{" + value + "}", "a" * 129, ""):
        boot.write_text(invalid)
        assert _platform_boot_identity("linux", Refuses(), boot) is None
    boot.unlink()
    assert _platform_boot_identity("linux", Refuses(), boot) is None


def test_boot_identity_uses_fixed_readonly_macos_query_and_rejects_failure():
    from heinzel.modules.system.daemon._service import _platform_boot_identity

    value = "12345678-1234-4234-8234-123456789abc"
    commands = []

    def runner(command):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, value.upper() + "\n", "")

    assert _platform_boot_identity("darwin", runner) == value
    assert commands == [["/usr/sbin/sysctl", "-n", "kern.bootsessionuuid"]]
    assert (
        _platform_boot_identity("darwin", lambda command: subprocess.CompletedProcess(command, 1, value, "no")) is None
    )
    assert (
        _platform_boot_identity("darwin", lambda command: subprocess.CompletedProcess(command, 0, value + "!", ""))
        is None
    )
    assert _platform_boot_identity("other", Refuses()) is None


@pytest.mark.parametrize("which", ["unit", "marker"])
@pytest.mark.parametrize("mode", [0o020, 0o002])
def test_update_handover_refuses_writable_unit_or_marker(make, bin_dir, which, mode):
    runner = Runner(bin_dir)
    service = make(runner=runner)
    service.install(timedelta(minutes=5))
    target = service._unit_paths()[0] if which == "unit" else service.state_dir / "install.json"
    target.chmod(target.stat().st_mode | mode)
    runner.commands.clear()
    with pytest.raises(HeinzelError, match="could not be verified"):
        service.capture_handover()
    assert all("kickstart" not in cmd and "try-restart" not in cmd for cmd in runner.commands)


@pytest.mark.parametrize("which", ["unit", "marker"])
@pytest.mark.parametrize("mode", [0o020, 0o002])
def test_update_handover_refuses_writable_parent(make, bin_dir, which, mode):
    service = make(runner=Runner(bin_dir))
    service.install(timedelta(minutes=5))
    target = service._unit_paths()[0] if which == "unit" else service.state_dir / "install.json"
    parent = target.parent
    old_mode = parent.stat().st_mode
    try:
        parent.chmod(old_mode | mode)
        with pytest.raises(HeinzelError, match="could not be verified"):
            service.capture_handover()
    finally:
        parent.chmod(old_mode)


def test_handover_assessment_compares_explicit_target_not_old_callers_version(make, jobs, bin_dir, monkeypatch):
    from heinzel.modules.system.daemon import _service

    runner = Runner(bin_dir)
    clock = Clock()
    service = make(jobs, runner=runner, clock=clock)
    service.install(timedelta(minutes=5))
    marker = service.state_dir / "install.json"
    marker.write_text(json.dumps({**json.loads(marker.read_text()), "version": "0.0.0"}))
    marker_before = marker.read_bytes()
    fence = {"ticks": 100, "hz": 100, "ns_dev": 1, "ns_ino": 2}
    service._boot_identity = lambda: "boot-one"
    service._birth_fence = lambda: fence
    service._process_birth = lambda: {**fence, "ticks": 101}
    service._continuity_fingerprint = lambda: "a" * 64
    request = service.request_handover(service.capture_handover())
    assert request.request_id is not None
    expected = tuple((item.qualname, item.origin.distribution, item.origin.version) for item in service._kernel.modules)
    assert service.assess_handover(request.request_id, __version__, "a" * 64, expected).status == "pending"
    monkeypatch.setattr(service, "_service_manager_pid", os.getpid)
    service.tick()
    monkeypatch.setattr(_service, "__version__", "0.0.0")  # caller still has the old core imported
    matched = service.assess_handover(request.request_id, __version__, "a" * 64, expected)
    assert matched.status == "matched"
    assert matched.observed is not None
    assert matched.observed.version == __version__
    before_fields = json.loads(marker_before)
    after_fields = json.loads(marker.read_bytes())
    assert {key: after_fields[key] for key in before_fields if key != "version"} == {
        key: value for key, value in before_fields.items() if key != "version"
    }
    assert after_fields["version"] == __version__
    assert after_fields["handover_id"] == request.request_id
    assert service.assess_handover(request.request_id, "0.0.0", "a" * 64, expected).status == "mismatched"
    assert service.assess_handover(request.request_id, __version__, "b" * 64, expected).status == "mismatched"
    extra = (*expected, ("other-plugin", "other-distribution", "1.0"))
    assert service.assess_handover(request.request_id, __version__, "a" * 64, extra).status == "mismatched"
    heartbeat = service.state_dir / "heartbeat.json"
    saved_heartbeat = heartbeat.read_bytes()
    heartbeat.unlink()
    assert service.assess_handover(request.request_id, __version__, "a" * 64, expected).status == "pending"
    heartbeat.write_bytes(saved_heartbeat)
    clock.set(10, 30)
    assert service.assess_handover(request.request_id, __version__, "a" * 64, expected).status == "pending"
    clock.set(10, 2)
    service.pause()
    assert service.assess_handover(request.request_id, __version__, "a" * 64, expected).status == "pending"
    service.resume()
    runner.codes["print"] = 1
    assert service.assess_handover(request.request_id, __version__, "a" * 64, expected).status == "pending"
    runner.codes.clear()
    service._unit_paths()[0].write_text("different unit")
    assert service.assess_handover(request.request_id, __version__, "a" * 64, expected).status == "pending"


@pytest.mark.parametrize("changed", ["request", "unit"])
def test_handover_race_does_not_advance_install_marker(make, jobs, bin_dir, monkeypatch, changed):
    service = make(jobs, runner=Runner(bin_dir))
    service.install(timedelta(minutes=5))
    marker = service.state_dir / "install.json"
    before = marker.read_bytes()
    fence = {"ticks": 100, "hz": 100, "ns_dev": 1, "ns_ino": 2}
    service._boot_identity = lambda: "fixture-boot"
    service._birth_fence = lambda: fence
    service._process_birth = lambda: {**fence, "ticks": 101}
    service._continuity_fingerprint = lambda: "a" * 64
    request = service.request_handover(service.capture_handover())
    assert request.request_id is not None
    monkeypatch.setattr(service, "_service_manager_pid", os.getpid)
    observation = service._begin_handover_observation()
    assert observation is not None

    def change_after_validation():
        if changed == "request":
            service._write_state("handover-request.json", {"request_id": "b" * 64, "boot": "fixture-boot"})
        else:
            service._unit_paths()[0].write_text("replacement unit")
        return "a" * 64

    service._continuity_fingerprint = change_after_validation
    service._finish_handover_observation(observation, service._clock())
    service._continuity_fingerprint = lambda: "a" * 64
    assert marker.read_bytes() == before
    assert service.handover_observation(request.request_id) is None


@pytest.mark.parametrize("failed_file", ["install.json", "handover-observation.json"])
def test_interrupted_marker_transaction_does_not_allow_install_to_clear_evidence(
    make, jobs, bin_dir, monkeypatch, failed_file
):
    from heinzel.modules.system.daemon import _service

    service = make(jobs, runner=Runner(bin_dir))
    service.install(timedelta(minutes=5))
    fence = {"ticks": 100, "hz": 100, "ns_dev": 1, "ns_ino": 2}
    service._boot_identity = lambda: "fixture-boot"
    service._birth_fence = lambda: fence
    service._process_birth = lambda: {**fence, "ticks": 101}
    service._continuity_fingerprint = lambda: "a" * 64
    request = service.request_handover(service.capture_handover())
    assert request.request_id is not None
    monkeypatch.setattr(service, "_service_manager_pid", os.getpid)
    real_write = _service.write_private

    def interrupted(path, text, **kwargs):
        if path.name == failed_file:
            msg = "injected interrupted transaction"
            raise OSError(msg)
        return real_write(path, text, **kwargs)

    monkeypatch.setattr(_service, "write_private", interrupted)
    service.tick()
    assert (service.state_dir / "handover-pending.json").exists()
    assert service.status().version_mismatch
    assert service.handover_observation(request.request_id) is None
    assert not service.request_handover(service.capture_handover()).requested
    monkeypatch.setattr(_service, "write_private", real_write)
    with pytest.raises(HeinzelError, match="requires recovery"):
        service.install(timedelta(minutes=5))
    assert (service.state_dir / "handover-pending.json").exists()
    assert service.status().version_mismatch


def test_lifecycle_writers_refuse_concurrent_commit(make, jobs, bin_dir):
    from heinzel.modules.system.daemon._runtime import lock

    service = make(jobs, runner=Runner(bin_dir))
    service.install(timedelta(minutes=5))
    plan = service.capture_handover()
    marker = (service.state_dir / "install.json").read_bytes()
    with lock(service.state_dir / "lifecycle.lock") as acquired:
        assert acquired
        assert not service.request_handover(plan).requested
        with pytest.raises(HeinzelError, match="lifecycle is changing"):
            service.install(timedelta(minutes=5))
        with pytest.raises(HeinzelError, match="lifecycle is changing"):
            service.uninstall()
        service._finish_handover_observation(None, service._clock())
    assert (service.state_dir / "install.json").read_bytes() == marker


def test_marker_fsync_failure_leaves_pending_even_after_heartbeat(make, jobs, bin_dir, monkeypatch):
    from heinzel.modules.system.daemon import _runtime

    service = make(jobs, runner=Runner(bin_dir))
    service.install(timedelta(minutes=5))
    fence = {"ticks": 100, "hz": 100, "ns_dev": 1, "ns_ino": 2}
    service._boot_identity = lambda: "fixture-boot"
    service._birth_fence = lambda: fence
    service._process_birth = lambda: {**fence, "ticks": 101}
    service._continuity_fingerprint = lambda: "a" * 64
    request = service.request_handover(service.capture_handover())
    monkeypatch.setattr(service, "_service_manager_pid", os.getpid)
    original = _runtime.fsync_dir
    failed = []

    def interrupted(directory):
        marker = service.state_dir / "install.json"
        if not failed and marker.exists() and "handover_id" in marker.read_text():
            failed.append(True)
            msg = "injected fsync failure"
            raise OSError(msg)
        return original(directory)

    monkeypatch.setattr(_runtime, "fsync_dir", interrupted)
    service.tick()
    assert failed
    assert (service.state_dir / "heartbeat.json").exists()
    assert (service.state_dir / "handover-pending.json").exists()
    assert service.status().version_mismatch
    assert request.request_id is not None
    assert service.handover_observation(request.request_id) is None


def test_inaccessible_pending_marker_denies_lifecycle_and_status(make, bin_dir, monkeypatch):
    service = make(runner=Runner(bin_dir))
    service.install(timedelta(minutes=5))
    plan = service.capture_handover()
    original = Path.lstat

    def inaccessible(path, *args, **kwargs):
        if path == service.state_dir / "handover-pending.json":
            raise PermissionError
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", inaccessible)
    assert service.status().version_mismatch
    assert not service.request_handover(plan).requested
    assert service.handover_observation("a" * 64) is None


def test_admission_guard_blocks_another_process_lifecycle_writer(make, bin_dir):
    import sys

    service = make(runner=Runner(bin_dir))
    lock_path = service.state_dir / "lifecycle.lock"
    script = "import fcntl,sys; f=open(sys.argv[1], 'a'); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)"
    with service.admission_guard():
        blocked = subprocess.run(
            [sys.executable, "-c", script, str(lock_path)], capture_output=True, check=False, timeout=5
        )
        assert blocked.returncode != 0
        assert b"BlockingIOError" in blocked.stderr
    available = subprocess.run(
        [sys.executable, "-c", script, str(lock_path)], capture_output=True, check=False, timeout=5
    )
    assert available.returncode == 0


def test_external_mutation_lock_prevents_tick_jobs(make, jobs, calls, bin_dir):
    import sys

    service = make(jobs, runner=Runner(bin_dir))
    lock_path = service.state_dir / "lifecycle.lock"
    lock_path.parent.mkdir(parents=True)
    script = (
        "import fcntl,sys; f=open(sys.argv[1], 'a'); "
        "fcntl.flock(f,fcntl.LOCK_EX); print('ready',flush=True); sys.stdin.read(1)"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(lock_path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready"
        report = service.tick()
        assert report.locked
        assert not report.runs
        assert not calls
    finally:
        if child.stdin is not None:
            child.stdin.close()
        child.wait(timeout=5)
        if child.stdout is not None:
            child.stdout.close()


def test_tick_blocks_mutation_and_pause_remains_available(make, jobs, bin_dir, monkeypatch, tmp_path):
    import sys

    service = make(jobs, runner=Runner(bin_dir))
    entered, release = threading.Event(), threading.Event()
    reports = []

    def running(*_args):
        entered.set()
        assert release.wait(timeout=5)
        return []

    monkeypatch.setattr(service, "_run", running)
    thread = threading.Thread(target=lambda: reports.append(service.tick()))
    thread.start()
    try:
        assert entered.wait(timeout=5)
        invoked = []
        with (
            pytest.raises(HeinzelError, match="lifecycle is changing"),
            service.mutation_guard(lambda: False, lambda: "a" * 64),
        ):
            invoked.append("uv")
        assert not invoked
        sentinel = tmp_path / "unsafe-mutation"
        script = (
            "import fcntl,sys; from pathlib import Path; f=open(sys.argv[1], 'a'); "
            "fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB); Path(sys.argv[2]).touch()"
        )
        child = subprocess.run(
            [sys.executable, "-c", script, str(service.state_dir / "lifecycle.lock"), str(sentinel)],
            capture_output=True,
            check=False,
            timeout=5,
        )
        assert child.returncode != 0
        assert b"BlockingIOError" in child.stderr
        assert not sentinel.exists()
        assert service.pause().paused
    finally:
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert reports
    assert service.paused


def test_pending_or_unreadable_mutation_state_refuses_tick(make, jobs, calls, bin_dir, monkeypatch):
    service = make(jobs, runner=Runner(bin_dir))
    pending = service.state_dir / "mutation-pending.json"
    pending.parent.mkdir(parents=True)
    pending.write_text("uncertain")
    assert service.tick().locked
    pending.unlink()
    original = Path.lstat

    def inaccessible(path, *args, **kwargs):
        if path == pending:
            raise PermissionError
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", inaccessible)
    assert service.tick().locked
    assert not calls


def test_later_heartbeat_covers_prior_handover():
    from heinzel.modules.system.daemon._service import _heartbeat_covers

    tick = "2026-10-30T10:02:00+00:00"
    assert _heartbeat_covers({"last_tick": "2026-10-30T10:03:00+00:00", "version": "1.0"}, tick, "1.0")
    assert not _heartbeat_covers({"last_tick": "2026-10-30T10:01:00+00:00", "version": "1.0"}, tick, "1.0")
    assert not _heartbeat_covers({"last_tick": "2026-10-30T10:03:00+00:00", "version": "0.9"}, tick, "1.0")


@pytest.mark.parametrize("changed", ["request", "unit", "fingerprint"])
def test_change_during_marker_commit_keeps_transaction_denied(make, jobs, bin_dir, monkeypatch, changed):
    from heinzel.modules.system.daemon import _service

    service = make(jobs, runner=Runner(bin_dir))
    service.install(timedelta(minutes=5))
    fence = {"ticks": 100, "hz": 100, "ns_dev": 1, "ns_ino": 2}
    service._boot_identity = lambda: "fixture-boot"
    service._birth_fence = lambda: fence
    service._process_birth = lambda: {**fence, "ticks": 101}
    service._continuity_fingerprint = lambda: "a" * 64
    request = service.request_handover(service.capture_handover())
    assert request.request_id is not None
    monkeypatch.setattr(service, "_service_manager_pid", os.getpid)
    real_write = _service.write_private

    def interleaved(path, text, **kwargs):
        result = real_write(path, text, **kwargs)
        if path.name == "install.json":
            if changed == "request":
                service._write_state("handover-request.json", {"request_id": "b" * 64})
            elif changed == "unit":
                service._unit_paths()[0].write_text("replacement unit")
            else:
                service._continuity_fingerprint = lambda: "b" * 64
        return result

    monkeypatch.setattr(_service, "write_private", interleaved)
    service.tick()
    assert (service.state_dir / "handover-pending.json").exists()
    assert service.status().version_mismatch
    assert service.handover_observation(request.request_id) is None


def test_mutation_scope_commits_verified_checkpoint_and_requests_active_handover(make, bin_dir):
    runner = Runner(bin_dir)
    service = make(runner=runner)
    service.install(timedelta(minutes=5))
    service._continuity_fingerprint = lambda: "a" * 64
    service._boot_identity = lambda: "fixture-boot"
    service._birth_fence = lambda: {"ticks": 100, "hz": 100, "ns_dev": 1, "ns_ino": 2}
    with service.mutation_guard(lambda: True, lambda: "b" * 64) as scope:
        plan = scope.capture()
        assert plan.active
        assert not service.request_handover(plan).requested  # the outer scope owns the nonreentrant lock
        scope.verified_complete("a" * 64)
        assert scope.request(plan).requested
        assert (service.state_dir / "mutation-pending.json").exists()
    assert not (service.state_dir / "mutation-pending.json").exists()
    assert service.status().version_mismatch  # request alone is not a fresh tick
    with pytest.raises(HeinzelError, match="scope is unavailable"):
        scope.capture()


@pytest.mark.parametrize("state", ["off", "paused"])
def test_mutation_scope_preserves_inactive_daemon(make, bin_dir, state):
    runner = Runner(bin_dir)
    service = make(runner=runner)
    if state == "paused":
        service.install(timedelta(minutes=5))
        service.pause()
    service._continuity_fingerprint = lambda: "a" * 64
    runner.commands.clear()
    with service.mutation_guard(lambda: True, lambda: "b" * 64) as scope:
        plan = scope.capture()
        assert not plan.active
        scope.verified_complete("a" * 64)
        assert not scope.request(plan).requested
    assert service.paused == (state == "paused")
    assert all("kickstart" not in command and "try-restart" not in command for command in runner.commands)


def test_plain_sdk_mutation_does_not_seed_a_missing_checkpoint(make, bin_dir):
    service = make(runner=Runner(bin_dir))
    service._continuity_fingerprint = lambda: "a" * 64
    with service.mutation_guard(lambda: False, lambda: "b" * 64) as scope:
        plan = scope.capture()
        scope.verified_complete("a" * 64)
        assert not scope.request(plan).requested
    assert not (service.state_dir / "mutation-pending.json").exists()


def test_mutation_scope_refuses_checkpoint_creation_for_plain_sdk(make, bin_dir):
    service = make(runner=Runner(bin_dir))
    service._continuity_fingerprint = lambda: "a" * 64
    checkpoint = [False]

    def attempt():
        with service.mutation_guard(lambda: checkpoint[0], lambda: "b" * 64) as scope:
            scope.capture()
            checkpoint[0] = True
            scope.verified_complete("a" * 64)

    with pytest.raises(HeinzelError, match="durable checkpoint"):
        attempt()
    assert (service.state_dir / "mutation-pending.json").exists()


def test_mutation_scope_rejects_changed_token_and_replay(make, bin_dir):
    service = make(runner=Runner(bin_dir))
    service._continuity_fingerprint = lambda: "a" * 64

    def attempt():
        with service.mutation_guard(lambda: True, lambda: "b" * 64) as scope:
            scope.capture()
            service._write_state("mutation-pending.json", {"token": "b" * 64})
            scope.verified_complete("a" * 64)

    with pytest.raises(HeinzelError, match="pending token changed"):
        attempt()
    assert (service.state_dir / "mutation-pending.json").exists()
    with pytest.raises(HeinzelError, match="requires recovery"), service.mutation_guard(lambda: True, lambda: "b" * 64):
        pytest.fail("pending mutation must not be replayed")


def test_killed_mutator_leaves_admission_denied_after_fake_uv(make, bin_dir, tmp_path):
    service = make(runner=Runner(bin_dir))
    service._continuity_fingerprint = lambda: "a" * 64
    fake_uv_output = tmp_path / "fake-uv-wrote"
    code = """import os, sys, time
from pathlib import Path
from heinzel import Kernel, Settings
from heinzel.modules.system.daemon import DaemonService, DaemonSettings
settings = Settings(state_dir=Path(sys.argv[1]), data_dir=Path(sys.argv[2]), log_dir=Path(sys.argv[3]))
service = DaemonService(Kernel(settings, []), settings, DaemonSettings(unit_dir=Path(sys.argv[4])),
                        platform="darwin", continuity_fingerprint=lambda: "a" * 64)
with service.mutation_guard(lambda: True, lambda: "b" * 64) as scope:
    scope.capture()
    Path(sys.argv[5]).write_text("fake mutation completed")
    os.write(1, b"R")
    time.sleep(60)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(service.state_dir.parent), str(tmp_path / "data"),
         str(tmp_path / "logs"), str(tmp_path / "units"), str(fake_uv_output)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    try:
        assert child.stdout is not None
        ready, _, _ = select.select([child.stdout], [], [], 3)
        assert ready
        assert os.read(child.stdout.fileno(), 1) == b"R"
        assert fake_uv_output.read_text() == "fake mutation completed"
        with lock(service.state_dir / "lifecycle.lock") as acquired:
            assert not acquired
        with pytest.raises(HeinzelError, match="changing or requires recovery"), service.admission_guard():
            pytest.fail("an active mutation must hold the cross-process lifecycle lock")
        child.kill()
        assert child.wait(timeout=2) < 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=2)
        if child.stdout is not None:
            child.stdout.close()
    assert fake_uv_output.read_text() == "fake mutation completed"
    with lock(service.state_dir / "lifecycle.lock") as acquired:
        assert acquired
    assert (service.state_dir / "mutation-pending.json").exists()
    with pytest.raises(HeinzelError, match="requires recovery"), service.admission_guard():
        pytest.fail("uncertain mutation must deny schedule admission")


def test_unreadable_handover_request_keeps_legacy_marker_denied(make, bin_dir):
    service = make(runner=Runner(bin_dir))
    service.install(timedelta(minutes=5))
    service._write_state("heartbeat.json", {"last_tick": service._clock().isoformat(), "version": __version__})
    (service.state_dir / "handover-request.json").write_text("{")
    assert service.status().version_mismatch


@pytest.mark.parametrize("failure", ["different_generation", "different_plan", "missing_request"])
def test_mutation_scope_keeps_pending_on_incomplete_or_mismatched_completion(make, bin_dir, failure):
    service = make(runner=Runner(bin_dir))
    service._continuity_fingerprint = lambda: "a" * 64

    def attempt():
        with service.mutation_guard(lambda: True, lambda: "b" * 64) as scope:
            plan = scope.capture()
            if failure == "different_generation":
                scope.verified_complete("b" * 64)
            else:
                scope.verified_complete("a" * 64)
                if failure == "different_plan":
                    scope.request(type(plan)(plan.active, plan.platform, plan.unit_signature))

    with pytest.raises(HeinzelError):
        attempt()
    assert (service.state_dir / "mutation-pending.json").exists()
    with pytest.raises(HeinzelError, match="requires recovery"), service.admission_guard():
        pytest.fail("an uncertain mutation must block admission")


def test_mutation_scope_refuses_handover_if_generation_changes_after_verification(make, bin_dir):
    runner = Runner(bin_dir)
    service = make(runner=runner)
    service.install(timedelta(minutes=5))
    current = ["a" * 64]
    service._continuity_fingerprint = lambda: current[0]
    runner.commands.clear()

    def attempt():
        with service.mutation_guard(lambda: True, lambda: "b" * 64) as scope:
            plan = scope.capture()
            scope.verified_complete("a" * 64)
            current[0] = "b" * 64
            scope.request(plan)

    with pytest.raises(HeinzelError, match="generation or its durable checkpoint"):
        attempt()
    assert (service.state_dir / "mutation-pending.json").exists()
    assert all("kickstart" not in command for command in runner.commands)


def test_user_daemon_factory_uses_the_kernel_installation_fingerprint(settings, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from heinzel.kernel import Installation
    from heinzel.modules.system.daemon import _service

    marker = "a" * 64
    fake_installation = SimpleNamespace(startup_fingerprint=lambda: marker)
    fake_kernel = SimpleNamespace(resolve=lambda kind: fake_installation if kind is Installation else None)
    fake_config = SimpleNamespace(
        settings=lambda kind, section: settings if kind is _service.Settings else DaemonSettings(unit_dir=tmp_path)
    )
    monkeypatch.setattr(_service.Config, "load", lambda path: fake_config)
    service = DaemonService.for_user(fake_kernel)
    assert service._continuity_fingerprint() == marker


@pytest.mark.parametrize("changed", [False, True])
def test_abort_unchanged_requires_exact_original_identity(make, bin_dir, changed):
    service = make(runner=Runner(bin_dir))
    service._continuity_fingerprint = lambda: "a" * 64
    identity = ["b" * 64]

    def attempt():
        with service.mutation_guard(lambda: True, lambda: identity[0]) as scope:
            scope.capture()
            if changed:
                identity[0] = "c" * 64
            scope.abort_unchanged()

    if changed:
        with pytest.raises(HeinzelError, match="identity changed"):
            attempt()
        assert (service.state_dir / "mutation-pending.json").exists()
    else:
        attempt()
        assert not (service.state_dir / "mutation-pending.json").exists()


def _fresh_quiescence_target(service, tmp_path):
    from heinzel import Progress
    from heinzel.kernel import Installation

    store = tmp_path / "selected-store"
    binary = tmp_path / "selected-bin"
    store.mkdir()
    binary.mkdir()
    service._quiescence_installation = Installation(Progress(), prefix=store / "heinzel", uv="uv")
    return binary


def test_quiescent_accepts_only_verified_gui_absence_under_admission(make, bin_dir, tmp_path):
    from heinzel.kernel import ProcessOutcome

    service = make(runner=Runner(bin_dir))
    selected_bin = _fresh_quiescence_target(service, tmp_path)
    calls = []

    def probe(command):
        calls.append(command)
        output = {"managername": "Aqua\n", "manageruid": "501\n", "list": "PID\tStatus\tLabel\n-\t0\tother.service\n"}
        return ProcessOutcome(0, output[command[1]], "")

    service._quiescence_probe = probe
    with service.quiescence_guard(selected_bin):
        assert not service.request_handover(service.capture_handover()).requested
        installation = service._quiescence_installation
        prefix = installation.prefix
        (prefix / "bin").mkdir(parents=True)
        (prefix / "bin/hz").write_text("fixture")
        (selected_bin / "hz").symlink_to(prefix / "bin/hz")
        (prefix / "uv-receipt.toml").write_text(
            '[tool]\nrequirements=[{name="heinzel"}]\nentrypoints=[{name="hz",install-path='
            + repr(str(selected_bin / "hz"))
            + "}]\n"
        )
        installation.record_startup_state(expected=installation.startup_fingerprint())
    assert [command[1] for command in calls] == ["managername", "manageruid", "list", "managername", "manageruid"] * 2
    assert not service._handover_pending()
    service._write_state("standard-install-pending.json", {"token": "a" * 64})
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(installation, "startup_state_matches", lambda: False)
        with pytest.raises(HeinzelError, match="could not be verified"):
            service.recover_quiescent_install(selected_bin)
    assert service._handover_pending()
    service.recover_quiescent_install(selected_bin)
    assert not service._handover_pending()


@pytest.mark.parametrize(
    "failure", ["background", "wrong_uid", "loaded", "malformed", "truncated", "error", "changed_context"]
)
def test_quiescent_denies_ambiguous_or_present_manager_state(make, bin_dir, tmp_path, failure):
    from heinzel.kernel import ProcessOutcome

    service = make(runner=Runner(bin_dir))
    selected_bin = _fresh_quiescence_target(service, tmp_path)
    count = []

    def probe(command):
        count.append(command[1])
        name = (
            "Background\n" if failure == "background" or (failure == "changed_context" and len(count) > 3) else "Aqua\n"
        )
        uid = "502\n" if failure == "wrong_uid" else "501\n"
        listing = "PID\tStatus\tLabel\n"
        if failure == "loaded":
            listing += f"-\t0\t{LABEL}\n"
        if failure == "malformed":
            listing += "not-three-columns\n"
        return ProcessOutcome(
            1 if failure == "error" else 0,
            {"managername": name, "manageruid": uid, "list": listing}[command[1]],
            "",
            truncated=failure == "truncated",
        )

    service._quiescence_probe = probe
    with pytest.raises(HeinzelError, match="not proven quiescent") as refused, service.quiescence_guard(selected_bin):
        pytest.fail("ambiguous manager state must deny")
    expected = {
        "background": "desktop session",
        "wrong_uid": "desktop session",
        "changed_context": "desktop session",
        "loaded": "did not confirm daemon absence",
        "malformed": "did not confirm daemon absence",
        "truncated": "inspection failed",
        "error": "inspection failed",
    }
    assert expected[failure] in str(refused.value)
    assert "uvx --from heinzel hz system daemon status" in str(refused.value)


@pytest.mark.parametrize("kind", ["unit", "marker", "request", "pending", "symlink"])
def test_quiescent_refuses_any_existing_daemon_artifact(make, bin_dir, tmp_path, kind):
    service = make(runner=Runner(bin_dir))
    selected_bin = _fresh_quiescence_target(service, tmp_path)
    if kind in {"unit", "symlink"}:
        path = service._unit_paths()[0]
        path.parent.mkdir(parents=True)
    else:
        name = {"marker": "install.json", "request": "handover-request.json", "pending": "mutation-pending.json"}[kind]
        path = service.state_dir / name
        path.parent.mkdir(parents=True)
    if kind == "symlink":
        path.symlink_to(tmp_path / "missing")
    else:
        path.write_text("uncertain")
    with pytest.raises(HeinzelError) as refused, service.quiescence_guard(selected_bin):
        pytest.fail("existing or uncertain daemon artifacts must deny")
    expected = {
        "request": "previous daemon or installation transaction",
        "pending": "daemon lifecycle is changing",
    }.get(kind, "retained daemon unit")
    assert expected in str(refused.value)


def test_quiescent_refuses_writable_unit_parent_before_manager_query(make, bin_dir, tmp_path):
    service = make(runner=Runner(bin_dir))
    selected_bin = _fresh_quiescence_target(service, tmp_path)
    service.unit_dir.mkdir(parents=True)
    old_mode = service.unit_dir.stat().st_mode
    service.unit_dir.chmod(old_mode | 0o002)
    calls = []

    def probe(command):
        from heinzel.kernel import ProcessOutcome

        calls.append(command)
        return ProcessOutcome(0, "", "")

    service._quiescence_probe = probe
    try:
        with pytest.raises(HeinzelError, match="not proven quiescent"), service.quiescence_guard(selected_bin):
            pytest.fail("writable unit parent must deny")
    finally:
        service.unit_dir.chmod(old_mode)
    assert not calls


def test_quiescence_reader_bounds_an_owned_child_output():
    import sys

    from heinzel.modules.system.daemon._service import _quiescence_command

    result = _quiescence_command([sys.executable, "-c", "print('x' * 1100000)"])
    assert result.truncated
    assert result.returncode is None


def _record_quiescent_tool(prefix, bin_dir):
    from heinzel import Progress
    from heinzel.kernel import Installation

    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin/hz").write_text("fixture")
    (bin_dir / "hz").unlink(missing_ok=True)
    (bin_dir / "hz").symlink_to(prefix / "bin/hz")
    (prefix / "uv-receipt.toml").write_text(
        f'[tool]\nrequirements=[{{name="heinzel"}}]\nentrypoints=[{{name="hz",install-path={str(bin_dir / "hz")!r}}}]\n'
    )
    installation = Installation(Progress(), prefix=prefix, uv="uv")
    installation.record_startup_state(expected=installation.startup_fingerprint())
    return installation


@pytest.fixture
def stopped_tool_daemon(make, bin_dir, tmp_path, request):
    service = make(runner=Runner(bin_dir), platform=getattr(request, "param", "darwin"))
    service._quiescence_installation = _record_quiescent_tool(tmp_path / "tools" / "heinzel", bin_dir)
    service.install(timedelta(minutes=5))
    return service


@pytest.mark.parametrize("paused", [False, True])
@pytest.mark.parametrize("removed", [False, True])
def test_quiescence_preserves_owned_stopped_unit_and_pause_state(stopped_tool_daemon, bin_dir, paused, removed):
    from heinzel.kernel import ProcessOutcome

    service = stopped_tool_daemon
    prefix = service._quiescence_installation.prefix
    if paused:
        service.pause()
    unit = service._unit_paths()[0]
    before = unit.read_bytes()
    marker = service.state_dir / "install.json"
    marker_before = marker.read_bytes()
    if removed:
        shutil.rmtree(prefix)
        (bin_dir / "hz").unlink()

    def probe(command):
        output = {"managername": "Aqua\n", "manageruid": "501\n", "list": "PID\tStatus\tLabel\n"}
        return ProcessOutcome(0, output[command[1]], "")

    service._quiescence_probe = probe
    with service.quiescence_guard(bin_dir):
        assert unit.read_bytes() == before
        assert marker.read_bytes() == marker_before
        if removed:
            _record_quiescent_tool(prefix, bin_dir)
        if paused:
            with pytest.raises(HeinzelError, match="lifecycle is changing"):
                service.resume()
    assert service.paused == paused
    assert unit.read_bytes() == before
    assert marker.read_bytes() == marker_before


@pytest.mark.parametrize(
    "artifact", ["prefix_link", "prefix_writable", "partial_prefix", "receipt", "unit", "unit_mode", "marker"]
)
def test_removed_tool_retained_unit_rejects_invalid_artifacts(stopped_tool_daemon, bin_dir, tmp_path, artifact):
    service = stopped_tool_daemon
    prefix = service._quiescence_installation.prefix
    shutil.rmtree(prefix)
    (bin_dir / "hz").unlink()
    unit = service._unit_paths()[0]
    marker = service.state_dir / "install.json"
    if artifact == "prefix_link":
        other = tmp_path / "other"
        other.mkdir()
        prefix.symlink_to(other, target_is_directory=True)
    elif artifact in {"prefix_writable", "partial_prefix"}:
        prefix.mkdir()
        if artifact == "prefix_writable":
            prefix.chmod(0o777)
    elif artifact == "receipt":
        _record_quiescent_tool(prefix, bin_dir)
        (prefix / "uv-receipt.toml").write_text('[tool]\nrequirements=[{name="other"}]\n')
    elif artifact == "unit":
        unit.write_text(unit.read_text() + "modified")
    elif artifact == "unit_mode":
        unit.chmod(0o666)
    else:
        data = json.loads(marker.read_text())
        data["executable"] = str(tmp_path / "other-hz")
        marker.write_text(json.dumps(data))
    service._quiescence_probe = lambda _command: pytest.fail("invalid artifacts must deny before manager queries")
    with pytest.raises(HeinzelError, match="not proven quiescent"), service.quiescence_guard(bin_dir):
        pytest.fail("invalid retained daemon artifacts must deny")


@pytest.mark.parametrize("shim", ["hz", "heinzel", "hz-install"])
def test_removed_tool_rejects_leftover_shims(stopped_tool_daemon, bin_dir, shim):
    service = stopped_tool_daemon
    prefix = service._quiescence_installation.prefix
    shutil.rmtree(prefix)
    (bin_dir / "hz").unlink()
    (bin_dir / shim).symlink_to(prefix / "bin" / shim)
    service._quiescence_probe = lambda _command: pytest.fail("leftover shims must deny before manager queries")
    with pytest.raises(HeinzelError, match="not proven quiescent"), service.quiescence_guard(bin_dir):
        pytest.fail("even a broken leftover shim must deny")


@pytest.mark.parametrize("stopped_tool_daemon", ["linux"], indirect=True)
def test_removed_tool_does_not_change_linux_retained_unit_policy(stopped_tool_daemon, bin_dir):
    service = stopped_tool_daemon
    assert service._quiescence_unit_state(bin_dir)
    shutil.rmtree(service._quiescence_installation.prefix)
    (bin_dir / "hz").unlink()
    assert not service._quiescence_unit_state(bin_dir)


@pytest.mark.parametrize("manager", ["loaded", "unknown", "malformed"])
def test_removed_tool_retained_unit_still_requires_actual_manager_absence(stopped_tool_daemon, bin_dir, manager):
    from heinzel.kernel import ProcessOutcome

    service = stopped_tool_daemon
    shutil.rmtree(service._quiescence_installation.prefix)
    (bin_dir / "hz").unlink()
    outputs = {"managername": "Aqua\n", "manageruid": "501\n", "list": "PID Status Label\n"}
    if manager != "unknown":
        outputs["list"] += f"4242 0 {LABEL}\n" if manager == "loaded" else "invalid\n"
    service._quiescence_probe = lambda command: ProcessOutcome(
        1 if manager == "unknown" else 0, outputs[command[1]], ""
    )
    with pytest.raises(HeinzelError, match="not proven quiescent"), service.quiescence_guard(bin_dir):
        pytest.fail("retained files cannot substitute for manager absence")


@pytest.mark.parametrize(
    "pending",
    ["handover-pending.json", "handover-request.json", "mutation-pending.json", "standard-install-pending.json"],
)
def test_removed_tool_retained_unit_denies_pending_lifecycle_state(stopped_tool_daemon, bin_dir, pending):
    service = stopped_tool_daemon
    shutil.rmtree(service._quiescence_installation.prefix)
    (bin_dir / "hz").unlink()
    (service.state_dir / pending).write_text("{}")
    service._quiescence_probe = lambda _command: pytest.fail("pending lifecycle state must deny before manager queries")
    with pytest.raises(HeinzelError), service.quiescence_guard(bin_dir):
        pytest.fail("retained files must not bypass pending lifecycle state")


@pytest.mark.parametrize("replacement", ["missing", "incomplete"])
def test_retained_unit_reinstall_cannot_complete_without_verified_replacement(
    stopped_tool_daemon, bin_dir, replacement
):
    from heinzel.kernel import ProcessOutcome

    service = stopped_tool_daemon
    prefix = service._quiescence_installation.prefix
    shutil.rmtree(prefix)
    (bin_dir / "hz").unlink()
    unchanged = {path: path.read_bytes() for path in [*service._unit_paths(), service.state_dir / "install.json"]}
    outputs = {"managername": "Aqua\n", "manageruid": "501\n", "list": "PID Status Label\n"}
    service._quiescence_probe = lambda command: ProcessOutcome(0, outputs[command[1]], "")
    admitted = []

    def attempt():
        with service.quiescence_guard(bin_dir):
            admitted.append(True)
            if replacement == "incomplete":
                prefix.mkdir()

    with pytest.raises(HeinzelError, match="Cannot verify the running uv tool"):
        attempt()
    assert admitted == [True]
    assert (service.state_dir / "standard-install-pending.json").is_file()
    assert all(path.read_bytes() == payload for path, payload in unchanged.items())


def test_quiescence_refuses_symlinked_selected_prefix(make, tmp_path):
    service = make()
    selected_bin = _fresh_quiescence_target(service, tmp_path)
    prefix = service._quiescence_installation.prefix
    other = tmp_path / "other" / "heinzel"
    other.mkdir(parents=True)
    prefix.symlink_to(other, target_is_directory=True)
    with pytest.raises(HeinzelError, match="not proven quiescent"), service.quiescence_guard(selected_bin):
        pass


def test_quiescence_refuses_unknown_platform_before_manager_query(make, tmp_path):
    service = make()
    selected_bin = _fresh_quiescence_target(service, tmp_path)
    service._platform = "freebsd"
    service._quiescence_probe = lambda _command: pytest.fail("unexpected manager query")
    with pytest.raises(HeinzelError, match="not proven quiescent"), service.quiescence_guard(selected_bin):
        pass


def test_quiescence_interrupted_install_retains_pending(make, tmp_path):
    from heinzel.kernel import ProcessOutcome

    service = make()
    selected_bin = _fresh_quiescence_target(service, tmp_path)
    output = {"managername": "Aqua\n", "manageruid": "501\n", "list": "PID\tStatus\tLabel\n"}
    service._quiescence_probe = lambda command: ProcessOutcome(0, output[command[1]], "")

    def interrupted():
        with service.quiescence_guard(selected_bin):
            assert service._handover_pending()
            msg = "interrupted"
            raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="interrupted"):
        interrupted()
    assert service._handover_pending()
    with pytest.raises(HeinzelError, match="requires recovery"), service.quiescence_guard(selected_bin):
        pass


@pytest.mark.parametrize("pending", ["standard-install-pending.json", "mutation-pending.json", "handover-pending.json"])
@pytest.mark.parametrize("operation", ["install", "restart", "uninstall"])
def test_pending_transaction_denies_manager_writers_without_effect(make, bin_dir, pending, operation):
    runner = Runner(bin_dir)
    service = make(runner=runner)
    service.install(timedelta(minutes=5))
    unit = service._unit_paths()[0]
    marker = service.state_dir / "install.json"
    original_unit, original_marker = unit.read_bytes(), marker.read_bytes()
    pending_path = service.state_dir / pending
    pending_path.write_text('{"token":"fixture"}')
    before = pending_path.read_bytes()
    runner.commands.clear()
    writer = {
        "install": lambda: service.install(timedelta(minutes=5)),
        "restart": service.restart,
        "uninstall": service.uninstall,
    }[operation]
    with pytest.raises(HeinzelError, match="requires recovery"):
        writer()
    assert runner.commands == []
    assert unit.read_bytes() == original_unit
    assert marker.read_bytes() == original_marker
    assert pending_path.read_bytes() == before
    service.pause()
    assert service.paused


@pytest.mark.parametrize("operation", ["install", "restart", "uninstall"])
def test_writer_rechecks_pending_after_acquiring_lifecycle_lock(make, bin_dir, monkeypatch, operation):
    from contextlib import contextmanager

    from heinzel.modules.system.daemon import _service

    runner = Runner(bin_dir)
    service = make(runner=runner)
    service.install(timedelta(minutes=5))
    unit = service._unit_paths()[0]
    original = unit.read_bytes()
    runner.commands.clear()
    real_lock = _service.lock

    @contextmanager
    def interleaved(path):
        with real_lock(path) as acquired:
            if acquired:
                (service.state_dir / "standard-install-pending.json").write_text('{"token":"fixture"}')
            yield acquired

    monkeypatch.setattr(_service, "lock", interleaved)
    writer = {
        "install": lambda: service.install(timedelta(minutes=5)),
        "restart": service.restart,
        "uninstall": service.uninstall,
    }[operation]
    with pytest.raises(HeinzelError, match="requires recovery"):
        writer()
    assert runner.commands == []
    assert unit.read_bytes() == original
    assert (service.state_dir / "standard-install-pending.json").exists()


def test_restart_denies_pending_even_when_unit_absent(make):
    service = make(runner=Refuses())
    service.state_dir.mkdir(parents=True)
    pending = service.state_dir / "standard-install-pending.json"
    pending.write_text('{"token":"fixture"}')
    with pytest.raises(HeinzelError, match="requires recovery"):
        service.restart()
    assert pending.exists()


def test_recovery_never_clears_unowned_or_conflicting_transaction(make, bin_dir, tmp_path):
    from heinzel.kernel import ProcessOutcome

    service = make(runner=Runner(bin_dir))
    selected_bin = _fresh_quiescence_target(service, tmp_path)
    output = {"managername": "Aqua\n", "manageruid": "501\n", "list": "PID\tStatus\tLabel\n"}
    service._quiescence_probe = lambda command: ProcessOutcome(0, output[command[1]], "")
    service._write_state("standard-install-pending.json", {"token": "bad"})
    with pytest.raises(HeinzelError, match="recovery record"):
        service.recover_quiescent_install(selected_bin)
    assert (service.state_dir / "standard-install-pending.json").exists()
    installation = service._quiescence_installation
    prefix = installation.prefix
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin/hz").write_text("fixture")
    (selected_bin / "hz").symlink_to(prefix / "bin/hz")
    (prefix / "uv-receipt.toml").write_text(
        '[tool]\nrequirements=[{name="heinzel"}]\nentrypoints=[{name="hz",install-path='
        + repr(str(selected_bin / "hz"))
        + "}]\n"
    )
    installation.record_startup_state(expected=installation.startup_fingerprint())
    service._write_state("standard-install-pending.json", {"token": "a" * 64})
    service._write_state("mutation-pending.json", {"token": "b" * 64})
    with pytest.raises(HeinzelError, match="could not be verified"):
        service.recover_quiescent_install(selected_bin)
    assert (service.state_dir / "standard-install-pending.json").exists()
    assert (service.state_dir / "mutation-pending.json").exists()


@pytest.mark.parametrize(
    "failure", ["none", "bus", "busy", "active", "job", "failed", "pid", "malformed", "changed", "context", "error"]
)
def test_linux_quiescence_requires_own_bus_and_both_inactive_units(make, tmp_path, failure):
    from heinzel.kernel import ProcessOutcome

    service = make(platform="linux")
    binary = _fresh_quiescence_target(service, tmp_path)
    identities = iter([(1, 2, 3, 4), (1, 2, 3, 5) if failure == "context" else (1, 2, 3, 4)])
    service._linux_manager_identity = lambda: None if failure == "bus" else next(identities)
    calls = []

    def probe(command):
        calls.append(command)
        if command[-1] == "--property=Version":
            version = "different" if failure == "changed" and len(calls) == 6 else "257"
            return ProcessOutcome(0, f"Version={version}\n", "")
        unit = command[-1]
        index = len(calls)
        active = "active" if failure == "active" and unit.endswith(".service") else "inactive"
        load = "error" if failure == "failed" else "not-found"
        job = "12" if failure == "job" else ""
        pid = "23" if failure == "pid" and unit.endswith(".service") else "0"
        result = f"Id={unit}\nLoadState={load}\nActiveState={active}\nSubState=dead\nJob={job}\n"
        result += f"MainPID={pid}\nControlPID=0\n"
        if failure == "malformed":
            result += "Id=other.service\n"
        if failure == "busy" and index == 4:
            result = result.replace("ActiveState=inactive", "ActiveState=activating")
        return ProcessOutcome(1 if failure == "error" else 0, result, "")

    service._quiescence_probe = probe
    assert service._quiescent_snapshot(binary) is (failure == "none")
    if failure == "none":
        assert [call[-1] for call in calls] == [
            "--property=Version",
            "hz-daemon.service",
            "hz-daemon.timer",
            "hz-daemon.service",
            "hz-daemon.timer",
            "--property=Version",
        ]


def test_linux_quiescence_denies_missing_properties_and_queued_jobs():
    from heinzel.modules.system.daemon._service import _systemd_quiescent_unit

    unit = "hz-daemon.service"
    base = f"Id={unit}\nLoadState=loaded\nActiveState=inactive\nSubState=dead\nJob=\nMainPID=0\nControlPID=0\n"
    assert _systemd_quiescent_unit(base, unit)
    assert not _systemd_quiescent_unit(base.replace("Job=\n", ""), unit)
    assert not _systemd_quiescent_unit(base.replace("Job=\n", "Job=1\n"), unit)
    assert not _systemd_quiescent_unit(base.replace("MainPID=0\n", ""), unit)


def test_linux_quiescence_guard_keeps_pending_if_install_is_interrupted(make, tmp_path):
    from heinzel.kernel import ProcessOutcome

    service = make(platform="linux")
    binary = _fresh_quiescence_target(service, tmp_path)
    service._linux_manager_identity = lambda: (1, 2, 3, 4)

    def probe(command):
        unit = command[-1]
        if unit == "--property=Version":
            return ProcessOutcome(0, "Version=257 (fixture)\n", "")
        return ProcessOutcome(0, f"Id={unit}\nLoadState=not-found\nActiveState=inactive\nSubState=dead\nJob=\n", "")

    service._quiescence_probe = probe

    def interrupted():
        with service.quiescence_guard(binary):
            assert service._handover_pending()
            msg = "interrupted"
            raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="interrupted"):
        interrupted()
    assert service._handover_pending()
    with pytest.raises(HeinzelError, match="requires recovery"), service.quiescence_guard(binary):
        pass


def test_linux_manager_environment_ignores_ambient_bus(monkeypatch, tmp_path):
    from heinzel.modules.system.daemon._service import _manager_environment

    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", f"unix:path={tmp_path}/foreign")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("SYSTEMD_BUS_ADDRESS", f"unix:path={tmp_path}/foreign")
    environment = _manager_environment(["/usr/bin/systemctl", "--user", "show"])
    runtime = f"/run/user/{os.getuid()}"
    assert environment["XDG_RUNTIME_DIR"] == runtime
    assert "DBUS_SESSION_BUS_ADDRESS" not in environment
    assert "SYSTEMD_BUS_ADDRESS" not in environment


def test_linux_guard_explains_missing_user_manager(make, tmp_path):
    service = make(platform="linux")
    binary = _fresh_quiescence_target(service, tmp_path)
    service._linux_manager_identity = lambda: None
    service._quiescence_probe = lambda _command: pytest.fail("unexpected manager query")
    with (
        pytest.raises(HeinzelError, match=r"user manager is unavailable.*hz system daemon status"),
        service.quiescence_guard(binary),
    ):
        pytest.fail("unexpected installation")
    assert not service._handover_pending()


def test_linux_private_manager_socket_identity_rejects_wrong_or_replaced_socket(tmp_path, monkeypatch):
    import socket

    from heinzel.modules.system.daemon._service import _linux_user_manager_identity

    run_root = tmp_path / "run"
    user_root = run_root / "user"
    manager = user_root / str(os.getuid()) / "systemd"
    manager.mkdir(parents=True)
    manager.parent.chmod(0o700)
    manager.chmod(0o700)
    original_lstat = Path.lstat

    def stat_with_root_owned_ancestors(path):
        result = original_lstat(path)
        if path in {run_root, user_root}:
            fields = list(result)
            fields[4] = 0
            return os.stat_result(fields)
        return result

    monkeypatch.setattr(Path, "lstat", stat_with_root_owned_ancestors)
    private = manager / "private"
    monkeypatch.chdir(manager)
    with socket.socket(socket.AF_UNIX) as first:
        first.bind("private")
        identity = _linux_user_manager_identity(run_root)
        assert identity is not None
        manager.chmod(0o777)
        assert _linux_user_manager_identity(run_root) is None
        manager.chmod(0o700)
        private.unlink()
        with socket.socket(socket.AF_UNIX) as second:
            second.bind("private")
            assert _linux_user_manager_identity(run_root) != identity


def test_a_local_cli_tick_runs_under_the_owners_daemon_identity(make_kernel, settings, tmp_path):
    """launchd and systemd reach a tick through the CLI head, and approved schedules only run as the daemon."""
    seen: list[str] = []
    user = CurrentUser(tmp_path / "identity")
    kernel = make_kernel()
    kernel.bind(CurrentUser, user)
    service = DaemonService(kernel, settings, DaemonSettings())

    def record(*, dry_run, job):
        assert (dry_run, job) == (False, None)
        seen.append(user.context().authentication)
        return "report"

    service._tick = record
    with user.local("cli"):
        assert service.tick() == "report"
    with user.local("mcp-stdio"):
        service.tick()
    assert seen == ["daemon", "mcp-stdio"]


HISTORIC_UNITS = Path(__file__).parent / "data"


@pytest.mark.parametrize("tag", ["v0.4.0", "v0.7.30"])
def test_units_generated_by_older_releases_are_recognized_exactly(tag):
    from heinzel.modules.system.daemon._units import released_launchd_minutes

    content = (HISTORIC_UNITS / f"launchd-{tag}.plist").read_bytes()
    executable, logs = Path("/Users/u/.local/bin/hz"), Path("/Users/u/Library/Logs/hz")
    assert released_launchd_minutes(content, executable, logs) == 30
    assert released_launchd_minutes(content + b"\n", executable, logs) is None
    assert released_launchd_minutes(content, Path("/Users/u/other/hz"), logs) is None


@pytest.fixture
def retained(make, bin_dir, tmp_path):
    """A unit from a removed tool, no marker: the state a macOS user is left with; `seed` chooses the rest."""
    from heinzel.kernel import ProcessOutcome

    service = make(runner=Runner(bin_dir))
    binary = _fresh_quiescence_target(service, tmp_path)
    unit = service._unit_paths()[0]
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(launchd_plist(binary / "hz", 5, service.log_path.parent))
    calls = []
    state = {"pid": None, "pgrep": 1, "bootout_clears": True}

    def probe(command):
        calls.append(command)
        if command[0] == "/usr/bin/pgrep":
            return ProcessOutcome(state["pgrep"], "", "")
        if command[1] == "bootout":
            if state["bootout_clears"]:
                state["pid"] = None
            return ProcessOutcome(0, "", "")
        entry = f"{state['pid']}\t78\t{LABEL}\n" if state["pid"] else ""
        return ProcessOutcome(
            0, {"managername": "Aqua\n", "manageruid": "501\n", "list": f"PID\tStatus\tLabel\n{entry}"}[command[1]], ""
        )

    service._quiescence_probe = probe
    return service, binary, unit, state, calls


def _retired(service):
    return sorted(path.name for path in (service.state_dir / "retired-units").glob("*/*"))


def test_stopped_released_unit_without_marker_is_moved_aside(retained):
    service, binary, unit, _, calls = retained
    content = unit.read_bytes()
    with service.quiescence_guard(binary):
        assert not unit.exists()
        _record_quiescent_tool(service._quiescence_installation.prefix, binary)
    assert _retired(service) == [unit.name]
    moved = next((service.state_dir / "retired-units").glob(f"*/{unit.name}"))
    assert moved.read_bytes() == content
    assert not any(command[1] == "bootout" for command in calls)


@pytest.mark.parametrize("marker", [False, True])
def test_loaded_dead_daemon_with_missing_executable_is_booted_out_and_moved(retained, marker):
    service, binary, unit, state, calls = retained
    state["pid"] = "-"
    if marker:
        record = {"interval_minutes": 5, "executable": str(binary / "hz"), "platform": "darwin"}
        service._write_state("install.json", record)
    with service.quiescence_guard(binary):
        assert not unit.exists()
        _record_quiescent_tool(service._quiescence_installation.prefix, binary)
    assert unit.name in _retired(service)
    assert ("install.json" in _retired(service)) is marker
    assert ["/bin/launchctl", "bootout", f"gui/{service._uid()}/{LABEL}"] in calls


def test_running_daemon_is_never_unloaded_or_moved(retained):
    service, binary, unit, state, calls = retained
    state["pid"] = "4242"
    with pytest.raises(HeinzelError, match="hz system daemon uninstall"), service.quiescence_guard(binary):
        pytest.fail("a running daemon must deny")
    assert unit.exists()
    assert not any(command[1] == "bootout" for command in calls)


def test_dead_daemon_with_a_matching_process_is_refused(retained):
    service, binary, unit, state, calls = retained
    state.update(pid="-", pgrep=0)
    with pytest.raises(HeinzelError, match="not proven quiescent"), service.quiescence_guard(binary):
        pytest.fail("a live tick must deny")
    assert unit.exists()
    assert not any(command[1] == "bootout" for command in calls)


def test_dead_daemon_with_its_executable_present_is_refused(retained):
    service, binary, unit, state, calls = retained
    state["pid"] = "-"
    (binary / "hz").write_text("fixture")
    with pytest.raises(HeinzelError, match="not proven quiescent"), service.quiescence_guard(binary):
        pytest.fail("a daemon that can still start must deny")
    assert unit.exists()
    assert not any(command[1] == "bootout" for command in calls)


def test_bootout_that_does_not_clear_keeps_the_unit(retained):
    service, binary, unit, state, _ = retained
    state.update(pid="-", bootout_clears=False)
    with pytest.raises(HeinzelError, match="not proven quiescent"), service.quiescence_guard(binary):
        pytest.fail("an unconfirmed bootout must deny")
    assert unit.exists()


@pytest.mark.parametrize("edit", ["edited", "other_executable", "mode", "disagreeing_marker"])
def test_unrecognized_unit_is_left_alone_with_the_exact_command(retained, tmp_path, edit):
    service, binary, unit, state, calls = retained
    state["pid"] = "-"
    if edit == "edited":
        unit.write_text(unit.read_text().replace("tick", "evil"))
    elif edit == "other_executable":
        unit.write_text(launchd_plist(tmp_path / "other/hz", 5, service.log_path.parent))
    elif edit == "mode":
        unit.chmod(0o666)
    else:
        service._write_state("install.json", {"executable": str(tmp_path / "other/hz"), "platform": "darwin"})
    with pytest.raises(HeinzelError, match="not proven quiescent") as refused, service.quiescence_guard(binary):
        pytest.fail("an unrecognized unit must deny")
    message = str(refused.value)
    assert "retained daemon unit or install receipt" in message
    assert "plutil -p" in message
    assert "mkdir -p ~/hz-retired && mv" in message
    assert unit.exists()
    assert calls == []


def test_marker_without_a_unit_gets_the_exact_command(retained):
    service, binary, unit, _, _ = retained
    unit.unlink()
    service._write_state("install.json", {"interval_minutes": 5})
    with pytest.raises(HeinzelError, match="mkdir -p ~/hz-retired && mv"), service.quiescence_guard(binary):
        pytest.fail("a marker without a unit must deny")


def test_impossible_marker_interval_denies_instead_of_crashing(retained):
    service, binary, _unit, _, _ = retained
    service._write_state(
        "install.json", {"interval_minutes": 7, "executable": str(binary / "hz"), "platform": "darwin"}
    )
    with pytest.raises(HeinzelError, match="not proven quiescent"), service.quiescence_guard(binary):
        pytest.fail("an impossible interval must deny")
