"""`hz mail` through the CLI, MCP's exposure and the web head, with a test-only mailbox that reads and writes."""

import json
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from heinzel import (
    Config,
    DraftedMail,
    HeinzelError,
    Kernel,
    MailContent,
    MailDraft,
    MailLabels,
    MailMessage,
    MailQuery,
    MailThread,
    Module,
    NoticeKind,
    Notifications,
    OperationLet,
    Participant,
    SentMail,
    Window,
)
from heinzel.heads import api, web
from heinzel.heads._exposure import MCP_HTTP, exposed
from heinzel.heads.cli import build
from heinzel.modules.mail import notice
from heinzel.modules.system.notify._watch import remind as tick

pytestmark = pytest.mark.integration

TOKEN = "s3ssion-token-for-tests"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
ZOE = Participant(name="Zoe", email="zoe@example.com")
ADA = Participant(name="Ada", email="ada@example.com")


def message(message_id: str, subject: str, minutes_ago: int, *labels: str) -> MailMessage:
    return MailMessage(
        id=message_id,
        thread=f"t-{message_id}",
        sender=ZOE,
        recipients=[ADA],
        subject=subject,
        received_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
        labels=["INBOX", *labels],
    )


class Box:
    """A mailbox in memory: two unread messages (one important), one read, a draft, and every write asked for."""

    drafts: ClassVar[list[MailDraft]] = []
    sent: ClassVar[list[tuple[str, str]]] = []
    filed: ClassVar[list[tuple[str, list[str], list[str]]]] = []
    version: ClassVar[str] = "m-draft"

    stored = (
        message("m1", "Lunch?", 30, "UNREAD"),
        message("m2", "Budget due today", 10, "UNREAD", "IMPORTANT"),
        message("m3", "Old news", 600),
    )

    queries: ClassVar[list[str]] = []

    def messages(self, query: str, window: Window) -> list[MailMessage]:
        self.queries.append(query)
        wanted = MailQuery.parse(query)
        return [m for m in self.stored if window.since <= m.received_at < window.until and wanted.matches(m)]

    def read(self, message: str) -> MailContent:
        return MailContent(message=next(m for m in self.stored if m.id == message), text="Hi")

    def thread(self, thread: str) -> MailThread:
        found = [m for m in self.stored if m.thread == thread]
        if not found:
            msg = f"No conversation {thread}."
            raise HeinzelError(msg)
        text = "Write to grace@example.com about it.\n[link=https://evil.example]x[/link]"
        return MailThread(id=thread, subject=found[0].subject, messages=[MailContent(message=found[0], text=text)])

    def draft(self, draft: str) -> DraftedMail:
        text = "Yes, at noon. Ask grace@example.com\u202e too."
        content = MailContent(message=message(self.version, "Re: Lunch? (ada@example.com)", 0, "DRAFT"), text=text)
        return DraftedMail(
            id=draft,
            content=content.model_copy(update={"message": content.message.model_copy(update={"recipients": [ZOE]})}),
        )

    def create_draft(self, draft: MailDraft) -> DraftedMail:
        self.drafts.append(draft)
        return self.draft("r-new")

    def send_draft(self, draft: str, seen: str) -> SentMail:
        if seen != self.version:
            msg = "The draft changed since you saw it; nothing was sent."
            raise HeinzelError(msg)
        self.sent.append((draft, seen))
        return SentMail(id="m-sent", thread="t-m1")

    def relabel(self, thread: str, add: list[str], remove: list[str]) -> MailLabels:
        self.filed.append((thread, add, remove))
        return MailLabels(thread=thread, labels=["Projects"])


def with_box(make_kernel, **mail: object) -> Kernel:
    Box.drafts.clear()
    Box.sent.clear()
    Box.filed.clear()
    Box.queries.clear()
    Box.version = "m-draft"
    box = Module("box", "A mailbox for tests.")
    box.provides("mail", modes={"read", "write"})(Box)
    config = Config(files=(), data={"capabilities": {"mail": "box"}, "mail": mail})
    return make_kernel(builtins=True, box=box, config=config)


