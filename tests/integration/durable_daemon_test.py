"""Personal check-ins through the CLI and the daemon's real threaded dispatcher."""

import hashlib
import json
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from heinzel import CurrentUser, HeinzelError, NoticeKind, Notifications
from heinzel.heads.cli import build
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.system.daemon import DaemonService, DaemonSettings
from heinzel.modules.system.daemon._ledger import Ledger

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def no_live_notifications(monkeypatch):
    """Keep these lifecycle regressions in the durable store; never invoke the host desktop notifier."""
    from heinzel.modules.system.notify._service import NotifyService

    monkeypatch.setattr(NotifyService, "post_native", lambda _self: 0)


def approve_schedule(call, session, schedule, request_id, action="activate"):
    """Stage and explicitly confirm an exact schedule preview through chat's human boundary."""
    staged = call(
        "chat",
        "propose-schedule-control",
        session,
        request_id,
        str(schedule["revision"]),
        action,
        str(schedule["contract_version"]),
        schedule["preview_hash"],
    )
    pending = next(item for item in staged["actions"] if item["arguments"].get("request_id") == request_id)
    return call("chat", "confirm", staged["id"], pending["id"])


def legacy_check_in(crud, session, notifications, slot, *, recorded, cleanup=True):  # noqa: PLR0913
    """Simulate the prior append-only writer while holding the same owner control lock."""
    with crud.repository.current.local("daemon"):
        notice_key = f"heinzel:{crud.repository.principal_id}:{session}:1:check-in:{slot.isoformat()}"
        outbox_key = hashlib.sha256(json.dumps({"notice_key": notice_key}, sort_keys=True).encode()).hexdigest()
        payload = {
            "slot": slot.isoformat(),
            "contract_version": 1,
            "notice_key": notice_key,
            "dedup_version": 1,
            "title": "Legacy checked in",
            "body": "Your scheduled five-minute check-in is due.",
            "route": f"/heinzel?chat={session}",
            "command": "hz heinzel sessions",
        }
        with crud.repository.control_writer(session) as writer:
            writer.append("check-in.intent", key=outbox_key, data=payload)
        assert notifications.emit_once(
            NoticeKind.HEINZEL,
            payload["title"],
            payload["body"],
            route=payload["route"],
            command=payload["command"],
            key=notice_key,
        )
        if recorded:
            with crud.repository.control_writer(session) as writer:
                writer.append("check-in.recorded", key=outbox_key, data={"notice_key": notice_key})
            if cleanup:
                notifications.forget_completed({notice_key})


