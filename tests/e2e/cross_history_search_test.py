"""Owner-private indexed search navigates to actual journal messages in the mounted UI."""

import os
import sys
import threading
from pathlib import Path
from typing import ClassVar, override
from urllib.parse import parse_qs, urlsplit

import pytest
import uvicorn
from fastapi.testclient import TestClient

from heinzel import CallIdentity, CurrentUser, HeinzelError, Notifications, Principal
from heinzel.heads import web
from heinzel.modules.heinzel._history_fts import indexed_search
from heinzel.modules.heinzel._history_search import HistorySearchRequest
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import MessageRequest
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository
from tests.e2e.docs_test import GuardedCommand
from tests.e2e.durable_five_minute_journey_test import BROWSER_CACHE, web_build

pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parents[2]


class NoNotices(Notifications):
    @override
    def recent(self, limit=50):
        return []

    @override
    def due(self, now=None):
        return []

    @override
    def watched(self):
        pass


def _fixture(make_kernel, tmp_path):
    kernel = make_kernel(builtins=True)
    kernel.bind(CurrentUser, CurrentUser(tmp_path / "identity"))
    kernel.bind(Notifications, NoNotices())
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    with current.local("cli"):
        session = crud.new_chat("history").session_id
        target = crud.new_chat("target").session_id
        for number in range(160):
            text = (
                "Find this distinctive phrase"
                if number == 123
                else "Expiring distinct result"
                if number == 124
                else f"Ordinary private message {number}"
            )
            crud.accept(session, MessageRequest(request_id=f"m{number}", text=text))
            text = "Find the second rare phrase" if number == 130 else f"Other private message {number}"
            crud.accept(target, MessageRequest(request_id=f"target-{number}", text=text))
        assert len(crud.snapshot(session).messages) == 50
        assert crud.snapshot(session).messages[0].id == "m0"
        assert [item.id for item in crud.recent_history(session)] == [f"m{n}" for n in range(140, 160)]
        match = indexed_search(crud, HistorySearchRequest(session_id=session, query="distinctive phrase"))
        assert [item.id for item in match.items] == ["m123"]
        jump = match.jump_cursors[0]
        assert crud.snapshot(session, cursor=jump).messages[0].id == "m123"
        with pytest.raises(HeinzelError):
            crud.snapshot(target, cursor=jump)
        foreign_identity = CallIdentity(
            Principal("u-" + "f" * 32), "cli", "local", "fixture", frozenset({"private:read"})
        )
        with current.using(foreign_identity), pytest.raises(HeinzelError):
            crud.snapshot(session, cursor=jump)
    foreign = CurrentUser(tmp_path / "foreign" / "identity")
    with foreign.local("cli"), pytest.raises(HeinzelError):
        SessionCrud(PersonalSessionRepository(foreign), crud.costs).snapshot(session, cursor=jump)
    with TestClient(
        web.build(kernel, port=None, token="fixture-token", static=None), base_url="http://localhost"
    ) as client:
        assert (
            client.post(
                "/api/heinzel/indexed-conversation-search",
                json={"request": {"session_id": session, "query": "distinctive"}},
            ).status_code
            == 401
        )

    return kernel, current, crud, session, target


def _expire_fixture_body(current, crud, session, tmp_path, stop):
    while not stop.wait(0.05):
        if not (tmp_path / "expire.request").exists():
            continue
        with current.local("cli"):
            event = next(
                e
                for e in crud.repository.control_read(session).events
                if e.kind == "message.accepted" and e.data.get("message_id") == "m124"
            )
            body = (
                current.directory.parent
                / "heinzel-sessions"
                / crud.repository.principal_id
                / "control"
                / session
                / "turns"
                / f"{event.data['body_id']}.jsonl"
            )
            assert body.resolve(strict=True).is_relative_to(tmp_path.resolve())
            assert not body.is_symlink()
            body.unlink()
        (tmp_path / "expire.done").write_text("ok")
        return


@pytest.mark.timeout(600)
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_mounted_cross_agent_search_opens_correct_journal_message(make_kernel, tmp_path, monkeypatch, engine):
    web_build.ensure_built()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(BROWSER_CACHE))
    kernel, current, crud, session, target = _fixture(make_kernel, tmp_path)

    class RecordedServer(uvicorn.Server):
        instances: ClassVar[list[uvicorn.Server]] = []

        def __init__(self, config):
            super().__init__(config)
            self.instances.append(self)

    monkeypatch.setattr(uvicorn, "Server", RecordedServer)
    ready, urls = threading.Event(), []
    stop = threading.Event()
    controller = threading.Thread(
        target=_expire_fixture_body, args=(current, crud, session, tmp_path, stop), daemon=True
    )

    def announce(value):
        urls.append(value)
        ready.set()

    server = threading.Thread(target=lambda: web.serve(kernel, port=0, browser=False, announce=announce), daemon=True)
    server.start()
    controller.start()
    try:
        assert ready.wait(15)
        token = parse_qs(urlsplit(urls[0]).fragment).get("token", [""])[0]
        assert token
        command = [
            sys.executable,
            str(ROOT / "scripts/web_build.py"),
            "exec",
            "node",
            "e2e/cross-history-search.mjs",
            urls[0],
            session,
            target,
            engine,
            str(tmp_path),
        ]
        guarded = GuardedCommand(command, cwd=ROOT, env=os.environ.copy())
        try:
            returncode = guarded.wait(180)
        finally:
            guarded.stop()
        output = guarded.capture.text("stderr") + "\n" + guarded.capture.text("stdout")
        assert returncode == 0, output.replace(token, "<redacted-session>")
        with current.local("cli"):
            assert len([e for e in crud.repository.control_read(session).events if e.kind == "message.accepted"]) == 160
            assert crud.snapshot(session).messages[0].id == "m0"
    finally:
        stop.set()
        controller.join(15)
        assert not controller.is_alive()
        if RecordedServer.instances:
            RecordedServer.instances[0].should_exit = True
        server.join(15)
        assert not server.is_alive()


def test_history_fixture_rejects_wrong_owner_and_preserves_tail(make_kernel, tmp_path):
    _kernel, current, crud, session, _target = _fixture(make_kernel, tmp_path)
    with current.local("cli"):
        assert len(crud.repository.control_read(session).events) >= 160
        assert crud.recent_history(session)[0].id == "m140"
