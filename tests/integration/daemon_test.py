"""`hz system daemon` through the CLI head, with a test-only job module.

Every directory is a temporary one (heinzel.testing); install and uninstall get a fake runner, so no test ever
reaches the real launchd or systemd.
"""

import json
import subprocess
import threading
from datetime import timedelta

import pytest
from typer.testing import CliRunner

from heinzel import JobLet, Module
from heinzel.heads.cli import build
from heinzel.modules.system.daemon import DaemonService, DaemonSettings

pytestmark = pytest.mark.integration

runner = CliRunner()
STUCK = threading.Event()


def job_module(calls: list[str]) -> Module:
    module = Module("example", "Jobs for tests.")

    @module.job(schedule="* * * * *")
    def minutely() -> str:
        """Run every minute."""
        calls.append("minutely")
        return "done"

    return module


def stuck_module() -> Module:
    """A job that overruns its timeout; only `tick --job` runs it, since no test runs on 1 January at midnight."""
    module = Module("slow", "A job that never finishes.")

    def stuck() -> None:
        STUCK.wait()

    module.add(
        JobLet(
            name="stuck", func=stuck, summary="Never finishes.", schedule="0 0 1 1 *", timeout=timedelta(seconds=0.01)
        )
    )
    return module


@pytest.fixture
def calls() -> list[str]:
    return []


@pytest.fixture
def without_workflow(settings):
    """Kernel settings without the workflow module, whose daily `workflow:prune` would join ticks near midnight."""
    return settings.model_copy(update={"disabled_modules": frozenset({"workflow"})})


@pytest.fixture
def kernel(make_kernel, calls, without_workflow):
    return make_kernel(builtins=True, settings_=without_workflow, example=job_module(calls))


def hz(kernel, *args: str):
    result = runner.invoke(build(kernel), list(args))
    assert result.exit_code == 0, result.output
    return result


def test_tick_runs_due_jobs_writes_receipts_and_logs(kernel, calls, settings):
    report = json.loads(hz(kernel, "system", "daemon", "tick", "--json").output)
    assert [(run["job"], run["disposition"]) for run in report["runs"]] == [("example:minutely", "rendered")]
    assert calls == ["minutely"]
    assert len(list((settings.data_dir / "receipts").glob("*.json"))) == 1
    logs = json.loads(hz(kernel, "system", "daemon", "logs", "--lines", "5", "--json").output)
    assert logs[-1]["message"] == "tick done: 1 run(s)"
    assert "tick done" in hz(kernel, "system", "daemon", "logs").output


def test_a_job_that_overruns_its_timeout_fails(make_kernel):
    kernel = make_kernel(builtins=True, slow=stuck_module())
    result = runner.invoke(build(kernel), ["system", "daemon", "tick", "--job", "slow:stuck", "--json"])
    assert result.exit_code == 1  # a failed job makes the tick fail, so launchd, systemd and CI see it
    [run] = json.loads(result.output)["runs"]
    assert (run["disposition"], run["wake"]) == ("failed", "manual")
    assert run["error"].startswith("timed out after 0.01s and was abandoned")
    STUCK.set()


def test_rendered_tick_jobs_and_dry_run(kernel):
    assert "Dry run" in hz(kernel, "system", "daemon", "tick", "--dry-run").output
    assert "example:minutely" in hz(kernel, "system", "daemon", "tick").output
    assert "Nothing was due" in hz(kernel, "system", "daemon", "tick").output
    assert "Jobs" in hz(kernel, "system", "daemon", "jobs").output
    listed = json.loads(hz(kernel, "system", "daemon", "jobs", "--json").output)
    assert [job["job"] for job in listed] == ["example:minutely"]


def test_pause_resume_and_status(kernel, calls):
    assert json.loads(hz(kernel, "system", "daemon", "pause", "--json").output) == {"paused": True}
    assert "Paused" in hz(kernel, "system", "daemon", "tick").output
    status = json.loads(hz(kernel, "system", "daemon", "status", "--json").output)
    assert (status["installed"], status["paused"], status["jobs"]) == (False, True, 1)
    assert json.loads(hz(kernel, "system", "daemon", "resume", "--json").output) == {"paused": False}
    assert calls == []


def test_doctor_reports_the_daemon(kernel):
    report = json.loads(runner.invoke(build(kernel), ["system", "doctor", "--json"]).output)
    assert report["checks"]["system.daemon"][0]["detail"].startswith("not installed")


def test_tick_says_another_tick_is_running(kernel, settings):
    from heinzel.modules.system.daemon._runtime import lock

    with lock(settings.state_dir / "daemon" / "tick.lock"):
        assert "Another tick is running" in hz(kernel, "system", "daemon", "tick").output


def test_jobs_without_any(make_kernel, without_workflow):
    kernel = make_kernel(builtins=True, settings_=without_workflow)
    assert "No module advertises a job" in hz(kernel, "system", "daemon", "jobs").output


def test_install_dry_run_and_uninstall_with_a_fake_service_manager(kernel, settings, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "hz").touch()
    commands: list[list[str]] = []

    def fake(command):
        commands.append(list(command))
        return subprocess.CompletedProcess(command, 0, f"{bin_dir}\n", "")

    service = DaemonService(
        kernel,
        settings,
        DaemonSettings(unit_dir=tmp_path / "units"),
        runner=fake,
        platform="linux",
        which=lambda name: f"/usr/bin/{name}",
    )
    kernel.bind(DaemonService, service)
    dry = hz(kernel, "system", "daemon", "install", "--interval", "10m", "--dry-run").output
    assert "OnCalendar=*:0/10" in dry
    assert "Would run" in dry
    installed = json.loads(hz(kernel, "system", "daemon", "install", "--json").output)
    assert installed["interval_minutes"] == 5
    assert ["/usr/bin/systemctl", "--user", "enable", "--now", "hz-daemon.timer"] in commands
    assert "Installed" in hz(kernel, "system", "daemon", "install").output
    removed = json.loads(hz(kernel, "system", "daemon", "uninstall", "--json").output)
    assert len(removed["removed"]) == 2


def test_install_rejects_a_malformed_interval(kernel):
    result = runner.invoke(build(kernel), ["system", "daemon", "install", "--interval", "5 minutes"])
    assert result.exit_code != 0
    assert "interval" in result.output