def kill_after_legacy_receipt_compaction(state_dir, session_id, contract_version, revision, slot):
    """Run a real child dispatcher and die after cleanup, while the owner lock is still held."""
    from heinzel import CurrentUser, Settings
    from heinzel.modules.heinzel._session_models import SessionCosts
    from heinzel.modules.heinzel._session_repository import PersonalSessionRepository
    from heinzel.modules.system.notify._models import NotifySettings
    from heinzel.modules.system.notify._service import NotifyService

    settings = Settings(data_dir=Path(state_dir).parent, state_dir=Path(state_dir))
    current = CurrentUser(Path(state_dir) / "identity")
    repository = PersonalSessionRepository(current)
    crud = SessionCrud(repository, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
    notifications = NotifyService(settings, NotifySettings(), {})
    original_forget = notifications.forget_completed

    def compact_then_die(keys):
        if not original_forget(keys):
            os._exit(74)
        os._exit(73)

    notifications.forget_completed = compact_then_die
    with current.local("daemon"):
        crud.emit_check_in(session_id, contract_version, revision, slot, notifications)


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
    proposed = call(kernel, "heinzel", "propose-check-in", session, "proposal")
    approve_schedule(lambda *args: call(kernel, *args), session, proposed["schedule"], "daemon-activate")
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
    notifications = kernel.resolve(Notifications)
    notices = notifications.recent()
    assert len(notices) == 1
    assert notices[0].key.endswith("2026-10-25T12:05:00+00:00")
    original_key = notices[0].key
    if evicted:
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


def test_check_in_payload_survives_rename_during_replay_and_next_slot_uses_new_name(make_kernel, settings, monkeypatch):
    runner = CliRunner()
    time = [datetime(2026, 10, 25, 12, 6, tzinfo=UTC)]

    def make():
        kernel = make_kernel(builtins=True)
        crud = kernel.resolve(SessionCrud)
        crud.clock = lambda: time[0].replace(minute=4)
        crud.repository.clock = lambda: time[0].replace(minute=4)
        kernel.bind(DaemonService, DaemonService(kernel, settings, DaemonSettings(), clock=lambda: time[0], zone=UTC))
        return kernel

    kernel = make()

    def call(target, *args):
        result = runner.invoke(build(target), [*args, "--json"])
        assert result.exit_code == 0, result.output
        return json.loads(result.output)

    session = call(kernel, "heinzel", "new-chat", "create", "--name", "Original")["session_id"]
    proposed = call(kernel, "heinzel", "propose-check-in", session, "rename-proposal")
    approve_schedule(lambda *args: call(kernel, *args), session, proposed["schedule"], "rename-activate")
    notices = kernel.resolve(Notifications)
    real_emit_once = notices.emit_once
    attempts = []

    def fail_once(*args, **kwargs):
        attempts.append(kwargs["key"])
        return False

    monkeypatch.setattr(notices, "emit_once", fail_once)
    job_id = f"heinzel:check-in-{session.lower()}"
    original_ledger_append = Ledger.append

    def interrupt_failure_receipt(self, **kwargs):
        if kwargs["job"] == job_id:
            msg = "stop before recording the failed slot"
            raise OSError(msg)
        return original_ledger_append(self, **kwargs)

    monkeypatch.setattr(Ledger, "append", interrupt_failure_receipt)
    failed = runner.invoke(build(kernel), ["system", "daemon", "tick", "--json"])
    assert failed.exit_code == 1, failed.output
    assert len(attempts) == 2
    assert attempts[0] == attempts[1]
    monkeypatch.setattr(Ledger, "append", original_ledger_append)
    (settings.state_dir / "daemon" / "heartbeat.json").unlink()

    crud = kernel.resolve(SessionCrud)
    with kernel.resolve(CurrentUser).local("cli"):
        crud.profile(session, "rename-profile", 1, name="Renamed")
    monkeypatch.setattr(notices, "emit_once", real_emit_once)
    replay = runner.invoke(build(make()), ["system", "daemon", "tick", "--json"])
    assert replay.exit_code == 0, replay.output
    replayed = kernel.resolve(Notifications).recent(200)
    assert len(replayed) == 1, replay.output
    assert replayed[0].title == "Original checked in"

    time[0] = time[0].replace(minute=11)
    (settings.state_dir / "daemon" / "heartbeat.json").unlink()
    next_slot = runner.invoke(build(make()), ["system", "daemon", "tick", "--json"])
    assert next_slot.exit_code == 0, next_slot.output
    latest = kernel.resolve(Notifications).recent(200)
    assert len(latest) == 2
    assert latest[0].title == "Renamed checked in"


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
    proposed = call("heinzel", "propose-check-in", session, "proposal")
    approve_schedule(call, session, proposed["schedule"], "foreground-activate")
    notifications = kernel.resolve(Notifications)
    attempts = []

    def fail(*args, **kwargs):
        attempts.append(kwargs["key"])
        return False

    monkeypatch.setattr(notifications, "emit_once", fail)
    real = head.serve
    monkeypatch.setattr(head, "serve", lambda k, i, report: real(k, i, report, ticks=1))
    result = runner.invoke(build(kernel), ["system", "serve", "daemon"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    job_id = f"heinzel:check-in-{session.lower()}"
    run = next(item for item in report["runs"] if item["job"] == job_id)
    assert run["disposition"] == "failed"
    assert "could not be recorded" in run["error"]
    assert len(attempts) == 2
    assert attempts[0] == attempts[1]
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
    proposed = call("heinzel", "propose-check-in", session, "proposal")
    approve_schedule(call, session, proposed["schedule"], "tick-activate")
    if resume:
        time[0] = time[0].replace(minute=6)
        paused = call("heinzel", "schedule-control", session, "pause", "2", "pause")
        time[0] = time[0].replace(minute=12)
        approve_schedule(call, session, paused["schedule"], "tick-resume", "resume")
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
    proposed = call("heinzel", "propose-check-in", session, "proposal")
    approve_schedule(call, session, proposed["schedule"], "manual-wake-activate")
    report = call("system", "daemon", "tick", "--job", f"heinzel:check-in-{session.lower()}")
    assert report["runs"][0]["disposition"] == "skipped"
    assert "scheduled slots" in report["runs"][0]["error"]
    assert kernel.resolve(Notifications).recent() == []


@pytest.mark.parametrize("pruned", [False, True])
def test_outbox_recovers_or_fails_closed_after_notice_before_outbox_ack(make_kernel, settings, monkeypatch, pruned):  # noqa: PLR0915
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
    proposed = call(kernel, "heinzel", "propose-check-in", session, "proposal")
    approve_schedule(lambda *args: call(kernel, *args), session, proposed["schedule"], "outbox-activate")
    job_id = f"heinzel:check-in-{session.lower()}"
    original_replace = SessionWriter.replace_checkpoint
    original_receipt = Ledger.append

    def interrupt_outbox(writer, name, value):
        if name == "checkin-outbox" and value.get("cleanup_key") is not None:
            msg = "interrupted after notice"
            raise OSError(msg)
        return original_replace(writer, name, value)

    def interrupt_receipt(ledger, **kwargs):
        if kwargs["job"] == job_id:
            msg = "interrupted before receipt"
            raise OSError(msg)
        return original_receipt(ledger, **kwargs)

    monkeypatch.setattr(SessionWriter, "replace_checkpoint", interrupt_outbox)
    monkeypatch.setattr(Ledger, "append", interrupt_receipt)
    failed = runner.invoke(build(kernel), ["system", "daemon", "tick", "--json"])
    assert failed.exit_code == 1, failed.output
    notices = kernel.resolve(Notifications)
    first = notices.recent()[0]
    receipt_digest = hashlib.sha256(first.key.encode()).hexdigest()
    assert receipt_digest in notices.store._read_state(strict=True).durable_keys
    if pruned:
        for index in range(201):
            assert notices.emit(NoticeKind.TEST, "Other", route="/", command="hz", key=f"other-{index}")
        assert all(item.key != first.key for item in notices.recent(200))
    (settings.state_dir / "daemon" / "heartbeat.json").unlink()
    monkeypatch.setattr(SessionWriter, "replace_checkpoint", original_replace)
    monkeypatch.setattr(Ledger, "append", original_receipt)
    restarted = make()
    replay = runner.invoke(build(restarted), ["system", "daemon", "tick", "--json"])
    assert replay.exit_code == 0, replay.output
    assert receipt_digest not in restarted.resolve(Notifications).store._read_state(strict=True).durable_keys
    replayed = [item for item in restarted.resolve(Notifications).recent(200) if item.key == first.key]
    assert len(replayed) == (0 if pruned else 1)
    owner_root = kernel.resolve(CurrentUser).directory.parent / "heinzel-sessions"
    control = next(owner_root.glob(f"*/control/{session}"))
    control = control / "checkpoint-checkin-outbox.json"
    state = json.loads(control.read_text())
    assert state["pending"] is None
    assert state["last_completed"] == "2026-10-25T12:05:00Z"


def test_check_in_delivery_state_is_constant_size_over_many_slots(make_kernel, settings):
    from heinzel import SessionStore

    kernel = make_kernel(builtins=True)
    crud = kernel.resolve(SessionCrud)
    now = datetime(2026, 10, 25, 12, 4, tzinfo=UTC)
    crud.clock = lambda: now
    crud.repository.clock = lambda: now
    runner = CliRunner()

    def call(*args):
        result = runner.invoke(build(kernel), [*args, "--json"])
        assert result.exit_code == 0, result.output
        return json.loads(result.output)

    session = call("heinzel", "new-chat", "create")["session_id"]
    proposed = call("heinzel", "propose-check-in", session, "bounded-proposal")
    approve_schedule(call, session, proposed["schedule"], "bounded-activate")
    for index in range(100):
        slot = datetime(2026, 10, 25, 12, 5, tzinfo=UTC) + timedelta(minutes=5 * index)
        with kernel.resolve(CurrentUser).local("daemon"):
            assert crud.emit_check_in(session, 1, 2, slot, kernel.resolve(Notifications))["notice"]
    owner_root = kernel.resolve(CurrentUser).directory.parent / "heinzel-sessions"
    folder = next(owner_root.glob(f"*/control/{session}"))
    state = json.loads((folder / "checkpoint-checkin-outbox.json").read_text())
    assert state["pending"] is None
    assert state["cleanup_key"] is None
    assert state["last_completed"] == "2026-10-25T20:20:00Z"
    events = SessionStore(folder.parent).read(session).events
    assert not any(event.kind.startswith("check-in.") for event in events)
    assert len((folder / "session.jsonl").read_bytes()) < 32_000


def test_check_in_watermark_never_regresses_for_out_of_order_slots(make_kernel):
    kernel = make_kernel(builtins=True)
    crud = kernel.resolve(SessionCrud)
    runner = CliRunner()
    now = datetime(2026, 10, 25, 12, 4, tzinfo=UTC)
    crud.clock = lambda: now
    crud.repository.clock = lambda: now

    def call(*args):
        result = runner.invoke(build(kernel), [*args, "--json"])
        assert result.exit_code == 0, result.output
        return json.loads(result.output)

    session = call("heinzel", "new-chat", "create")["session_id"]
    proposed = call("heinzel", "propose-check-in", session, "order-proposal")
    approve_schedule(call, session, proposed["schedule"], "order-activate")
    notices = kernel.resolve(Notifications)
    newer = datetime(2026, 10, 25, 12, 10, tzinfo=UTC)
    older = datetime(2026, 10, 25, 12, 5, tzinfo=UTC)
    for slot in (newer, older):
        with kernel.resolve(CurrentUser).local("daemon"):
            assert crud.emit_check_in(session, 1, 2, slot, notices)["notice"]
    assert len(notices.recent()) == 1
    assert notices.recent()[0].key.endswith(newer.isoformat())


def test_concurrent_check_in_callers_share_one_durable_slot(make_kernel):
    kernel = make_kernel(builtins=True)
    crud = kernel.resolve(SessionCrud)
    runner = CliRunner()
    now = datetime(2026, 10, 25, 12, 4, tzinfo=UTC)
    crud.clock = lambda: now
    crud.repository.clock = lambda: now

    def call(*args):
        result = runner.invoke(build(kernel), [*args, "--json"])
        assert result.exit_code == 0, result.output
        return json.loads(result.output)

    session = call("heinzel", "new-chat", "create")["session_id"]
    proposed = call("heinzel", "propose-check-in", session, "concurrent-proposal")
    approve_schedule(call, session, proposed["schedule"], "concurrent-activate")
    notices = kernel.resolve(Notifications)
    slot = datetime(2026, 10, 25, 12, 5, tzinfo=UTC)

    def dispatch():
        with kernel.resolve(CurrentUser).local("daemon"):
            return crud.emit_check_in(session, 1, 2, slot, notices)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _index: dispatch(), range(16)))
    assert all(result["notice"] for result in results)
    assert len([notice for notice in notices.recent() if notice.key.endswith(slot.isoformat())]) == 1


def test_legacy_completed_receipts_are_compacted_as_a_batch_before_checkpoint(make_kernel, monkeypatch):
    from heinzel import SessionWriter

    kernel = make_kernel(builtins=True)
    crud = kernel.resolve(SessionCrud)
    runner = CliRunner()
    now = datetime(2026, 10, 25, 12, 16, tzinfo=UTC)
    crud.clock = lambda: now.replace(minute=4)
    crud.repository.clock = lambda: now.replace(minute=4)

    def call(*args):
        result = runner.invoke(build(kernel), [*args, "--json"])
        assert result.exit_code == 0, result.output
        return json.loads(result.output)

    session = call("heinzel", "new-chat", "create")["session_id"]
    proposed = call("heinzel", "propose-check-in", session, "legacy-batch-proposal")
    approve_schedule(call, session, proposed["schedule"], "legacy-batch-activate")
    notices = kernel.resolve(Notifications)
    first = datetime(2026, 10, 25, 12, 5, tzinfo=UTC)
    second = datetime(2026, 10, 25, 12, 10, tzinfo=UTC)
    legacy_check_in(crud, session, notices, first, recorded=True, cleanup=False)
    legacy_check_in(crud, session, notices, second, recorded=True, cleanup=False)
    old_keys = {notice.key for notice in notices.recent()}
    assert len(old_keys) == 2

    # Simulate crash after receipt removal but before the migration checkpoint commits.
    original_replace = SessionWriter.replace_checkpoint
    fired = False

    def fail_migrated_checkpoint(writer, name, value):
        nonlocal fired
        if name == "checkin-outbox" and value.get("journal_seq", 0) > 0 and not fired:
            fired = True
            msg = "crash after legacy receipt compaction"
            raise OSError(msg)
        return original_replace(writer, name, value)

    from heinzel import SessionWriter

    monkeypatch.setattr(SessionWriter, "replace_checkpoint", fail_migrated_checkpoint)
    with kernel.resolve(CurrentUser).local("daemon"):
        revision = crud._schedule_revision(crud.repository.control_read(session).events)
    identity = kernel.resolve(CurrentUser).local("daemon")
    with pytest.raises(OSError, match="crash after legacy receipt compaction"), identity:
        crud.emit_check_in(session, 1, revision, datetime(2026, 10, 25, 12, 15, tzinfo=UTC), notices)
    assert not (
        notices.store._read_state(strict=True).durable_keys
        & {hashlib.sha256(key.encode()).hexdigest() for key in old_keys}
    )
    monkeypatch.undo()
    with kernel.resolve(CurrentUser).local("daemon"):
        result = crud.emit_check_in(session, 1, revision, datetime(2026, 10, 25, 12, 15, tzinfo=UTC), notices)
    assert result["notice"]
    assert len([notice for notice in notices.recent() if notice.key in old_keys]) == 2
    durable_keys = notices.store._read_state(strict=True).durable_keys
    assert all(hashlib.sha256(key.encode()).hexdigest() not in durable_keys for key in old_keys)


def test_process_death_after_legacy_compaction_recovers_completed_and_unresolved_intents(  # noqa: PLR0915
    make_kernel, settings, monkeypatch
):
    from heinzel import SessionStore

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    kernel = make_kernel(builtins=True)
    crud = kernel.resolve(SessionCrud)
    now = datetime(2026, 10, 25, 12, 16, tzinfo=UTC)
    crud.clock = lambda: now.replace(minute=4)
    crud.repository.clock = lambda: now.replace(minute=4)
    runner = CliRunner()

    def call(*args):
        result = runner.invoke(build(kernel), [*args, "--json"])
        assert result.exit_code == 0, result.output
        return json.loads(result.output)

    session = call("heinzel", "new-chat", "create")["session_id"]
    proposed = call("heinzel", "propose-check-in", session, "process-legacy-proposal")
    approve_schedule(call, session, proposed["schedule"], "process-legacy-activate")
    notifications = kernel.resolve(Notifications)
    completed_slots = [
        datetime(2026, 10, 25, 12, 5, tzinfo=UTC),
        datetime(2026, 10, 25, 12, 10, tzinfo=UTC),
    ]
    unresolved_slot = datetime(2026, 10, 25, 12, 15, tzinfo=UTC)
    for slot in completed_slots:
        legacy_check_in(crud, session, notifications, slot, recorded=True, cleanup=False)
    legacy_check_in(crud, session, notifications, unresolved_slot, recorded=False)
    all_keys = {notice.key for notice in notifications.recent()}
    assert len(all_keys) == 3
    unresolved_key = next(key for key in all_keys if key.endswith(unresolved_slot.isoformat()))

    with kernel.resolve(CurrentUser).local("daemon"):
        revision = crud._schedule_revision(crud.repository.control_read(session).events)
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=kill_after_legacy_receipt_compaction,
        args=(str(settings.state_dir), session, 1, revision, unresolved_slot),
    )
    process.start()
    process.join(20)
    if process.is_alive():
        process.terminate()
        process.join(10)
        pytest.fail("child dispatcher did not reach the receipt cleanup boundary")
    assert process.exitcode == 73

    durable = notifications.store._read_state(strict=True).durable_keys
    completed_digests = {hashlib.sha256(key.encode()).hexdigest() for key in all_keys if key != unresolved_key}
    assert not durable.intersection(completed_digests)
    owner_root = kernel.resolve(CurrentUser).directory.parent / "heinzel-sessions"
    control = next(owner_root.glob(f"*/control/{session}"))
    # The child died before replacing the checkpoint; the legacy unresolved intent remains authoritative.
    checkpoint_path = control / "checkpoint-checkin-outbox.json"
    assert not checkpoint_path.exists()
    session_events = SessionStore(control.parent).read(session).events
    legacy_events = [event for event in session_events if event.kind.startswith("check-in.")]
    assert len([event for event in legacy_events if event.kind == "check-in.intent"]) == 3

    # Restart from a fresh kernel, reconcile the unresolved intent, and dispatch it once.
    restarted = make_kernel(builtins=True)
    restarted_crud = restarted.resolve(SessionCrud)
    restarted_crud.clock = lambda: now.replace(minute=4)
    restarted_crud.repository.clock = lambda: now.replace(minute=4)
    restarted_notifications = restarted.resolve(Notifications)
    with restarted.resolve(CurrentUser).local("daemon"):
        result = restarted_crud.emit_check_in(session, 1, revision, unresolved_slot, restarted_notifications)
    assert result["notice"]
    assert len([notice for notice in restarted_notifications.recent() if notice.key in all_keys]) == 3
    remaining = restarted_notifications.store._read_state(strict=True).durable_keys
    assert not remaining.intersection({hashlib.sha256(key.encode()).hexdigest() for key in all_keys})
    checkpoint = json.loads(checkpoint_path.read_text())
    assert checkpoint["last_completed"] == unresolved_slot.isoformat().replace("+00:00", "Z")