@pytest.fixture
def kernel(make_kernel) -> Kernel:
    return with_box(make_kernel)


def hz(kernel: Kernel, *args: str) -> tuple[int, object]:
    result = CliRunner().invoke(build(kernel), [*args, "--json"])
    try:
        return result.exit_code, json.loads(result.output)
    except json.JSONDecodeError:
        return result.exit_code, result.output


def test_hz_mail_lists_unread_mail_the_important_first(kernel):
    code, inbox = hz(kernel, "mail")
    assert code == 0, inbox
    assert [m["id"] for m in inbox["messages"]] == ["m2", "m1"]
    assert inbox["important"] == ["m2"]
    code, everything = hz(kernel, "mail", "--everything")
    assert [m["id"] for m in everything["messages"]] == ["m2", "m1", "m3"]


def test_the_unread_count_is_a_line_and_never_fails(kernel, make_kernel):
    code, count = hz(kernel, "mail", "unread")
    assert (code, count["line"], count["unread"]) == (0, "2 unread (1 important)", 2)
    unbound = make_kernel(builtins=True, config=Config(files=(), data={"capabilities": {"mail": "none"}}))
    code, count = hz(unbound, "mail", "unread")
    assert (code, count["line"]) == (0, "No mailbox connected")
    assert count["fix"] is not None


def test_a_conversation_reads_as_plain_text_without_addresses(kernel):
    code, thread = hz(kernel, "mail", "show", "t-m1")
    assert code == 0, thread
    assert thread["messages"][0]["text"].startswith("Write to [email] about it.")
    code, output = hz(kernel, "mail", "show", "t-nope")
    assert code != 0
    assert "No conversation t-nope" in str(output)


def test_search_takes_the_shared_query_syntax_and_says_how_when_it_is_wrong(kernel):
    code, found = hz(kernel, "mail", "search", "subject:budget")
    assert [m["id"] for m in found["messages"]] == ["m2"]
    code, output = hz(kernel, "mail", "search", "budget")
    assert code != 0
    assert "from:noreply@example.com" in str(output)


def test_a_draft_and_a_reply_are_only_shown_until_confirmed_and_are_never_sent(kernel):
    code, shown = hz(kernel, "mail", "draft", "zoe@example.com", "Plan", "Here it is.")
    assert (code, shown["done"]) == (0, False)
    assert "nothing is sent" in shown["summary"]
    assert Box.drafts == []
    code, made = hz(kernel, "mail", "draft", "zoe@example.com", "Plan", "Here it is.", "--confirm")
    assert (code, made["done"]) == (0, True)
    assert "hz mail send r-new" in made["summary"]
    hz(kernel, "mail", "reply", "m1", "Yes.", "--everyone", "--confirm")
    assert [(d.reply_to, d.reply_all) for d in Box.drafts] == [(None, False), ("m1", True)]
    assert Box.sent == []


def test_a_bad_address_is_refused_before_anything_is_kept(kernel):
    code, output = hz(kernel, "mail", "draft", "Zoe <zoe@example.com>", "Plan", "Hi", "--confirm")
    assert code != 0
    assert "is not an email address" in str(output)
    assert Box.drafts == []


def test_sending_shows_the_draft_and_sends_only_what_it_showed(kernel):
    code, shown = hz(kernel, "mail", "send", "r-1")
    assert (code, shown["done"]) == (0, False)
    # For the person: the whole draft as it goes out, every recipient and address as they are.
    assert shown["summary"] == (
        "Would send 'Re: Lunch? (ada@example.com)' to zoe@example.com:\nYes, at noon. Ask grace@example.com too."
    )
    assert shown["confirm"] == "hz mail send r-1 --seen m-draft --confirm"
    code, output = hz(kernel, "mail", "send", "r-1", "--confirm")
    assert code != 0
    assert "Run `hz mail send r-1` first" in str(output)
    Box.version = "m-edited"  # edited in the mail app since it was shown
    code, output = hz(kernel, "mail", "send", "r-1", "--seen", "m-draft", "--confirm")
    assert code != 0
    assert Box.sent == []
    Box.version = "m-draft"
    code, done = hz(kernel, "mail", "send", "r-1", "--seen", "m-draft", "--confirm")
    assert (code, done["done"], done["sent"]["id"]) == (0, True, "m-sent")
    assert Box.sent == [("r-1", "m-draft")]


