"""A lifecycle writer waits, bounded, for a running tick or update to let go, and then says plainly why it can't start.

The owner's case (2026-11-12): after a laptop slept, the first ticks after wake held the daemon lifecycle lock while
their jobs ran, and `hz system update` refused at once with "The daemon lifecycle is changing or requires recovery";
a few minutes later it worked. Nothing is archived or recovered here: only a held lock is waited for.
"""

import contextlib
import logging
import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest

from heinzel import HeinzelError, Progress
from heinzel.modules.system.daemon import DaemonService, DaemonSettings
from heinzel.modules.system.daemon._runtime import lock
from tests.fixtures.daemon_manager import RecordedManager as Runner
from tests.fixtures.daemon_manager import record_tool

pytestmark = pytest.mark.unit


class Tasks(Progress):
    """Keeps every progress task description, in order."""

    def __init__(self) -> None:
        self.tasks: list[str] = []

    @contextlib.contextmanager
    def task(self, description, total=None):
        del total
        self.tasks.append(description)
        yield lambda _amount: None


@pytest.fixture
def tasks() -> Tasks:
    return Tasks()


@pytest.fixture
def service(make_kernel, settings, tmp_path, tasks) -> DaemonService:
    binary = tmp_path / "bin"
    binary.mkdir()
    installation = record_tool(tmp_path / "tool-store/heinzel", binary)
    runner = Runner(binary)
    kernel = make_kernel()
    kernel.bind(Progress, tasks)
    built = DaemonService(
        kernel,
        settings,
        DaemonSettings(unit_dir=tmp_path / "units"),
        runner=runner,
        platform="darwin",
        uid=lambda: 501,
        which=lambda name: f"/bin/{name}",
        desktop_session=lambda: "Aqua",
        birth_fence=lambda: None,
        process_birth=lambda: None,
        quiescence_installation=installation,
        quiescence_probe=runner.probe,
        linux_manager_identity=lambda: (1, 2, 3),
        continuity_fingerprint=installation.startup_fingerprint,
        host_boot_evidence=lambda: ("11111111-1111-4111-8111-111111111111", 1),
    )
    runner.attach(built)
    built.lifecycle_poll_seconds = 0.02
    return built


@contextlib.contextmanager
def running_tick(service: DaemonService, *, seconds: float | None = None):
    """Hold the lifecycle and tick locks as a tick running its jobs does; release after `seconds` if given."""
    release = threading.Event()
    holding = threading.Event()

    def hold() -> None:
        with lock(service.state_dir / "lifecycle.lock") as lifecycle, lock(service.state_dir / "tick.lock") as tick:
            assert lifecycle
            assert tick
            holding.set()
            release.wait(seconds if seconds is not None else 30)

    thread = threading.Thread(target=hold)
    thread.start()
    holding.wait(5)
    try:
        yield
    finally:
        release.set()
        thread.join(5)


def _attempt_update(service: DaemonService) -> None:
    with service.mutation_guard(lambda: True, lambda: "b" * 64) as scope:
        scope.capture()
        scope.abort_unchanged()


def _waits(caplog) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.getMessage() == "daemon.lifecycle.wait"]


def test_without_a_wait_a_running_tick_refuses_an_update_at_once_and_names_the_tick(service, tasks):
    """The owner's refusal, reproduced: a tick holds the lock; nothing waits; the message now names who holds it."""
    with running_tick(service), pytest.raises(HeinzelError) as refused:
        _attempt_update(service)
    message = str(refused.value)
    assert message == (
        "The daemon lifecycle is changing: the background service's current run of its jobs is still running. "
        "Nothing was changed; try again in a minute or two."
    )
    assert not tasks.tasks
    assert not (service.state_dir / "mutation-pending.json").exists()