@pytest.mark.parametrize("has_record", [False, True])
def test_bounded_outbox_reconciles_prior_writer_intent_before_emitting(make_kernel, has_record):
    kernel = make_kernel(builtins=True)
    crud = kernel.resolve(SessionCrud)
    runner = CliRunner()
    now = datetime(2026, 10, 25, 12, 4, tzinfo=UTC)
    crud.clock = lambda: now
    crud.repository.clock = lambda: now

    def call(*args):
        result = runner.invoke(build(kernel), [*args, "--json"])
        assert result.exit_code == 0, result.output
        return json.loads(result.output)

    session = call("heinzel", "new-chat", "create")["session_id"]
    proposed = call("heinzel", "propose-check-in", session, "legacy-proposal")
    approve_schedule(call, session, proposed["schedule"], "legacy-activate")
    slot = datetime(2026, 10, 25, 12, 5, tzinfo=UTC)
    notices = kernel.resolve(Notifications)
    legacy_check_in(crud, session, notices, slot, recorded=has_record)
    with kernel.resolve(CurrentUser).local("daemon"):
        result = crud.emit_check_in(session, 1, 2, slot, notices)
    assert result["notice"]
    assert len([notice for notice in notices.recent() if notice.key.endswith(slot.isoformat())]) == 1


