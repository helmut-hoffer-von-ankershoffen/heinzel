"""One contract per port in `heinzel.ports`: the fake and the real adapter both pass it."""

import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from heinzel import _adapter_system
from heinzel.modules.system.updater._adapter_state import FileInstallationState
from heinzel.modules.ui._adapter_service import (
    ForegroundServiceManager,
    Job,
    LaunchdServiceManager,
    SystemdServiceManager,
)
from heinzel.ports import Clock, InstallationState, ProcessRunner, ReleaseSource, ServiceManager
from heinzel.testing import (
    FakeClock,
    FakeJob,
    FakeReleaseSource,
    FakeServiceManager,
    MemoryInstallationState,
    ScriptedProcessRunner,
)

pytestmark = pytest.mark.unit

SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]


# --- Clock ---------------------------------------------------------------------------------------------------------


@pytest.fixture(params=["fake", "system"])
def clock(request) -> Clock:
    return FakeClock() if request.param == "fake" else _adapter_system.SystemClock()


def test_a_clock_tells_aware_time_that_does_not_go_back(clock):
    first = clock.now()
    assert first.tzinfo is not None
    assert clock.now() >= first
    assert clock.monotonic() <= clock.monotonic()


def test_a_clock_sleeps_and_time_passes(clock):
    before = clock.monotonic()
    clock.sleep(0.01)
    assert clock.monotonic() - before >= 0.01 - 1e-3


def test_a_deadline_is_that_many_seconds_ahead(clock):
    start = clock.monotonic()
    deadline = clock.deadline(5)
    assert 5 <= deadline - start < 5.5


def test_the_fake_clock_never_waits_and_remembers_the_waits():
    fake = FakeClock(datetime(2026, 5, 1, tzinfo=UTC))
    real_start = time.monotonic()
    fake.sleep(3600)
    assert time.monotonic() - real_start < 0.5
    assert fake.now() == datetime(2026, 5, 1, 1, tzinfo=UTC)
    assert fake.sleeps == [3600]


# --- ProcessRunner -------------------------------------------------------------------------------------------------


@pytest.fixture(params=["scripted", "subprocess"])
def runner(request) -> ProcessRunner:
    if request.param == "scripted":
        return (
            ScriptedProcessRunner()
            .on(sys.executable, "-c", "print('hi')", stdout="hi\n")
            .on(sys.executable, "-c", "raise SystemExit(3)", returncode=3)
        )
    return _adapter_system.SubprocessRunner()


def test_a_runner_returns_output_and_the_exit_code_without_raising(runner):
    ok = runner.run([sys.executable, "-c", "print('hi')"], timeout=20)
    assert (ok.returncode, ok.stdout.strip()) == (0, "hi")
    failed = runner.run([sys.executable, "-c", "raise SystemExit(3)"], timeout=20)
    assert failed.returncode == 3


def test_a_runner_spawns_a_child_that_can_be_watched_and_stopped(runner):
    child = runner.spawn(SLEEPER)
    assert child.pid > 0
    assert child.poll() is None
    child.kill()
    assert child.wait(10) != 0
    assert child.poll() is not None


def test_a_runner_that_cannot_start_a_program_raises_oserror():
    real = _adapter_system.SubprocessRunner()
    with pytest.raises(OSError):  # noqa: PT011 - the words are the OS's own
        real.run(["/nonexistent/program"], timeout=5)
    scripted = ScriptedProcessRunner().raises("/nonexistent/program", error=FileNotFoundError("missing"))
    with pytest.raises(OSError, match="missing"):
        scripted.run(["/nonexistent/program"], timeout=5)


def test_a_real_run_that_takes_too_long_times_out():
    with pytest.raises(subprocess.TimeoutExpired):
        _adapter_system.SubprocessRunner().run(SLEEPER, timeout=0.2)


# --- ServiceManager ------------------------------------------------------------------------------------------------


@pytest.fixture(params=["fake", "foreground"])
def manager(request):
    if request.param == "fake":
        yield FakeServiceManager("1.0.0"), FakeJob("web")
        return
    real = ForegroundServiceManager({"web": SLEEPER}, _adapter_system.SubprocessRunner(), version="1.0.0")
    try:
        yield real, Job("web")
    finally:
        real.stop_all()


def test_a_job_that_was_never_loaded_is_not_loaded_and_cannot_be_kicked(manager):
    service, job = manager
    assert isinstance(service, ServiceManager)
    assert service.status(job).loaded is False
    with pytest.raises(OSError, match="not loaded"):
        service.kickstart(job, force=True)


def test_a_loaded_job_runs_and_a_forced_kick_gives_it_a_new_process(manager):
    service, job = manager
    service.load(job)
    first = service.status(job)
    assert (first.loaded, first.pid is not None, first.version) == (True, True, "1.0.0")
    service.kickstart(job, force=True)
    second = service.status(job)
    assert second.loaded
    assert second.pid not in {None, first.pid}


