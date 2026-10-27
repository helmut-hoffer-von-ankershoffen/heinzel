"""Personal check-ins through the CLI and the daemon's real threaded dispatcher."""

import json
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from heinzel import Notifications
from heinzel.heads.cli import build
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.system.daemon import DaemonService, DaemonSettings
from heinzel.modules.system.daemon._ledger import Ledger

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("evicted", [False, True])
def test_check_in_dispatch_replays_intended_slot_after_interrupted_receipt(make_kernel, settings, monkeypatch, evicted):
    runner = CliRunner()
    now = datetime(2026, 10, 25, 12, 6, tzinfo=UTC)

    def make():
        kernel = make_kernel(builtins=True)
        session_crud = kernel.resolve(SessionCrud)
        session_crud.clock = lambda: now.replace(minute=4)
        session_crud.repository.clock = lambda: now.replace(minute=4)
        kernel.bind(DaemonService, DaemonService(kernel, settings, DaemonSettings(), clock=lambda: now, zone=UTC))
        return kernel

    kernel = make()
    notifications = kernel.resolve(Notifications)
    notifications.settings = notifications.settings.model_copy(update={"repeat_hours": 0})

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
    original_key = notices[0].key
    if evicted:
        from heinzel import NoticeKind

        for index in range(201):
            assert notifications.emit(NoticeKind.TEST, "Other", route="/", command="hz", key=f"other-{index}")
        assert all(notice.key != original_key for notice in notifications.recent(200))
    (settings.state_dir / "daemon" / "heartbeat.json").unlink()
    monkeypatch.setattr(Ledger, "append", original)
    restarted = make()
    restarted_notifications = restarted.resolve(Notifications)
    restarted_notifications.settings = restarted_notifications.settings.model_copy(update={"repeat_hours": 0})
    report = call(restarted, "system", "daemon", "tick")
    check_in = next(run for run in report["runs"] if run["job"] == job_id)
    assert check_in["disposition"] == "rendered"
    assert check_in["as_of"].startswith("2026-10-25T12:05:00")
    replayed = [notice for notice in restarted.resolve(Notifications).recent(200) if notice.key == original_key]
    assert len(replayed) == (0 if evicted else 1)


def test_foreground_daemon_compiles_owned_check_in_and_reports_failed_notice(make_kernel, settings, monkeypatch):
    import heinzel.heads.daemon as head

    runner = CliRunner()
    kernel = make_kernel(builtins=True)
    now = datetime(2026, 10, 25, 12, 6, tzinfo=UTC)
    session_crud = kernel.resolve(SessionCrud)
    session_crud.clock = lambda: now.replace(minute=4)
    session_crud.repository.clock = lambda: now.replace(minute=4)
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
    assert "outcome is unknown" in run["error"]
    assert notifications.recent() == []


@pytest.mark.parametrize("resume", [False, True])
def test_tick_never_dispatches_a_slot_from_before_activation_or_resume(make_kernel, settings, resume):
    runner = CliRunner()
    kernel = make_kernel(builtins=True)
    time = [datetime(2026, 10, 25, 12, 4 if resume else 6, tzinfo=UTC)]
    session_crud = kernel.resolve(SessionCrud)
    session_crud.clock = lambda: time[0]
    session_crud.repository.clock = lambda: time[0]
    daemon = DaemonService(kernel, settings, DaemonSettings(), clock=lambda: time[0], zone=UTC)
    kernel.bind(DaemonService, daemon)

    def call(*args):
        result = runner.invoke(build(kernel), [*args, "--json"])
        assert result.exit_code == 0, result.output
        return json.loads(result.output)

    session = call("heinzel", "new-chat", "create")["session_id"]
    call("heinzel", "propose-check-in", session, "proposal")
    call("heinzel", "schedule-control", session, "activate", "1", "activate")
    if resume:
        time[0] = time[0].replace(minute=6)
        call("heinzel", "schedule-control", session, "pause", "2", "pause")
        time[0] = time[0].replace(minute=12)
        call("heinzel", "schedule-control", session, "resume", "3", "resume")
    time[0] = time[0].replace(minute=13 if resume else 7)
    first = call("system", "daemon", "tick")
    job = f"heinzel:check-in-{session.lower()}"
    result = next(item for item in first["runs"] if item["job"] == job)
    assert result["disposition"] == "skipped"
    assert "predates" in result["error"]
    assert kernel.resolve(Notifications).recent() == []
    time[0] = time[0].replace(minute=15 if resume else 10)
    second = call("system", "daemon", "tick")
    result = next(item for item in second["runs"] if item["job"] == job)
    assert result["disposition"] == "rendered"
    assert len(kernel.resolve(Notifications).recent()) == 1


@pytest.mark.parametrize("minute", [5, 7])
def test_manual_wake_cannot_dispatch_a_scheduled_check_in(make_kernel, settings, minute):
    runner = CliRunner()
    now = datetime(2026, 10, 25, 12, minute, tzinfo=UTC)
    kernel = make_kernel(builtins=True)
    session_crud = kernel.resolve(SessionCrud)
    session_crud.clock = lambda: now.replace(minute=4)
    session_crud.repository.clock = lambda: now.replace(minute=4)
    kernel.bind(DaemonService, DaemonService(kernel, settings, DaemonSettings(), clock=lambda: now, zone=UTC))

    def call(*args):
        result = runner.invoke(build(kernel), [*args, "--json"])
        assert result.exit_code == 0, result.output
        return json.loads(result.output)

    session = call("heinzel", "new-chat", "create")["session_id"]
    call("heinzel", "propose-check-in", session, "proposal")
    call("heinzel", "schedule-control", session, "activate", "1", "activate")
    report = call("system", "daemon", "tick", "--job", f"heinzel:check-in-{session.lower()}")
    assert report["runs"][0]["disposition"] == "skipped"
    assert "scheduled slots" in report["runs"][0]["error"]
    assert kernel.resolve(Notifications).recent() == []