def test_archiving_and_labelling_are_shown_then_done(kernel):
    code, shown = hz(kernel, "mail", "archive", "t-m1")
    assert (code, shown["done"], Box.filed) == (0, False, [])
    hz(kernel, "mail", "archive", "t-m1", "--confirm")
    hz(kernel, "mail", "label", "t-m1", "--add", "Projects", "--remove", "unread", "--confirm")
    assert Box.filed == [("t-m1", [], ["inbox"]), ("t-m1", ["Projects"], ["unread"])]
    code, output = hz(kernel, "mail", "label", "t-m1", "--confirm")
    assert code != 0
    assert "Name a label" in str(output)


def test_without_a_mailbox_a_write_says_how_to_connect_one(make_kernel):
    kernel = make_kernel(builtins=True, config=Config(files=(), data={"capabilities": {"mail": "none"}}))
    code, output = hz(kernel, "mail", "send", "r-1")
    assert code != 0
    assert "Nothing writes your mail" in str(output)


def test_reads_are_tools_and_web_endpoints_but_never_rest_and_writes_skip_mcp_over_http(kernel):
    names = {head: {item.name for item in exposed(kernel, head)} for head in ("mcp", MCP_HTTP, web.HEAD, api.HEAD)}
    reads = {"mail", "mail_search", "mail_show", "mail_preview", "mail_unread"}
    writes = {"mail_draft", "mail_reply", "mail_archive", "mail_label"}
    assert reads | writes <= names["mcp"]  # over stdio: the client started the server as you
    assert reads | writes | {"mail_send"} <= names[web.HEAD]
    assert "mail_send" not in names["mcp"]  # agents draft, people send
    assert not (reads | writes | {"mail_send"}) & (names[MCP_HTTP] | names[api.HEAD])  # nothing unauthenticated
    assert "mail_notice" not in names["mcp"] | names[web.HEAD]


def test_the_web_head_serves_the_inbox_behind_its_token(kernel):
    client = TestClient(web.build(kernel, port=8000, token=TOKEN, static=None), base_url="http://127.0.0.1:8000")
    assert client.get("/api/mail").status_code == 401
    assert [m["id"] for m in client.get("/api/mail", headers=AUTH).json()["messages"]] == ["m2", "m1"]


class Inbox(Notifications):
    """Notifications in memory."""

    def __init__(self) -> None:
        self.emitted: list[dict[str, object]] = []

    def emit(self, kind, title, body="", *, route, command, key, urgent=False):  # noqa: PLR0913 - the base's shape
        self.emitted.append({"kind": kind, "title": title, "body": body, "route": route, "key": key})


def test_important_unread_mail_gets_a_notice_at_the_notify_tick(kernel):
    inbox = Inbox()
    kernel.bind(Notifications, inbox)
    assert tick(kernel, set()) == 0
    assert tick(kernel, {NoticeKind.MAIL}) == 1
    (only,) = inbox.emitted
    assert (only["title"], only["body"], only["key"], only["route"]) == (
        "From Zoe",
        "Budget due today",
        "mail:m2",
        "/mail",
    )


def test_mail_notices_can_be_turned_off_and_need_a_mailbox(make_kernel):
    inbox = Inbox()
    kernel = with_box(make_kernel, notify_important=False)
    kernel.bind(Notifications, inbox)
    assert kernel.call(notice).skipped == "important mail notices are off"
    unbound = make_kernel(builtins=True, config=Config(files=(), data={"capabilities": {"mail": "none"}}))
    unbound.bind(Notifications, inbox)
    assert str(unbound.call(notice).skipped).startswith("no mailbox")
    assert inbox.emitted == []


