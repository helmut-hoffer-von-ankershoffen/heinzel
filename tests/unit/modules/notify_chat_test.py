"""Notices also in chat (ADR 0160): sent once, best effort, never at the cost of the notice itself."""

import json
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from heinzel import HeinzelError, NoticeHelper, NoticeKind, SelfMessage
from heinzel.kernel._self_chat import SelfChat
from heinzel.modules.system.notify import NotifyService, NotifySettings
from heinzel.modules.system.notify._chat import WINDOW, Ledger, chat_context, chat_text
from heinzel.modules.system.notify._native import Native

pytestmark = pytest.mark.unit

BERLIN = ZoneInfo("Europe/Berlin")
ORG = {"working_hours": "09-18", "timezone": "Europe/Berlin"}


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 21, 12, 0, tzinfo=BERLIN)

    def __call__(self) -> datetime:
        return self.now


class Inbox:
    """The user's own direct messages, as a chat adapter that can only message oneself sees them."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[dict[str, str | None]] = []
        self.lock = threading.Lock()

    def notify_self(self, text: str, *, thread: str | None = None, context: str | None = None) -> SelfMessage:
        if self.fail:
            msg = "the company CLI is down"
            raise HeinzelError(msg)
        with self.lock:
            self.sent.append({"text": text, "thread": thread, "context": context})
            number = len(self.sent)
        return SelfMessage(id=f"m{number}", thread=thread or f"t{number}")


def _channel(inbox: Inbox, *, notifies: bool = True) -> SelfChat:
    return SelfChat(adapter="acmechat", notifies=notifies, asks=False, build=lambda: inbox)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def make(settings, clock):
    def build(inbox: Inbox | None, **values: object) -> NotifyService:
        service = NotifyService(
            settings,
            NotifySettings.model_validate(values),
            ORG,
            clock=clock,
            notifier=Native(None, None, detail="none"),
            ui_url=lambda route: f"http://127.0.0.1:8737{route}",
        )
        service.self_chat = (lambda: _channel(inbox)) if inbox is not None else (lambda: None)
        return service

    return build


def _emit(service: NotifyService, key: str = "draft:1", **values: object):
    return service.emit(
        NoticeKind.DRAFT,
        "Draft ready: Weekly",
        "Read it.",
        route="/contracts/me%3Aweekly",
        command="hz heinzel drafts --latest",
        key=key,
        **values,
    )


def _wait_for(predicate, seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() > deadline:
            msg = "timed out"
            raise AssertionError(msg)
        time.sleep(0.01)


def test_off_by_default_nothing_goes_to_chat(make):
    inbox = Inbox()
    service = make(inbox)
    assert _emit(service) is not None
    assert service.post_chat() == 0
    assert inbox.sent == []


def test_switched_on_a_notice_also_goes_to_chat_once(make):
    inbox = Inbox()
    service = make(inbox, chat_notifications=True)
    notice = _emit(service)
    assert notice is not None
    _wait_for(lambda: len(inbox.sent) == 1)
    assert service.post_chat() == 0, "never twice"
    assert inbox.sent[0]["context"] == "Draft"
    assert inbox.sent[0]["text"] == (
        "*Draft ready: Weekly*\nRead it.\nOpen in Heinzel: "
        f"http://127.0.0.1:8737/contracts/me%3Aweekly?event={notice.id}"
    )
    assert service.recent() == [notice], "the app keeps it as before"


def test_a_failing_chat_never_blocks_or_drops_the_notice(make, caplog):
    service = make(Inbox(fail=True), chat_notifications=True)
    notice = _emit(service)
    assert notice is not None
    assert service.recent() == [notice]
    assert service.post_chat() == 0
    assert service.due() == [notice], "it still pops up in the app or natively"


def test_a_self_chat_that_cannot_be_built_costs_nothing(make):
    service = make(None, chat_notifications=True)

    def broken() -> SelfChat:
        msg = "no adapter"
        raise HeinzelError(msg)

    service.self_chat = broken
    assert _emit(service) is not None
    assert service.post_chat() == 0


def test_an_adapter_that_cannot_notify_is_skipped(make):
    inbox = Inbox()
    service = make(inbox, chat_notifications=True)
    service.self_chat = lambda: _channel(inbox, notifies=False)
    _emit(service)
    assert service.post_chat() == 0
    assert inbox.sent == []


def test_held_quiet_and_old_notices_stay_out_of_chat(make, clock):
    inbox = Inbox()
    service = make(inbox, chat_notifications=True, quiet=True)
    _emit(service, key="quiet")
    assert service.post_chat() == 0, "quiet mode: nothing pops up, nothing goes to chat"
    service = make(inbox, chat_notifications=True)
    service.settings = service.settings.model_copy(update={"chat_notifications": False})
    _emit(service, key="before")
    clock.now += WINDOW + timedelta(minutes=1)
    service.settings = service.settings.model_copy(update={"chat_notifications": True})
    assert service.post_chat() == 0, "switching on doesn't replay a backlog"
    assert inbox.sent == []


def test_a_heinzels_notices_share_one_thread_and_name_it(make, clock):
    inbox = Inbox()
    service = make(inbox, chat_notifications=True)
    service.settings = service.settings.model_copy(update={"chat_notifications": False})
    helper = NoticeHelper(name="Ada", reason="scheduled check-in")
    for key in ("one", "two"):
        service.emit(
            NoticeKind.HEINZEL, "Ada checked in", "", route="/heinzel?chat=hz-1", command="hz", key=key, helper=helper
        )
        clock.now += timedelta(seconds=1)
    service.settings = service.settings.model_copy(update={"chat_notifications": True})
    assert service.post_chat() == 2
    assert [m["thread"] for m in inbox.sent] == [None, "t1"]
    assert {m["context"] for m in inbox.sent} == {"Ada"}


def test_the_daemon_tick_sends_too(make):
    inbox = Inbox()
    service = make(inbox)
    _emit(service)
    service.settings = service.settings.model_copy(update={"chat_notifications": True})
    service._refresh_settings = lambda: service.settings
    assert service.post_native() == 0
    assert len(inbox.sent) == 1


def test_two_senders_never_send_one_notice_twice(make):
    inbox = Inbox()
    service = make(inbox)
    _emit(service)
    service.settings = service.settings.model_copy(update={"chat_notifications": True})
    threads = [threading.Thread(target=service.post_chat) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(inbox.sent) == 1


def test_the_ledger_is_private_bounded_and_survives_damage(settings):
    ledger = Ledger(settings.state_dir / "self-chat")
    with ledger.locked() as state:
        state["sent"].update({str(n): "x" for n in range(600)})
    data = json.loads((ledger.directory / "notices.json").read_text())
    assert len(data["sent"]) == 500
    assert (ledger.directory / "notices.json").stat().st_mode & 0o777 == 0o600
    (ledger.directory / "notices.json").write_text("[not a ledger")
    with ledger.locked() as state:
        assert state == {"sent": {}, "threads": {}}


def test_text_without_a_url_names_the_command(make):
    service = make(None)
    notice = _emit(service)
    assert notice is not None
    assert chat_text(notice, None).endswith("In a terminal: `hz heinzel drafts --latest`")
    assert chat_context(notice) == "Draft"


def test_chat_status_names_the_connector_and_what_it_can_carry(make):
    inbox = Inbox()
    service = make(inbox, chat_notifications=True)
    service._refresh_settings = lambda: service.settings
    status = service.chat_status()
    assert status.adapter == "acmechat"
    assert (status.notifications.supported, status.notifications.on) == (True, True)
    assert (status.questions.supported, status.questions.on) == (False, False)
    assert status.wait_minutes == 5
    assert status.detail == "acmechat can carry notifications."
    service.self_chat = lambda: None
    status = service.chat_status()
    assert status.adapter is None
    assert "heinzel-slack" in status.detail

    def broken() -> SelfChat:
        msg = "The chat connector can't start."
        raise HeinzelError(msg)

    service.self_chat = broken
    assert service.chat_status().detail == "The chat connector can't start."