@pytest.mark.parametrize("pruned", [False, True])
def test_outbox_recovers_or_fails_closed_after_notice_before_outbox_ack(make_kernel, settings, monkeypatch, pruned):
    from heinzel import NoticeKind, SessionWriter

    runner = CliRunner()
    now = datetime(2026, 10, 25, 12, 6, tzinfo=UTC)

    def make():
        kernel = make_kernel(builtins=True)
        crud = kernel.resolve(SessionCrud)
        crud.clock = lambda: now.replace(minute=4)
        crud.repository.clock = lambda: now.replace(minute=4)
        kernel.bind(DaemonService, DaemonService(kernel, settings, DaemonSettings(), clock=lambda: now, zone=UTC))
        notices = kernel.resolve(Notifications)
        notices.settings = notices.settings.model_copy(update={"repeat_hours": 0})
        return kernel

    def call(kernel, *args):
        result = runner.invoke(build(kernel), [*args, "--json"])
        assert result.exit_code == 0, result.output
        return json.loads(result.output)

    kernel = make()
    session = call(kernel, "heinzel", "new-chat", "create")["session_id"]
    call(kernel, "heinzel", "propose-check-in", session, "proposal")
    call(kernel, "heinzel", "schedule-control", session, "activate", "1", "activate")
    job_id = f"heinzel:check-in-{session.lower()}"
    original_append = SessionWriter.append
    original_receipt = Ledger.append

    def interrupt_outbox(writer, kind, **kwargs):
        if kind == "check-in.recorded":
            msg = "interrupted after notice"
            raise OSError(msg)
        return original_append(writer, kind, **kwargs)

    def interrupt_receipt(ledger, **kwargs):
        if kwargs["job"] == job_id:
            msg = "interrupted before receipt"
            raise OSError(msg)
        return original_receipt(ledger, **kwargs)

    monkeypatch.setattr(SessionWriter, "append", interrupt_outbox)
    monkeypatch.setattr(Ledger, "append", interrupt_receipt)
    failed = runner.invoke(build(kernel), ["system", "daemon", "tick", "--json"])
    assert failed.exit_code == 1, failed.output
    notices = kernel.resolve(Notifications)
    first = notices.recent()[0]
    if pruned:
        for index in range(201):
            assert notices.emit(NoticeKind.TEST, "Other", route="/", command="hz", key=f"other-{index}")
        assert all(item.key != first.key for item in notices.recent(200))
    (settings.state_dir / "daemon" / "heartbeat.json").unlink()
    monkeypatch.setattr(SessionWriter, "append", original_append)
    monkeypatch.setattr(Ledger, "append", original_receipt)
    restarted = make()
    replay = runner.invoke(build(restarted), ["system", "daemon", "tick", "--json"])
    if pruned:
        assert replay.exit_code == 1
        assert "outcome is unknown" in replay.output
        assert all(item.key != first.key for item in restarted.resolve(Notifications).recent(200))
    else:
        assert replay.exit_code == 0, replay.output
        assert len([item for item in restarted.resolve(Notifications).recent(200) if item.key == first.key]) == 1


@pytest.mark.parametrize("foreground", [False, True])
def test_slow_daemon_cadence_fails_check_in_instead_of_dropping_slots(make_kernel, settings, monkeypatch, foreground):
    import heinzel.heads.daemon as head

    runner = CliRunner()
    now = datetime(2026, 10, 25, 12, 6, tzinfo=UTC)
    kernel = make_kernel(builtins=True)
    crud = kernel.resolve(SessionCrud)
    crud.clock = lambda: now.replace(minute=4)
    crud.repository.clock = lambda: now.replace(minute=4)
    kernel.bind(DaemonService, DaemonService(kernel, settings, DaemonSettings(), clock=lambda: now, zone=UTC))

    def call(*args):
        result = runner.invoke(build(kernel), [*args, "--json"])
        assert result.exit_code == 0, result.output
        return json.loads(result.output)

    session = call("heinzel", "new-chat", "create")["session_id"]
    call("heinzel", "propose-check-in", session, "proposal")
    activated = call("heinzel", "schedule-control", session, "activate", "1", "activate")
    assert activated["schedule"]["next_wake"] is None
    assert "not verified" in activated["schedule"]["blocked_reason"]
    if foreground:
        original = head.serve
        monkeypatch.setattr(head, "serve", lambda k, interval, report: original(k, interval, report, ticks=1))
        result = runner.invoke(build(kernel), ["system", "serve", "daemon", "--interval", "15m"])
        assert result.exit_code == 0, result.output
    else:
        folder = settings.state_dir / "daemon"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "install.json").write_text(json.dumps({"interval_minutes": 15}), encoding="utf-8")
        result = runner.invoke(build(kernel), ["system", "daemon", "tick", "--json"])
        assert result.exit_code == 1, result.output
    report = json.loads(result.output)
    job_id = f"heinzel:check-in-{session.lower()}"
    check_in = next(item for item in report["runs"] if item["job"] == job_id)
    assert check_in["disposition"] == "failed"
    assert "current cadence" in check_in["error"]
    assert kernel.resolve(Notifications).recent() == []