def test_an_update_waits_for_a_running_tick_and_then_proceeds(service, tasks, caplog):
    service.lifecycle_wait_seconds = 5.0
    caplog.set_level(logging.INFO)
    with running_tick(service, seconds=0.2):
        started = time.monotonic()
        _attempt_update(service)
    assert time.monotonic() - started < 5.0
    assert tasks.tasks == ["Waiting for the background service's current run of its jobs to finish (up to 5 s)"]
    start, outcome = _waits(caplog)
    assert (start.phase, start.holder, start.budget_seconds) == ("start", "tick", 5.0)
    assert (outcome.phase, outcome.holder, outcome.outcome) == ("outcome", "tick", "free")
    assert 0 < outcome.waited_seconds < 5.0
    assert not (service.state_dir / "mutation-pending.json").exists()


def test_an_update_gives_up_after_the_bounded_wait_with_a_plain_retry_hint(service, tasks, caplog):
    service.lifecycle_wait_seconds = 0.2
    caplog.set_level(logging.INFO)
    with running_tick(service), pytest.raises(HeinzelError) as refused:
        _attempt_update(service)
    assert str(refused.value) == (
        "The daemon lifecycle is changing: the background service's current run of its jobs is still running after "
        "waiting 0.2 s. Nothing was changed; try again in a minute or two."
    )
    assert tasks.tasks == ["Waiting for the background service's current run of its jobs to finish (up to 0.2 s)"]
    start, outcome = _waits(caplog)
    assert (start.phase, start.holder) == ("start", "tick")
    assert (outcome.phase, outcome.holder, outcome.outcome) == ("outcome", "tick", "busy")
    assert outcome.waited_seconds >= 0.2


def test_a_free_lock_with_a_pending_marker_is_not_waited_for_and_says_recovery(service, tasks):
    service.lifecycle_wait_seconds = 30.0
    service._write_state("mutation-pending.json", {"token": "a" * 64})  # an earlier attempt's, in this boot
    started = time.monotonic()
    with pytest.raises(HeinzelError, match="requires recovery: an earlier update"):
        _attempt_update(service)
    assert time.monotonic() - started < 5.0
    assert not tasks.tasks
    assert (service.state_dir / "mutation-pending.json").exists()  # nothing is archived automatically


def test_install_waits_for_a_running_tick_and_then_installs(service, tasks):
    service.lifecycle_wait_seconds = 5.0
    with running_tick(service, seconds=0.2):
        service.install(timedelta(minutes=5))
    assert all(path.exists() for path in service._unit_paths())
    assert "Waiting for the background service's current run of its jobs to finish (up to 5 s)" in tasks.tasks


def test_install_still_busy_after_the_wait_refuses_with_who_holds_it(service):
    service.lifecycle_wait_seconds = 0.1
    with running_tick(service), pytest.raises(HeinzelError, match="current run of its jobs is still running after"):
        service.install(timedelta(minutes=5))
    assert not service._unit_paths()[0].exists()


def test_restart_waits_for_a_running_tick_and_then_restarts(service, tasks):
    service.install(timedelta(minutes=5))
    service.lifecycle_wait_seconds = 5.0
    with running_tick(service, seconds=0.2):
        result = service.restart()
    assert result.restarted
    assert [task for task in tasks.tasks if task.startswith("Waiting")] == [
        "Waiting for the background service's current run of its jobs to finish (up to 5 s)"
    ]


def test_restart_still_busy_after_the_wait_refuses_with_who_holds_it(service):
    service.install(timedelta(minutes=5))
    service.lifecycle_wait_seconds = 0.1
    with running_tick(service), pytest.raises(HeinzelError, match="current run of its jobs is still running after"):
        service.restart()


def test_another_update_holding_only_the_lifecycle_lock_is_named_as_a_change(service, caplog):
    service.lifecycle_wait_seconds = 0.1
    caplog.set_level(logging.INFO)
    holding, release = threading.Event(), threading.Event()

    def hold() -> None:
        with lock(service.state_dir / "lifecycle.lock") as acquired:
            assert acquired
            holding.set()
            release.wait(30)

    thread = threading.Thread(target=hold)
    thread.start()
    holding.wait(5)
    try:
        with pytest.raises(HeinzelError, match="an update or another Heinzel change is still running"):
            _attempt_update(service)
    finally:
        release.set()
        thread.join(5)
    assert [record.holder for record in _waits(caplog)] == ["other", "other"]


