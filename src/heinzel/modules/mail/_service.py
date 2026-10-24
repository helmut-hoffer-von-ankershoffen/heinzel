"""What the mail operations do, over the bound `mail` capability: list, search, read, draft, send, file.

Message text reaches agents' models through MCP, so every text the reads and drafts return has its email addresses
replaced (`redact_emails`) and control characters removed (`clean_text`); senders and recipients stay `Participant`s,
whose addresses a chat's head redacts. `send`, for people only, shows the whole draft as it is, recipients included.
Nothing here logs a message or its text.

Writes show what they would do and change nothing until they are run again with `confirm` (ADR 0059). Sending sends
a draft only if it is still the one shown (`seen`, its message id), so an edit in between sends nothing.
"""

import shlex
from collections.abc import Sequence
from datetime import datetime, timedelta

from heinzel import (
    DraftedMail,
    HeinzelError,
    Mail,
    MailContent,
    MailDraft,
    MailMessage,
    MailQuery,
    MailThread,
    Participant,
    Window,
    clean_text,
    redact_emails,
)
from heinzel.modules.mail._models import Inbox, MailChange, MailSettings

EXCERPT = 600
"""Characters of a draft's text a draft's or reply's summary shows; a send shows all of it."""

INBOX = "inbox"
UNREAD = "unread"
"""The portable label names every mail adapter knows (ADR 0059)."""


def safe(text: str) -> str:
    """`text` as this module returns it: no control characters, no email addresses."""
    return redact_emails(clean_text(text))


def _labelled(message: MailMessage, label: str) -> bool:
    return label.casefold() in {name.casefold() for name in message.labels}


def _named(person: Participant) -> Participant:
    return person.model_copy(update={"name": clean_text(person.name) if person.name else None})


def _safe_message(message: MailMessage) -> MailMessage:
    return message.model_copy(
        update={
            "subject": safe(message.subject),
            "sender": _named(message.sender),
            "recipients": [_named(p) for p in message.recipients],
        }
    )


def _safe_content(content: MailContent) -> MailContent:
    return content.model_copy(update={"message": _safe_message(content.message), "text": safe(content.text)})


def window(now: datetime, days: int) -> Window:
    """The last `days` days up to now."""
    return Window(since=now - timedelta(days=days), until=now + timedelta(seconds=1))


def ordered(messages: Sequence[MailMessage], settings: MailSettings, since: datetime, until: datetime) -> Inbox:
    """`messages` with the important first, then the newest; subjects made safe."""
    important = [m.id for m in messages if _labelled(m, settings.important_label)]
    found = sorted(messages, key=lambda m: (m.id not in important, -m.received_at.timestamp()))
    return Inbox(since=since, until=until, messages=[_safe_message(m) for m in found], important=important)


def inbox(mail: Mail, settings: MailSettings, now: datetime, *, everything: bool = False) -> Inbox:
    """Unread mail in your inbox from the last `[mail] days` (all of it with `everything`).

    Raises:
        HeinzelError: If the mailbox can't be read.
    """
    span = window(now, settings.days)
    # The narrower label is the query, the other is checked here: a busy inbox holds more than an adapter lists at
    # once (Gmail's reads 200 a window), its unread part rarely does.
    if everything:
        return ordered(mail.messages(f"label:{INBOX}", span), settings, span.since, span.until)
    unread = [m for m in mail.messages(f"label:{UNREAD}", span) if _labelled(m, INBOX)]
    return ordered(unread, settings, span.since, span.until)


def search(mail: Mail, settings: MailSettings, query: str, now: datetime, days: int) -> Inbox:
    """Mail from the last `days` days that matches `query` (`from:`, `subject:`, `label:`).

    Raises:
        HeinzelError: If the query is malformed (with one that works), or the mailbox can't be read.
    """
    try:
        MailQuery.parse(query)
    except ValueError as exc:
        raise HeinzelError(str(exc)) from None
    span = window(now, days)
    return ordered(mail.messages(query, span), settings, span.since, span.until)


def thread(mail: Mail, thread: str) -> MailThread:
    """The conversation `thread`, every message's text made safe.

    Raises:
        HeinzelError: If there is no such conversation, or the mailbox can't be read.
    """
    found = mail.thread(thread)
    return found.model_copy(
        update={"subject": safe(found.subject), "messages": [_safe_content(c) for c in found.messages]}
    )


