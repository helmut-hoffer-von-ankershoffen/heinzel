"""The mail module: inbox triage in the CLI, for agents over MCP and in the web app, with drafts and confirmed sends.

It reads and writes through the bound `mail` capability (ADR 0028, ADR 0059), whatever serves it (Gmail through
`heinzel-google`, or another adapter). `hz mail` lists unread mail, the important first; `search`, `show` (a
conversation) and `preview` (a draft) read; `draft`, `reply`, `send`, `archive` and `label` write, and each says what
it would do until it runs again with `--confirm`. `send` is for people only (the CLI and the web app): it shows the
whole draft with every recipient and sends only that version. Agents read and draft over MCP's stdio transport;
texts they get have their email addresses replaced (`[email]`). Nothing goes to an unauthenticated head (REST, MCP over
HTTP).
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from pydantic import Field, ValidationError

from heinzel import (
    DraftedMail,
    HeinzelError,
    Mail,
    MailDraft,
    MailThread,
    Module,
    NoticeKind,
    Notifications,
    Window,
)
from heinzel.kernel import CapabilityView, Requirement
from heinzel.modules.mail import _service as service
from heinzel.modules.mail._models import Inbox, MailChange, MailSettings, Noticed, Unread
from heinzel.modules.mail._render import render_change, render_draft, render_inbox, render_thread, render_unread

module = Module("mail", "Your mail: unread and important first, conversations, drafts and confirmed sends.")
module.settings(MailSettings)

READS = {"cli", "mcp-stdio", "web"}
"""Your mail, bodies and all: never an unauthenticated head (REST, MCP over HTTP)."""
WRITES = {"cli", "mcp-stdio", "web"}
"""Drafts and filing: not over an unauthenticated head either."""
SENDS = {"cli", "web"}
"""Sending: people only. An agent reads untrusted mail and drafts; a person sends, having seen the whole draft."""
READ = ["mail:read"]
WRITE = ["mail:read,write"]
MAIL = Requirement.parse("mail:read")
WRITTEN = Requirement.parse("mail:read,write")
NOTICE_WINDOW = timedelta(hours=1)
"""How far back the notify tick looks for new important mail."""

ThreadId = Annotated[str, Field(description="The conversation's id: `thread` of a message `hz mail` lists.")]
DraftId = Annotated[str, Field(description="The draft's id, as `hz mail draft` or `hz mail reply` answered it.")]
Confirm = Annotated[
    bool,
    Field(
        description="Carry it out. Without it, nothing changes: the answer says what would happen. An agent passes it "
        "only after the person approved that summary."
    ),
]
Days = Annotated[int, Field(ge=1, le=366, description="Days back to look through.")]
Text = Annotated[str, Field(description="The message, plain text.")]
Addresses = Annotated[str, Field(description="Email addresses, comma-separated: ada@example.com,bo@example.com.")]


def _now() -> datetime:
    return datetime.now(UTC)


@module.operation(name="mail", render=render_inbox, heads=READS, requires=READ, optional=True, audience="everyday")
def inbox(
    mail: Mail,
    settings: MailSettings,
    everything: Annotated[bool, Field(description="List read mail in the inbox too, not only unread.")] = False,
) -> Inbox:
    """List the unread mail in your inbox from the last `[mail] days`, the important first."""
    return service.inbox(mail, settings, _now(), everything=everything)


@module.operation(render=render_inbox, heads=READS, requires=READ, optional=True)
def search(
    mail: Mail,
    settings: MailSettings,
    query: Annotated[
        str, Field(description='Terms like `from:ada@example.com subject:"Plan" label:INBOX`; all must match.')
    ],
    days: Days = 30,
) -> Inbox:
    """Find mail of the last days by sender, subject and label."""
    return service.search(mail, settings, query, _now(), days)


@module.operation(render=render_thread, heads=READS, requires=READ, optional=True, audience="everyday")
def show(mail: Mail, thread: ThreadId) -> MailThread:
    """Show a conversation: every message's text, oldest first, as plain text without addresses."""
    return service.thread(mail, thread)


