"""The mail module's service and rendering, with a mailbox in memory."""

from datetime import UTC, datetime, timedelta

import pytest
from rich.console import Console

from heinzel import DraftedMail, MailContent, MailMessage, MailThread, Participant
from heinzel.modules.mail import _render, _service
from heinzel.modules.mail._models import Inbox, MailChange, MailSettings, Unread

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 26, 10, tzinfo=UTC)
ZOE = Participant(name="[bold]Zoe[/bold]", email="zoe@example.com")


def message(message_id: str, minutes_ago: int, *labels: str, subject: str = "Plan") -> MailMessage:
    return MailMessage(
        id=message_id,
        thread=f"t-{message_id}",
        sender=ZOE,
        subject=subject,
        received_at=NOW - timedelta(minutes=minutes_ago),
        labels=list(labels),
    )


def text_of(renderable) -> str:
    console = Console(width=200, record=True)
    console.print(renderable)
    return console.export_text()


def test_the_important_come_first_then_the_newest_and_subjects_lose_addresses():
    found = _service.ordered(
        [
            message("old", 90),
            message("new", 5),
            message("vip", 60, "important", subject="From ada@example.com"),
        ],
        MailSettings(),
        NOW - timedelta(days=1),
        NOW,
    )
    assert [m.id for m in found.messages] == ["vip", "new", "old"]
    assert found.important == ["vip"]
    assert found.messages[0].subject == "From [email]"


def test_safe_text_has_no_addresses_and_no_control_characters():
    assert _service.safe("ask ada@example.com\u202e now") == "ask [email] now"


def test_the_terminal_shows_mail_as_text_never_as_markup():
    inbox = Inbox(since=NOW, until=NOW, messages=[message("m1", 5, subject="[link=https://evil.example]Pay[/link]")])
    shown = text_of(_render.render_inbox(inbox))
    assert "[link=https://evil.example]Pay[/link]" in shown
    assert "[bold]Zoe[/bold]" in shown
    assert "No mail here." in text_of(_render.render_inbox(Inbox(since=NOW, until=NOW)))
    content = MailContent(message=message("m1", 5), text="[red]hello[/red]")
    thread = text_of(_render.render_thread(MailThread(id="t-m1", subject="Plan", messages=[content])))
    assert "[red]hello[/red]" in thread
    assert "hz mail reply m1 TEXT" in thread
    draft = text_of(_render.render_draft(DraftedMail(id="r-1", content=content)))
    assert "To: nobody yet" in draft
    assert "hz mail send r-1" in draft


def test_the_unread_line_and_changes_say_what_to_run():
    assert "Run `hz setup google`" in text_of(
        _render.render_unread(Unread(line="x", problem="No mailbox connected.", fix="hz setup google"))
    )
    assert text_of(_render.render_unread(Unread(line="2 unread"))).strip() == "2 unread"
    shown = MailChange(done=False, summary="Would send it.", confirm="hz mail send r-1 --seen m --confirm")
    assert "To do it: hz mail send r-1 --seen m --confirm" in text_of(_render.render_change(shown))
    assert text_of(_render.render_change(MailChange(done=True, summary="Sent."))).strip() == "Sent."
