"""Approved generic scheduling survives actual fresh processes with fake effects."""

import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from heinzel import ConnectedItemIdentity, Notifications
from tests.integration.chat_scheduled_item_journey_test import (
    _chat_proposal,
    _confirm_contract,
    _propose_schedule,
    _write_model,
)
from tests.integration.scheduled_connected_read_test import (
    _approve_read,
    _confirm_item_schedule,
    _fixture,
)

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.timeout(150)  # four fresh worker processes, each importing the whole application
def test_approved_schedule_survives_process_exit_and_revocation(make_kernel, tmp_path, monkeypatch, settings):  # noqa: PLR0915
    import heinzel.kernel._ai as ai_module
    import heinzel.kernel._session_store as store_module
    import heinzel.modules.heinzel._autonomous_schedule as schedule_module
    import heinzel.modules.heinzel._interactive_turn as interactive_module
    import heinzel.modules.heinzel._service as service_module
    import heinzel.modules.heinzel._session_crud as crud_module
    import heinzel.modules.heinzel._session_repository as repository_module

    now = datetime(2026, 5, 4, 12, 2, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz is not None else now.replace(tzinfo=None)

    for module in (
        ai_module,
        store_module,
        schedule_module,
        interactive_module,
        service_module,
        crud_module,
        repository_module,
    ):
        monkeypatch.setattr(module, "datetime", Clock)
    local_settings = settings.model_copy(
        update={
            "data_dir": tmp_path / "data",
            "state_dir": tmp_path / "state",
            "cache_dir": tmp_path / "cache",
            "log_dir": tmp_path / "log",
        }
    )

    def local_kernel(**kwargs):
        return make_kernel(settings_=local_settings, **kwargs)

    client, kernel, current, session, state = _fixture(local_kernel, tmp_path, monkeypatch)
    state["item_identity"] = ConnectedItemIdentity(collection=("events",), identity_field="id", revision_field="rev")
    state["events"] = [{"id": "provider-item", "rev": "one"}]
    executable = tmp_path / "fake-claude"
    assert executable.exists()
    assert executable.resolve().is_relative_to(tmp_path.resolve())
    assert not executable.samefile(sys.executable)
    assert not executable.samefile(sys._base_executable)
    _write_model(executable, tmp_path / "journey-calls")
    proposal = _chat_proposal(client, session)
    _confirm_contract(client, session, proposal)
    _approve_read(client, kernel, current, session, state, rolling=True)
    schedule = _propose_schedule(client, session, proposal)
    assert state["reads"] == 0
    assert not (tmp_path / "notices.jsonl").exists()

    _confirm_item_schedule(client, session, schedule)
    (tmp_path / "block-notice").touch()
    env = {
        "HOME": str(tmp_path),
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": f"{ROOT / 'src'}:{ROOT}",
        "HZ_SYSTEM_RUNNER__CLAUDE_EXECUTABLE": str(executable),
        "HZ_CAPABILITIES__AI_ACCESS": "claude-code",
        "HZ_CAPABILITIES__AGENT_RUNNER": "claude-code",
    }

    def worker(at):
        return subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "tests/integration/crash_schedule_worker.py"),
                str(tmp_path),
                session,
                at.isoformat(),
            ],
            env=env,
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def wait_for(path):
        import time

        deadline = time.monotonic() + 20
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert path.exists(), f"missing {path.name}"

    original = worker(now)
    owned = [original]
    try:
        wait_for(tmp_path / "notice-blocked")
        from heinzel.modules.heinzel._session_crud import SessionCrud
        from heinzel.modules.heinzel._user_chat import UserChatService

        crud = kernel.resolve(SessionCrud)
        with current.local("cli"):
            events = crud.repository.control_read(session).events
            attempted = [e for e in events if e.kind == "scheduled-notification.attempted"]
            assert len(attempted) == 1
            occurrence = next(
                e.data["message_id"]
                for e in events
                if e.kind == "message.accepted" and e.data.get("origin") == "daemon"
            )
        original.kill()
        original.wait(timeout=5)
        (tmp_path / "block-notice").unlink()
        # Without an owner resolution the new slot must not issue an effect.
        later = worker(now + timedelta(minutes=5))
        owned.append(later)
        assert later.wait(timeout=30) == 0
        assert not (tmp_path / "notices.jsonl").exists()
        assert not (tmp_path / "effect-written").exists()
        with current.local("cli"):
            before_resolution = crud.repository.control_read(session).events
            assert (
                len([e for e in before_resolution if e.kind == "message.accepted" and e.data.get("origin") == "daemon"])
                == 1
            )
        with current.local("cli"):
            kernel.resolve(UserChatService).abandon(session, occurrence)
        after_abandon = worker(now + timedelta(minutes=10))
        owned.append(after_abandon)
        assert after_abandon.wait(timeout=30) == 0
        with current.local("cli"):
            after_turn_abandon = crud.repository.control_read(session).events
            assert (
                len(
                    [e for e in after_turn_abandon if e.kind == "message.accepted" and e.data.get("origin") == "daemon"]
                )
                == 1
            )
            from heinzel.modules.heinzel._scheduled_notifications import ScheduledNotificationRecovery

            class UnknownNotice(Notifications):
                def has_once(self, _key):
                    return False

            recovery = ScheduledNotificationRecovery(crud, current, UnknownNotice())
            assert recovery.resolve(session, attempted[0].key, acknowledge_uncertain=True).status == "abandoned"
        after_ack = worker(now + timedelta(minutes=15))
        owned.append(after_ack)
        assert after_ack.wait(timeout=30) == 0
        assert not (tmp_path / "effect-written").exists()
        (tmp_path / "release-effect").touch()
        wait_for(tmp_path / "effect-written")
        with current.local("cli"):
            events = crud.repository.control_read(session).events
            assert any(e.kind == "interactive.abandoned" for e in events)
            scheduled = [e for e in events if e.kind == "message.accepted" and e.data.get("origin") == "daemon"]
            assert len(scheduled) >= 2
    finally:
        (tmp_path / "release-effect").touch(exist_ok=True)
        for child in owned:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