@module.operation(render=render_draft, heads=READS, requires=READ, optional=True)
def preview(mail: Mail, draft: DraftId) -> DraftedMail:
    """Show a draft as it stands, possibly edited in your mail app since."""
    return service.preview(mail, draft)


@module.operation(render=render_unread, heads=READS, requires=READ, optional=True, audience="everyday")
def unread(mail: Mail, settings: MailSettings, view: CapabilityView) -> Unread:
    """Count the unread mail in your inbox, in one line for a status bar; never fails."""
    adapter, problem = view.serves(MAIL)
    if adapter is None or problem is not None:
        if view.bound(MAIL.capability) is None:
            line = "No mailbox connected"
            return Unread(line=line, problem=f"{line} ({problem}).", fix=view.command("mail"))
        return Unread(line="Mail unavailable", problem=f"{problem}. {view.fix('mail')}")
    try:
        found = service.inbox(mail, settings, _now())
    except HeinzelError as exc:
        return Unread(line="Mail unavailable", problem=str(exc))
    except Exception as exc:  # noqa: BLE001 - isolation boundary: a broken adapter must not break the status bar
        return Unread(line="Mail unavailable", problem=f"Reading your mail failed: {type(exc).__name__}")
    count, important = len(found.messages), len(found.important)
    line = f"{count} unread" + (f" ({important} important)" if important else "") if count else "No unread mail"
    return Unread(unread=count, important=important, line=line)


@module.operation(read_only=False, render=render_change, heads=WRITES, requires=WRITE, optional=True)
def draft(  # noqa: PLR0913, PLR0917 - what a message is
    mail: Mail,
    view: CapabilityView,
    to: Addresses,
    subject: Annotated[str, Field(description="The subject, one line.")],
    text: Text,
    cc: Addresses = "",
    confirm: Confirm = False,
) -> MailChange:
    """Keep a new message in your drafts, unsent; shown first, kept with `--confirm`.

    Raises:
        HeinzelError: If nothing writes your mail, or the message can't be made (an address, the subject).
    """
    _writable(view)
    made = _draft(to=to.split(","), cc=cc.split(","), subject=subject, text=text)
    words = ["draft", to, subject, text, *(["--cc", cc] if cc else [])]
    return service.write_draft(mail, made, words, confirm=confirm)


@module.operation(read_only=False, render=render_change, heads=WRITES, requires=WRITE, optional=True)
def reply(  # noqa: PLR0913, PLR0917 - what a reply is
    mail: Mail,
    view: CapabilityView,
    message: Annotated[str, Field(description="The message to answer: `id` of one `hz mail show` lists.")],
    text: Text,
    everyone: Annotated[bool, Field(description="Answer everyone it went to, not only its sender.")] = False,
    confirm: Confirm = False,
) -> MailChange:
    """Keep a reply in your drafts, in its conversation, unsent; shown first, kept with `--confirm`.

    Raises:
        HeinzelError: If nothing writes your mail, or the reply can't be made.
    """
    _writable(view)
    made = _draft(text=text, reply_to=message, reply_all=everyone)
    words = ["reply", message, text, *(["--everyone"] if everyone else [])]
    return service.write_draft(mail, made, words, confirm=confirm)


@module.operation(read_only=False, render=render_change, heads=SENDS, requires=WRITE, optional=True)
def send(
    mail: Mail,
    view: CapabilityView,
    draft: DraftId,
    seen: Annotated[
        str | None,
        Field(description="The draft version to send: run `hz mail send DRAFT` first and copy the command it prints."),
    ] = None,
    confirm: Confirm = False,
) -> MailChange:
    """Send a draft: shown first with its recipients and text, sent with `--confirm` if unchanged since.

    Raises:
        HeinzelError: If nothing writes your mail, `--confirm` comes without `--seen`, or the draft changed.
    """
    _writable(view)
    return service.send(mail, draft, seen, confirm=confirm)