@pytest.mark.parametrize(("seconds", "label"), [(15.0, "15 s"), (89.0, "89 s"), (90.0, "2 min"), (180.0, "3 min")])
def test_the_wait_budget_reads_in_seconds_below_ninety_and_minutes_above(seconds, label):
    from heinzel.modules.system.daemon._service import _budget

    assert _budget(seconds) == label


@pytest.fixture
def user_kernel(make_kernel, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    return make_kernel(builtins=True)


def test_the_user_service_takes_the_heads_budget_and_waits_nothing_unbound(user_kernel):
    from heinzel.kernel import DaemonLifecycleWait

    assert DaemonService.for_user(user_kernel)._wait_budget() == 0.0
    user_kernel.bind(DaemonLifecycleWait, DaemonLifecycleWait(42.0))
    assert DaemonService.for_user(user_kernel)._wait_budget() == 42.0


def test_a_budget_bound_after_the_service_was_built_counts_at_the_next_wait(user_kernel):
    """Resolve order or a cached service can't keep another head's budget: it is read at each wait."""
    from heinzel.kernel import DaemonLifecycleWait

    user_kernel.bind(DaemonLifecycleWait, DaemonLifecycleWait(180.0))
    service = DaemonService.for_user(user_kernel)
    assert service._wait_budget() == 180.0
    user_kernel.bind(DaemonLifecycleWait, DaemonLifecycleWait(10.0))
    assert service._wait_budget() == 10.0


def test_the_terminal_waits_three_minutes(user_kernel):
    from heinzel.heads import cli
    from heinzel.kernel import DaemonLifecycleWait

    service = DaemonService.for_user(user_kernel)  # built before the head binds its budget
    cli.build(user_kernel)
    assert user_kernel.resolve(DaemonLifecycleWait).seconds == 180.0
    assert service._wait_budget() == 180.0


def test_a_web_request_waits_well_under_the_apps_thirty_second_timeout(user_kernel):
    from heinzel.heads import web
    from heinzel.kernel import DaemonLifecycleWait

    service = DaemonService.for_user(user_kernel)
    web.build(user_kernel, port=8000, token="t" * 32, static=None, restart=lambda: None)
    assert user_kernel.resolve(DaemonLifecycleWait).seconds == 10.0
    assert service._wait_budget() == 10.0


def test_a_scheduled_tick_never_waits_even_with_the_terminals_budget_bound(service, tasks):
    """The tick runs in a CLI-built kernel (180 s bound) but must skip at once when the lifecycle is held."""
    from heinzel.kernel import DaemonLifecycleWait

    service._kernel.bind(DaemonLifecycleWait, DaemonLifecycleWait(180.0))
    service.lifecycle_wait_seconds = None
    assert service._wait_budget() == 180.0
    with running_tick(service):
        started = time.monotonic()
        report = service.tick()
    assert report.locked
    assert time.monotonic() - started < 5.0
    assert not [task for task in tasks.tasks if task.startswith("Waiting")]


def test_a_refused_lifecycle_fails_the_install_step_not_finding_the_newest_version(tmp_path):
    """The owner saw "[1/9] Finding the newest version: failed": the version was found; taking the lifecycle failed."""
    from heinzel.modules.system._update import UpdateService
    from tests.unit.modules.system_update_test import (
        REPO,
        FakeMutationLifecycle,
        FakeReleases,
        FakeUv,
        RecordingProgress,
        final_steps,
        installation,
    )

    class Busy(FakeMutationLifecycle):
        def mutation(self, checkpoint, rollback_identity):
            del checkpoint, rollback_identity
            msg = "The daemon lifecycle is changing: the background service's current run of its jobs is still running."
            raise HeinzelError(msg)

    uv = FakeUv("0.2.0")
    progress = RecordingProgress()
    target = installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv)
    service = UpdateService(
        target, FakeReleases("0.4.0"), lifecycle=Busy([], checkpoint_matched=True), progress=progress
    )
    with pytest.raises(HeinzelError, match="lifecycle is changing"):
        service.update(current="0.2.0")
    steps = final_steps(progress)
    assert steps["check"] == ("done", None)
    assert steps["install"][0] == "failed"
    assert "background service" in str(steps["install"][1])
    assert not uv.commands
