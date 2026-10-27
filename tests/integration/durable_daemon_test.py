"""Personal check-ins through the CLI and the daemon's real threaded dispatcher."""

import json
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from heinzel import Notifications
from heinzel.heads.cli import build
from heinzel.modules.system.daemon import DaemonService, DaemonSettings
from heinzel.modules.system.daemon._ledger import Ledger

pytestmark = pytest.mark.integration


def test_check_in_dispatch_replays_intended_slot_after_interrupted_receipt(make_kernel, settings, monkeypatch):
    runner = CliRunner()
    now = datetime(2026, 10, 25, 12, 6, tzinfo=UTC)

    def make():
        kernel = make_kernel(builtins=True)
        kernel.bind(DaemonService, DaemonService(kernel, settings, DaemonSettings(), clock=lambda: now, zone=UTC))
        return kernel

    kernel = make()

    def call(k, *args):
        result = runner.invoke(build(k), [*args, "--json"])
        assert result.exit_code == 0, result.output
        return json.loads(result.output)

    created = call(kernel, "heinzel", "new-chat", "create", "--name", "Helper")
    session = created["session_id"]
    call(kernel, "heinzel", "propose-check-in", session, "proposal")
    call(kernel, "heinzel", "schedule-control", session, "activate", "1", "activate")
    job_id = f"heinzel:check-in-{session.lower()}"

    # Simulate a process dying after notice persistence but before its receipt and heartbeat.
    original = Ledger.append

    def interrupted(self, **kwargs):
        if kwargs["job"] == job_id:
            msg = "interrupted receipt"
            raise OSError(msg)
        return original(self, **kwargs)

    monkeypatch.setattr(Ledger, "append", interrupted)
    result = runner.invoke(build(kernel), ["system", "daemon", "tick", "--json"])
    assert result.exit_code == 1, result.output
    notices = kernel.resolve(Notifications).recent()
    assert len(notices) == 1
    assert notices[0].key.endswith("2026-10-25T12:05:00+00:00")
    (settings.state_dir / "daemon" / "heartbeat.json").unlink()
    monkeypatch.setattr(Ledger, "append", original)
    restarted = make()
    report = call(restarted, "system", "daemon", "tick")
    check_in = next(run for run in report["runs"] if run["job"] == job_id)
    assert check_in["disposition"] == "rendered"
    assert check_in["as_of"].startswith("2026-10-25T12:05:00")
    assert len(restarted.resolve(Notifications).recent()) == 1


def test_foreground_daemon_compiles_owned_check_in_and_reports_failed_notice(make_kernel, settings, monkeypatch):
    import heinzel.heads.daemon as head

    runner = CliRunner()
    kernel = make_kernel(builtins=True)
    now = datetime(2026, 10, 25, 12, 6, tzinfo=UTC)
    kernel.bind(DaemonService, DaemonService(kernel, settings, DaemonSettings(), clock=lambda: now, zone=UTC))

    def call(*args):
        result = runner.invoke(build(kernel), [*args, "--json"])
        assert result.exit_code == 0, result.output
        return json.loads(result.output)

    session = call("heinzel", "new-chat", "create")["session_id"]
    call("heinzel", "propose-check-in", session, "proposal")
    call("heinzel", "schedule-control", session, "activate", "1", "activate")
    notifications = kernel.resolve(Notifications)
    monkeypatch.setattr(notifications, "emit", lambda *args, **kwargs: None)
    real = head.serve
    monkeypatch.setattr(head, "serve", lambda k, i, report: real(k, i, report, ticks=1))
    result = runner.invoke(build(kernel), ["system", "serve", "daemon"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    job_id = f"heinzel:check-in-{session.lower()}"
    run = next(item for item in report["runs"] if item["job"] == job_id)
    assert run["disposition"] == "failed"
    assert "could not be recorded" in run["error"]
    assert notifications.recent() == []