@module.operation(read_only=False, render=render_change, heads=WRITES, requires=WRITE, optional=True)
def archive(mail: Mail, view: CapabilityView, thread: ThreadId, confirm: Confirm = False) -> MailChange:
    """Archive a conversation (out of the inbox, nothing deleted); shown first, done with `--confirm`.

    Raises:
        HeinzelError: If nothing writes your mail, or the mailbox refuses.
    """
    _writable(view)
    return service.relabel(mail, thread, [], [service.INBOX], ["archive", thread], confirm=confirm)


@module.operation(read_only=False, render=render_change, heads=WRITES, requires=WRITE, optional=True)
def label(  # noqa: PLR0913, PLR0917 - what filing is
    mail: Mail,
    view: CapabilityView,
    thread: ThreadId,
    add: Annotated[str, Field(description="Labels to add, comma-separated, e.g. `Projects`.")] = "",
    remove: Annotated[str, Field(description="Labels to remove, comma-separated; `unread` marks it read.")] = "",
    confirm: Confirm = False,
) -> MailChange:
    """File a conversation under labels (never Spam or Trash); shown first, done with `--confirm`.

    Raises:
        HeinzelError: If nothing writes your mail, nothing would change, or a label doesn't exist.
    """
    _writable(view)
    words = ["label", thread, *(["--add", add] if add else []), *(["--remove", remove] if remove else [])]
    return service.relabel(mail, thread, add.split(","), remove.split(","), words, confirm=confirm)


# The daemon's notify tick runs it (`system.notify`), like the calendar's reminders: no job, no receipts. It only
# records local notices; it is still no read, so a chat's agent can't call it.
@module.operation(read_only=False, heads={"cli"}, requires=READ, optional=True)
def notice(mail: Mail, settings: MailSettings, notifications: Notifications, view: CapabilityView) -> Noticed:
    """Record a notice for each important unread message of the last hour, from whom and about what."""
    adapter, problem = view.serves(MAIL)
    if not settings.notify_important:
        return Noticed(noticed=0, skipped="important mail notices are off")
    if adapter is None or problem is not None:
        return Noticed(noticed=0, skipped=f"no mailbox: {problem}")
    now = _now()
    try:
        found = mail.messages(_label(settings.important_label), Window(since=now - NOTICE_WINDOW, until=now))
    except HeinzelError as exc:  # an expired sign-in, at every tick: said once in the result, not as a traceback
        return Noticed(noticed=0, skipped=service.safe(str(exc)))
    important = [m for m in found if {service.INBOX, service.UNREAD} <= {name.casefold() for name in m.labels}]
    for message in important:
        sender = message.sender.name or message.sender.email or "someone"
        notifications.emit(
            NoticeKind.MAIL,
            f"From {service.safe(sender)}",
            service.safe(message.subject),
            route="/mail",
            command="hz mail",
            key=f"mail:{message.id}",
        )
    return Noticed(noticed=len(important))


def _label(name: str) -> str:
    """A `label:` query term for `name`, quoted when it holds spaces."""
    return f'label:"{name}"' if " " in name else f"label:{name}"


def _writable(view: CapabilityView) -> None:
    """Refuse a write before showing it when nothing serves `mail:write`, saying how to connect one.

    Raises:
        HeinzelError: If no adapter serves writing mail.
    """
    adapter, problem = view.serves(WRITTEN)
    if adapter is None or problem is not None:
        msg = f"Nothing writes your mail ({WRITTEN}: {problem}). {view.fix('mail')}"
        raise HeinzelError(msg)


def _draft(**fields: object) -> MailDraft:
    """A `MailDraft`, or what is wrong with it.

    Raises:
        HeinzelError: If the draft's fields don't make a message.
    """
    try:
        return MailDraft.model_validate(fields)
    except ValidationError as exc:
        reason = str(exc.errors()[0]["msg"]).removeprefix("Value error, ")
        msg = f"The message can't be made: {reason}"
        raise HeinzelError(msg) from None


__all__ = ["Inbox", "MailChange", "MailSettings", "Noticed", "Unread", "module"]