def test_bounded_outbox_reconciles_legacy_writer_after_checkpoint_creation(make_kernel):
    kernel = make_kernel(builtins=True)
    crud = kernel.resolve(SessionCrud)
    runner = CliRunner()
    now = datetime(2026, 10, 25, 12, 4, tzinfo=UTC)
    crud.clock = lambda: now
    crud.repository.clock = lambda: now

    def call(*args):
        result = runner.invoke(build(kernel), [*args, "--json"])
        assert result.exit_code == 0, result.output
        return json.loads(result.output)

    session = call("heinzel", "new-chat", "create")["session_id"]
    proposed = call("heinzel", "propose-check-in", session, "legacy-overlap-proposal")
    approve_schedule(call, session, proposed["schedule"], "legacy-overlap-activate")
    notices = kernel.resolve(Notifications)
    first = datetime(2026, 10, 25, 12, 5, tzinfo=UTC)
    later = datetime(2026, 10, 25, 12, 10, tzinfo=UTC)
    with kernel.resolve(CurrentUser).local("daemon"):
        crud.emit_check_in(session, 1, 2, first, notices)
    legacy_check_in(crud, session, notices, later, recorded=True)
    with kernel.resolve(CurrentUser).local("daemon"):
        crud.emit_check_in(session, 1, 2, later, notices)
    assert len(notices.recent()) == 2