def preview(mail: Mail, draft: str) -> DraftedMail:
    """The draft `draft` as it stands, its text made safe.

    Raises:
        HeinzelError: If there is no such draft, or the mailbox can't be read.
    """
    found = mail.draft(draft)
    return found.model_copy(update={"content": _safe_content(found.content)})


def _command(words: Sequence[str]) -> str:
    return " ".join(shlex.quote(w) for w in ["hz", "mail", *words, "--confirm"])


def _to(draft: DraftedMail) -> str:
    """Every recipient, To and Cc: what a person must see before a send."""
    people = [p.email or p.name or "" for p in draft.content.message.recipients]
    return ", ".join(clean_text(p) for p in people if p) or "nobody yet"


def write_draft(mail: Mail, draft: MailDraft, command: Sequence[str], *, confirm: bool) -> MailChange:
    """Keep `draft` in the mailbox with `confirm`; without it, say what would be kept. Nothing is sent.

    Raises:
        HeinzelError: If the message answered can't be read, or the mailbox refuses.
    """
    if draft.reply_to:
        answered = mail.read(draft.reply_to).message
        who = answered.sender.name or answered.sender.email or "its sender"
        what = f"a reply to {who!r} about {answered.subject or '(no subject)'!r}"
        what += ", to everyone it went to" if draft.reply_all else ""
    else:
        what = f"a draft to {', '.join([*draft.to, *draft.cc])}, {draft.subject!r}"
    excerpt = draft.text if len(draft.text) <= EXCERPT else draft.text[:EXCERPT] + "…"
    if not confirm:
        summary = f"Would keep {what} in your drafts; nothing is sent:\n{excerpt}"
        return MailChange(done=False, summary=safe(summary), confirm=_command(command))
    made = mail.create_draft(draft)
    safe_made = made.model_copy(update={"content": _safe_content(made.content)})
    summary = f"Kept a draft to {_to(made)} in your drafts; a person sends it with `hz mail send {made.id}`."
    return MailChange(done=True, summary=safe(summary), draft=safe_made)


def send(mail: Mail, draft: str, seen: str | None, *, confirm: bool) -> MailChange:
    """Send `draft` with `confirm` if it is still `seen`; without it, show the draft and the command that sends it.

    Raises:
        HeinzelError: If `confirm` comes without `seen` (show it first), the draft changed, or the mailbox refuses.
    """
    # For a person only (`send` is on the CLI and web heads): the whole draft, every recipient, addresses as they
    # are, since they approve exactly what goes out.
    current = mail.draft(draft)
    content = current.content.model_copy(update={"text": clean_text(current.content.text)})
    shown = current.model_copy(update={"content": content})
    subject = clean_text(current.content.message.subject) or "(no subject)"
    if not confirm:
        return MailChange(
            done=False,
            summary=f"Would send {subject!r} to {_to(current)}:\n{content.text}",
            confirm=_command(["send", draft, "--seen", current.content.message.id]),
            draft=shown,
        )
    if seen is None:
        msg = f"Run `hz mail send {draft}` first: it shows the draft and the command that sends exactly that one."
        raise HeinzelError(msg)
    sent = mail.send_draft(draft, seen)
    return MailChange(done=True, summary=f"Sent {subject!r} to {_to(current)}.", sent=sent)


def relabel(  # noqa: PLR0913 - what filing is
    mail: Mail,
    thread: str,
    add: list[str],
    remove: list[str],
    command: Sequence[str],
    *,
    confirm: bool,
) -> MailChange:
    """File the conversation `thread` with `confirm`; without it, say how it would be filed.

    Raises:
        HeinzelError: If nothing would change, a label doesn't exist, or the mailbox refuses.
    """
    add, remove = [a for a in add if a.strip()], [r for r in remove if r.strip()]
    if not add and not remove:
        msg = "Name a label to add or remove, e.g. `hz mail label THREAD --add Projects`."
        raise HeinzelError(msg)
    parts = ([f"adding {', '.join(add)}"] if add else []) + ([f"removing {', '.join(remove)}"] if remove else [])
    what = f"conversation {thread}, {' and '.join(parts)}"
    if not confirm:
        return MailChange(done=False, summary=f"Would file {what}.", confirm=_command(command))
    return MailChange(done=True, summary=f"Filed {what}.", labels=mail.relabel(thread, add, remove))