def test_what_agents_read_has_no_addresses_and_no_control_characters(kernel):
    code, draft = hz(kernel, "mail", "preview", "r-1")
    assert (code, draft["content"]["text"]) == (0, "Yes, at noon. Ask [email] too.")
    assert draft["content"]["message"]["subject"] == "Re: Lunch? ([email])"
    code, made = hz(kernel, "mail", "draft", "zoe@example.com", "Plan", "Write bo@example.com", "--confirm")
    assert made["draft"]["content"]["text"] == "Yes, at noon. Ask [email] too."
    assert "zoe@example.com" not in made["summary"]
    code, shown = hz(kernel, "mail", "draft", "zoe@example.com", "Plan", "Write bo@example.com")
    assert "[email]" in shown["summary"]
    assert "bo@example.com" not in shown["summary"]


def test_a_reply_says_whom_and_what_it_answers_with_its_text(kernel):
    code, shown = hz(kernel, "mail", "reply", "m1", "Yes, noon.", "--everyone")
    assert code == 0, shown
    assert shown["summary"].startswith("Would keep a reply to 'Zoe' about 'Lunch?', to everyone it went to")
    assert shown["summary"].endswith("Yes, noon.")


def test_the_inbox_asks_for_unread_mail_not_the_whole_inbox(kernel):
    hz(kernel, "mail")
    assert Box.queries == ["label:unread"]


def test_the_important_label_can_be_set(make_kernel, monkeypatch):
    kernel = with_box(make_kernel, important_label="lunch")
    monkeypatch.setattr(Box, "stored", (*Box.stored[:1], message("m4", "Lunch at noon", 5, "UNREAD", "Lunch")))
    _, inbox = hz(kernel, "mail")
    assert inbox["important"] == ["m4"]


def test_the_web_head_sends_only_behind_its_token(kernel):
    client = TestClient(web.build(kernel, port=8000, token=TOKEN, static=None), base_url="http://127.0.0.1:8000")
    assert client.post("/api/mail/send", params={"draft": "r-1", "seen": "m-draft", "confirm": True}).status_code == 401
    assert Box.sent == []
    done = client.post("/api/mail/send", params={"draft": "r-1", "seen": "m-draft", "confirm": True}, headers=AUTH)
    assert (done.status_code, Box.sent) == (200, [("r-1", "m-draft")])


def test_one_failing_tick_operation_doesnt_stop_the_other(make_kernel, caplog, monkeypatch):

    inbox = Inbox()
    kernel = with_box(make_kernel)
    kernel.bind(Notifications, inbox)

    def broken(**_: object) -> None:
        msg = "secret subject"
        raise RuntimeError(msg)

    operation = next(
        let for owner, let in kernel.lets(OperationLet) if (owner.qualname, let.name) == ("calendar", "remind")
    )
    monkeypatch.setattr(
        kernel, "invoke", lambda op, **kw: broken() if op is operation else Kernel.invoke(kernel, op, **kw)
    )
    assert tick(kernel, {NoticeKind.MEETING, NoticeKind.MAIL}) == 1
    assert "hz calendar remind failed: RuntimeError" in caplog.text
    assert "secret subject" not in caplog.text


def test_a_broken_or_unusable_mailbox_degrades_the_unread_count(kernel, make_kernel, monkeypatch):
    def broken(self, query, window):
        msg = "Your sign-in expired."
        raise HeinzelError(msg)

    monkeypatch.setattr(Box, "messages", broken)
    assert hz(kernel, "mail", "unread")[1]["problem"] == "Your sign-in expired."
    monkeypatch.setattr(Box, "messages", lambda self, query, window: 1 / 0)
    assert hz(kernel, "mail", "unread")[1]["problem"] == "Reading your mail failed: ZeroDivisionError"
    nowhere = make_kernel(builtins=True, config=Config(files=(), data={"capabilities": {"mail": "nowhere"}}))
    assert hz(nowhere, "mail", "unread")[1]["line"] == "Mail unavailable"