@pytest.mark.parametrize("notice_was_recorded", [False, True])
def test_pause_resume_never_replays_a_stale_pending_check_in(make_kernel, monkeypatch, notice_was_recorded):
    from heinzel import SessionWriter

    kernel = make_kernel(builtins=True)
    crud = kernel.resolve(SessionCrud)
    now = [datetime(2026, 10, 25, 12, 4, tzinfo=UTC)]
    crud.clock = lambda: now[0]
    crud.repository.clock = lambda: now[0]
    notices = kernel.resolve(Notifications)
    runner = CliRunner()

    def call(*args):
        result = runner.invoke(build(kernel), [*args, "--json"])
        assert result.exit_code == 0, result.output
        return json.loads(result.output)

    session = call("heinzel", "new-chat", "create")["session_id"]
    proposed = call("heinzel", "propose-check-in", session, "pause-replay-proposal")
    approve_schedule(call, session, proposed["schedule"], "pause-replay-activate")
    old_slot = datetime(2026, 10, 25, 12, 5, tzinfo=UTC)
    real_emit_once = notices.emit_once
    real_replace = SessionWriter.replace_checkpoint

    if notice_was_recorded:

        def interrupt_after_notice(writer, name, value):
            if name == "checkin-outbox" and value.get("cleanup_key") is not None:
                msg = "crash after notice before checkpoint acknowledgement"
                raise OSError(msg)
            return real_replace(writer, name, value)

        monkeypatch.setattr(SessionWriter, "replace_checkpoint", interrupt_after_notice)
    else:
        monkeypatch.setattr(notices, "emit_once", lambda *_args, **_kwargs: False)

    now[0] = datetime(2026, 10, 25, 12, 6, tzinfo=UTC)
    with kernel.resolve(CurrentUser).local("daemon"), pytest.raises((HeinzelError, OSError)):
        crud.emit_check_in(session, 1, 2, old_slot, notices)

    monkeypatch.setattr(SessionWriter, "replace_checkpoint", real_replace)
    monkeypatch.setattr(notices, "emit_once", real_emit_once)
    pending_file = next(
        path
        for path in kernel.resolve(CurrentUser).directory.parent.glob(
            f"heinzel-sessions/*/control/{session}/checkpoint-checkin-outbox.json"
        )
    )
    assert json.loads(pending_file.read_text())["pending"]["slot"] == old_slot.isoformat().replace("+00:00", "Z")

    now[0] = datetime(2026, 10, 25, 12, 7, tzinfo=UTC)
    paused = call("heinzel", "schedule-control", session, "pause-before-replay", "2", "pause")
    now[0] = datetime(2026, 10, 25, 12, 9, tzinfo=UTC)
    approve_schedule(call, session, paused["schedule"], "resume-after-pending", "resume")
    revision = call("heinzel", "session", session)["schedule"]["revision"]
    new_slot = datetime(2026, 10, 25, 12, 10, tzinfo=UTC)
    with kernel.resolve(CurrentUser).local("daemon"):
        result = crud.emit_check_in(session, 1, revision, new_slot, notices)
    assert result["slot"].startswith("2026-10-25T12:10")
    old_notices = [notice for notice in notices.recent() if notice.key.endswith(old_slot.isoformat())]
    new_notices = [notice for notice in notices.recent() if notice.key.endswith(new_slot.isoformat())]
    assert len(old_notices) == int(notice_was_recorded)
    assert len(new_notices) == 1


