"""How mail looks in a terminal: one line per message, a conversation's texts, and what a write does.

Subjects, names and texts come from whoever wrote the mail, so they go in as `Text`, never as markup.
"""

from rich.console import Group, RenderableType
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from heinzel import DraftedMail, MailMessage, MailThread
from heinzel.modules.mail._models import Inbox, MailChange, Unread


def _who(message: MailMessage) -> str:
    return message.sender.name or message.sender.email or "(unknown sender)"


def render_inbox(inbox: Inbox) -> RenderableType:
    """A table of messages: when, from whom, the subject, and the conversation to show."""
    if not inbox.messages:
        return Text("No mail here.")
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    for column in ("When", "From", "Subject", "Show"):
        table.add_column(column, overflow="fold")
    for message in inbox.messages:
        mark = "! " if message.id in inbox.important else ""
        table.add_row(
            Text(f"{message.received_at.astimezone():%a %d %b %H:%M}"),
            Text(_who(message)),
            Text(mark + (message.subject or "(no subject)"), style="bold" if mark else ""),
            Text(message.thread or message.id, style="dim"),
        )
    return table


def render_thread(thread: MailThread) -> RenderableType:
    """Every message of a conversation: its sender and time, then its text."""
    parts: list[RenderableType] = [Text(thread.subject or "(no subject)", style="bold")]
    for content in thread.messages:
        message = content.message
        parts.append(Rule(Text(f"{_who(message)} · {message.received_at.astimezone():%a %d %b %H:%M}"), align="left"))
        parts.append(Text(content.text))
        parts.append(Text(f"reply: hz mail reply {message.id} TEXT", style="dim"))
    return Group(*parts)


def render_draft(draft: DraftedMail) -> RenderableType:
    """A draft: to whom, its subject and text, and how to send it."""
    to = ", ".join(p.email or p.name or "" for p in draft.content.message.recipients) or "nobody yet"
    return Group(
        Text(f"To: {to}"),
        Text(f"Subject: {draft.content.message.subject or '(no subject)'}"),
        Text(draft.content.text),
        Text(f"send: hz mail send {draft.id}", style="dim"),
    )


def render_unread(unread: Unread) -> RenderableType:
    """The count, or why there is none and what to run."""
    if unread.problem is not None:
        fix = f" Run `{unread.fix}`." if unread.fix else ""
        return Text(f"{unread.problem}{fix}")
    return Text(unread.line)


def render_change(change: MailChange) -> RenderableType:
    """What it did, or what it would do and the command that does it."""
    if change.done or change.confirm is None:
        return Text(change.summary)
    return Group(Text(change.summary), Text(f"Nothing changed yet. To do it: {change.confirm}", style="bold"))