def test_a_kick_without_force_keeps_a_loaded_job_loaded(manager):
    service, job = manager
    service.load(job)
    service.kickstart(job)
    assert service.status(job).loaded


def test_launchd_and_systemd_send_the_commands_the_service_always_ran():
    sent: list[list[str]] = []

    def run(command):
        sent.append(list(command))
        return subprocess.CompletedProcess(command, 0, "", "")

    LaunchdServiceManager(run, uid=501, launchctl="/bin/launchctl").kickstart(Job("team.heinzel.web"), force=True)
    LaunchdServiceManager(run, uid=501, launchctl="/bin/launchctl").kickstart(Job("team.heinzel.daemon"))
    SystemdServiceManager(run, systemctl="/usr/bin/systemctl").kickstart(Job("heinzel-web.service"), force=True)
    SystemdServiceManager(run, systemctl="/usr/bin/systemctl").kickstart(Job("heinzel.timer"))
    assert sent == [
        ["/bin/launchctl", "kickstart", "-k", "gui/501/team.heinzel.web"],
        ["/bin/launchctl", "kickstart", "gui/501/team.heinzel.daemon"],
        ["/usr/bin/systemctl", "--user", "restart", "heinzel-web.service"],
        ["/usr/bin/systemctl", "--user", "try-restart", "heinzel.timer"],
    ]


def test_a_manager_that_refuses_is_an_oserror_with_its_words():
    def refuse(command):
        return subprocess.CompletedProcess(command, 113, "", "Could not find service")

    with pytest.raises(OSError, match="Could not find service"):
        LaunchdServiceManager(refuse, uid=1).kickstart(Job("x"), force=True)


# --- ReleaseSource -------------------------------------------------------------------------------------------------


@pytest.fixture(params=["fake", "github"])
def releases(request, monkeypatch):
    if request.param == "fake":
        source = FakeReleaseSource("1.2.0")
        source.publish("1.1.0", accepted=False)
        source.publish("1.2.0")
        return source
    from heinzel.modules.system import _github, _service

    facts = {"tag_name": "v1.2.0", "published_at": "2026-01-01T00:00:00+00:00", "prerelease": False}
    monkeypatch.setattr(
        _github,
        "latest_release_url",
        lambda repository, timeout: f"https://github.com/{repository}/releases/tag/v1.2.0",
    )

    def get_json(url, token, timeout):
        if "/git/ref/tags/" in url:
            return {"object": {"type": "commit", "sha": "a" * 40}}
        return facts

    monkeypatch.setattr(_github, "get_json", get_json)
    monkeypatch.setattr(
        _github, "read_asset", lambda url, timeout: b'{"project":"heinzel","version":"1.2.0","accepted":true}'
    )
    return _service.GitHubReleases("org/heinzel")


def test_a_release_source_names_the_latest_and_what_is_known_about_it(releases):
    assert isinstance(releases, ReleaseSource)
    latest = releases.latest_version(5)
    assert latest == "1.2.0"
    facts = releases.release_facts(latest, 5)
    assert facts.version == latest
    assert facts.published_at is not None
    assert releases.accepted(latest, 5) is True


# --- InstallationState ---------------------------------------------------------------------------------------------


@pytest.fixture(params=["memory", "files"])
def state(request, tmp_path) -> InstallationState:
    folder = tmp_path / "state"
    return MemoryInstallationState(folder) if request.param == "memory" else FileInstallationState(folder)


def test_a_journal_reads_back_what_was_appended_in_order(state):
    assert list(state.journal("update")) == []
    state.append("update", "started 1.0.0")
    state.append("update", "done 1.0.0")
    assert list(state.journal("update")) == ["started 1.0.0", "done 1.0.0"]
    assert list(state.journal("other")) == []


def test_a_lock_is_exclusive_between_threads(state):
    order: list[str] = []
    inside = threading.Event()
    release = threading.Event()

    def holder():
        with state.lock("update"):
            inside.set()
            release.wait(5)
            order.append("holder")

    thread = threading.Thread(target=holder)
    thread.start()
    assert inside.wait(5)

    def waiter():
        with state.lock("update"):
            order.append("waiter")

    second = threading.Thread(target=waiter)
    second.start()
    second.join(0.2)
    assert second.is_alive(), "the second holder got in while the first held the lock"
    release.set()
    thread.join(5)
    second.join(5)
    assert order == ["holder", "waiter"]


def test_a_state_folder_is_a_path_that_exists_for_real_adapters(state, tmp_path):
    assert isinstance(state.folder, Path)
    if isinstance(state, FileInstallationState):
        assert state.folder.is_dir()
        assert (state.folder.stat().st_mode & 0o777) == 0o700


def test_a_journal_entry_is_one_line(state):
    if isinstance(state, FileInstallationState):
        with pytest.raises(ValueError, match="one line"):
            state.append("update", "two\nlines")


def test_two_servers_that_share_a_state_see_each_others_journal(state):
    server_a, server_b = state, state
    server_a.append("update", "a started")
    assert list(server_b.journal("update")) == ["a started"]