def test_legacy_pending_slot_keeps_its_revision_across_clock_rollback_and_resume(make_kernel):
    from heinzel.modules.heinzel._session_crud import _digest

    kernel = make_kernel(builtins=True)
    crud = kernel.resolve(SessionCrud)
    now = [datetime(2026, 10, 25, 12, 4, tzinfo=UTC)]
    crud.clock = lambda: now[0]
    crud.repository.clock = lambda: now[0]
    runner = CliRunner()

    def call(*args):
        result = runner.invoke(build(kernel), [*args, "--json"])
        assert result.exit_code == 0, result.output
        return json.loads(result.output)

    session = call("heinzel", "new-chat", "create")["session_id"]
    proposed = call("heinzel", "propose-check-in", session, "legacy-clock-proposal")
    approve_schedule(call, session, proposed["schedule"], "legacy-clock-activate")
    old_slot = datetime(2026, 10, 25, 12, 25, tzinfo=UTC)
    now[0] = datetime(2026, 10, 25, 12, 30, tzinfo=UTC)
    with kernel.resolve(CurrentUser).local("daemon"):
        owner = crud.repository.principal_id
    notice_key = f"heinzel:{owner}:{session}:1:check-in:{old_slot.isoformat()}"
    outbox_key = _digest({"notice_key": notice_key})
    payload = {
        "slot": old_slot.isoformat(),
        "contract_version": 1,
        "notice_key": notice_key,
        "dedup_version": 1,
        "title": "Old name checked in",
        "body": "Your scheduled five-minute check-in is due.",
        "route": f"/heinzel?chat={session}",
        "command": "hz heinzel sessions",
    }
    with kernel.resolve(CurrentUser).local("daemon"), crud.repository.control_writer(session) as writer:
        writer.append("check-in.intent", key=outbox_key, data=payload)

    now[0] = datetime(2026, 10, 25, 12, 31, tzinfo=UTC)
    paused = call("heinzel", "schedule-control", session, "legacy-clock-pause", "2", "pause")
    with kernel.resolve(CurrentUser).local("cli"):
        crud.profile(session, "legacy-clock-rename", 1, name="Renamed")
    now[0] = datetime(2026, 10, 25, 12, 20, tzinfo=UTC)  # Wall clock moved back before the old intent slot.
    approve_schedule(call, session, paused["schedule"], "legacy-clock-resume", "resume")
    revision = call("heinzel", "session", session)["schedule"]["revision"]
    with kernel.resolve(CurrentUser).local("daemon"):
        crud.emit_check_in(session, 1, revision, old_slot, kernel.resolve(Notifications))
    matching = [notice for notice in kernel.resolve(Notifications).recent() if notice.key == notice_key]
    assert len(matching) == 1
    assert matching[0].title == "Renamed checked in"


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
    proposed = call("heinzel", "propose-check-in", session, "proposal")
    approve_schedule(call, session, proposed["schedule"], "slow-cadence-activate")
    activated = call("heinzel", "session", session)
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
