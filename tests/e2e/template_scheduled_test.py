"""Mounted owner reviews precede a fake-source scheduled model turn."""

import json
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, override
from urllib.parse import parse_qs, urlsplit

import pytest
import uvicorn

from heinzel import ChatDraftTemplate, ConnectedItemIdentity, HeinzelError, Module, Notifications, PlaybookLet
from heinzel.heads import web
from tests.e2e._browser_result import check_browser, require_browser, run_browser
from tests.integration.chat_template_scheduled_journey_test import Notices, _assert_ticks, _write_model
from tests.integration.scheduled_connected_read_test import _fixture

pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parents[2]


class MountedNotices(Notices):
    """An empty in-memory notice stream; recorded attempts stay in Notices.calls."""

    @override
    def recent(self, limit=50):
        return []

    @override
    def due(self, now=None):
        return []

    @override
    def watched(self):
        pass


def _fixed_clock(monkeypatch):
    import heinzel.kernel._ai as ai_module
    import heinzel.kernel._session_store as session_store
    import heinzel.modules.heinzel._autonomous_schedule as schedule_module
    import heinzel.modules.heinzel._interactive_turn as interactive_turn
    import heinzel.modules.heinzel._service as service_module
    import heinzel.modules.heinzel._session_crud as session_crud
    import heinzel.modules.heinzel._session_repository as session_repository

    now = [datetime(2026, 5, 4, 12, 2, tzinfo=UTC)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now[0] if tz is not None else now[0].replace(tzinfo=None)

    for module in (
        ai_module,
        session_store,
        schedule_module,
        interactive_turn,
        service_module,
        session_crud,
        session_repository,
    ):
        monkeypatch.setattr(module, "datetime", Clock)
    return now


def _run_browser(url, tmp_path):
    token = parse_qs(urlsplit(url).fragment).get("token", [""])[0]
    assert token
    result = require_browser(
        run_browser(
            [
                sys.executable,
                str(ROOT / "scripts/web_build.py"),
                "exec",
                "node",
                "e2e/template-scheduled.mjs",
                url,
                str(tmp_path),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
    )
    check_browser(result.returncode, (result.stderr + "\n" + result.stdout).replace(token, "<redacted-session>"))
    assert json.loads(result.stdout.strip().splitlines()[-1])["completed"] is True


def _draft_fixture(make_kernel, tmp_path, monkeypatch):
    import hashlib

    source = "Ask what source and result the person wants before proposing a draft."
    draft = Module("draftfixture", "Inert generic draft")
    draft.add(
        PlaybookLet(name="draftfixture", summary="Fixture", chat_drafts=(ChatDraftTemplate(name="draft", text=source),))
    )

    def with_draft(**kwargs):
        return make_kernel(**kwargs, draftfixture=draft)

    client, kernel, current, _unused_session, state = _fixture(with_draft, tmp_path, monkeypatch)
    state["item_identity"] = ConnectedItemIdentity(collection=("events",), identity_field="id", revision_field="rev")
    state["events"] = [{"id": "provider-item", "rev": "one"}]
    notices = MountedNotices()
    kernel.bind(Notifications, notices)
    executable = tmp_path / "fake-claude"
    assert executable.exists()
    assert executable.resolve().is_relative_to(tmp_path.resolve())
    assert not executable.samefile(sys.executable)
    assert not executable.samefile(sys._base_executable)
    _write_model(executable, tmp_path / "journey-calls", hashlib.sha256(source.encode()).hexdigest())

    return client, kernel, current, state, notices


def _wake(tmp_path, stop, finished, context):
    try:
        deadline = time.monotonic() + 120
        for name in ("preconfirm.json", "wake.json"):
            marker = tmp_path / name
            while not marker.exists() and time.monotonic() < deadline and not stop.wait(0.05):
                pass
            if stop.is_set():
                return
            assert marker.is_file()
            if name == "preconfirm.json":
                assert context[4]["reads"] == 0
                assert context[5].calls == []
                temporary = tmp_path / "preconfirm-result.tmp"
                temporary.write_text(json.dumps({"zero_effects": True}))
                temporary.replace(tmp_path / "preconfirm-result.json")
        session = json.loads(marker.read_text())["session"]
        first_turn = _assert_ticks((*context[:3], session, *context[4:]))
        temporary = tmp_path / "wake-result.tmp"
        temporary.write_text(json.dumps({"ok": True, **first_turn}))
        temporary.replace(tmp_path / "wake-result.json")
    except (AssertionError, OSError, ValueError, HeinzelError) as exc:
        temporary = tmp_path / "wake-result.tmp"
        temporary.write_text(json.dumps({"ok": False, "failure": type(exc).__name__}))
        temporary.replace(tmp_path / "wake-result.json")
    finally:
        finished.set()


@pytest.mark.timeout(600)
def test_mounted_template_approvals_precede_fake_scheduled_turn(make_kernel, tmp_path, monkeypatch, settings):
    now = _fixed_clock(monkeypatch)
    client, kernel, current, state, notices = _draft_fixture(make_kernel, tmp_path, monkeypatch)

    class RecordedServer(uvicorn.Server):
        instances: ClassVar[list[uvicorn.Server]] = []

        def __init__(self, config):
            super().__init__(config)
            self.instances.append(self)

    monkeypatch.setattr(uvicorn, "Server", RecordedServer)
    ready, finished, stop = threading.Event(), threading.Event(), threading.Event()
    urls = []

    def announce(value):
        urls.append(value)
        ready.set()

    server = threading.Thread(target=lambda: web.serve(kernel, port=0, browser=False, announce=announce), daemon=True)
    server.start()

    context = (client, kernel, current, None, state, notices, settings, monkeypatch, now)
    worker = threading.Thread(target=_wake, args=(tmp_path, stop, finished, context), daemon=True)
    worker.start()
    try:
        assert ready.wait(15)
        _run_browser(urls[0], tmp_path)
        assert finished.wait(15)
        assert json.loads((tmp_path / "wake-result.json").read_text())["ok"] is True
        assert state["reads"] == 2
        assert len(notices.calls) == 1
    finally:
        if RecordedServer.instances:
            RecordedServer.instances[0].should_exit = True
        server.join(15)
        assert not server.is_alive()
        stop.set()
        worker.join(15)
        assert not worker.is_alive()
