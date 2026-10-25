"""The Gmail adapter of the `mail` capability (ADR 0028), read mode only, through `gws gmail ...` (ADR 0042).

`messages` lists the ids Gmail finds for the window (`after:`/`before:` in epoch seconds, widened a second), the
sender (`from:`) and the label (by id, `labelIds`, exact), reads each one's metadata (`users messages get`, format
`metadata`) and keeps those received within the window that `MailQuery.matches`, oldest first. The subject is checked
here only: Gmail's `subject:` matches whole words, the protocol any part of the subject. `read` reads one message in
full and takes its text part, or its HTML part turned into text, in the part's own charset; attachments are left out.
Senders and recipients become `Participant`s, parsed from the raw headers so that an encoded display name can't pose
as an address; the workflow packet builder drops their addresses before anything reaches an agent's prompt.

`thread` reads a conversation (`users threads get`, format `full`), its messages as `read` reads one. Writes need
`gmail.modify` (ADR 0042): `create_draft` builds the message with Python's `email` package (one-line headers only, so
nothing can add a header) and keeps it with `users drafts create`, a reply in its conversation with `In-Reply-To` and
`References`; `send_draft` sends a draft unless it changed since it was shown (`users drafts send`); `relabel`
files a conversation (`users threads modify`) under labels that exist, never into Spam or Trash; the portable
`inbox` and `unread` are Gmail's `INBOX` and `UNREAD`. Nothing deletes mail.
"""

import base64
import binascii
import codecs
import re
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage, Message
from email.utils import getaddresses
from html import unescape
from typing import Any, cast

from heinzel import (
    DraftedMail,
    HeinzelError,
    MailContent,
    MailDraft,
    MailLabels,
    MailMessage,
    MailQuery,
    MailThread,
    Participant,
    SentMail,
    Window,
    clean_text,
    plain_addresses,
)
from heinzel_google._gws import GoogleSession

LIST = ("gmail", "users", "messages", "list")
GET = ("gmail", "users", "messages", "get")
LABELS = ("gmail", "users", "labels", "list")
THREAD = ("gmail", "users", "threads", "get")
MODIFY = ("gmail", "users", "threads", "modify")
DRAFT = ("gmail", "users", "drafts", "get")
CREATE = ("gmail", "users", "drafts", "create")
SEND = ("gmail", "users", "drafts", "send")
PROFILE = ("gmail", "users", "getProfile")
MAX_ATTENDEES = 100
"""Most people a reply computed from the message goes to, `to` and `cc` together; the core's limit for drafts."""
MAX_THREAD = 100
"""Messages of one conversation read at once; a longer one is refused."""
FILED = frozenset({"INBOX", "UNREAD", "STARRED", "IMPORTANT"})
"""The system labels `relabel` may add or remove; user labels too. Not SPAM, TRASH, DRAFT, SENT or CHAT."""
MAX_MESSAGES = 200
"""Messages read for one window, one `gws` run each; more asks for a narrower window or query."""
MAX_TEXT = 2_000_000
"""Characters of one message's text; a longer message is refused, not cut (the `Mail` protocol)."""
MAX_PARTS = 500
"""MIME parts looked at in one message; a message with more is refused."""
MAX_HTML = 2_000_000
"""Characters of an HTML part turned into text; a larger one is refused."""
MAX_HEADER = 10_000
"""Characters of one header value decoded; the rest is dropped before decoding."""
DEADLINE = 300.0
"""Seconds `messages` may spend reading one window's messages; past it, it asks for a narrower window."""
_NOT_TEXT = frozenset({"unicode_escape", "raw_unicode_escape"})
"""Text codecs that interpret their input as escape sequences, not characters: never a mail charset."""
_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_BLOCK = frozenset({"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "table", "hr"})
_HIDDEN = frozenset({"script", "style", "title", "template"})
_ADDRESSED = frozenset({"from", "to", "cc"})

type Json = dict[str, Any]


def _object(value: object) -> Json:
    return cast("Json", value) if isinstance(value, dict) else {}


def _objects(value: object) -> list[Json]:
    if not isinstance(value, list):
        return []
    return [cast("Json", item) for item in cast("list[object]", value) if isinstance(item, dict)]


def _line(value: object) -> str:
    return " ".join(clean_text(str(value or "")).split())


def _decoded(value: str) -> str:
    """A header value, its first `MAX_HEADER` characters, with RFC 2047 encoded words decoded; as it is if malformed."""
    value = value[:MAX_HEADER]
    try:
        return str(make_header(decode_header(value)))
    except (ValueError, LookupError, UnicodeDecodeError):
        return value


def _people(raw: str) -> list[Participant]:
    """The people of a raw `From`, `To` or `Cc` value, its first `MAX_HEADER` characters.

    Addresses are parsed before any display name is decoded, so an encoded name can't pose as an address.
    """
    found = []
    for name, address in getaddresses([raw[:MAX_HEADER]]):
        display = _line(_decoded(name))
        email = _line(address).lower()
        email = email if email.count("@") == 1 and not email.startswith("@") and not email.endswith("@") else ""
        if display or email:
            found.append(Participant(name=display if display and "@" not in display else None, email=email or None))
    return found


def _quoted(value: str) -> str:
    return '"' + value.replace("\\", " ").replace('"', " ") + '"'


def gmail_search(query: MailQuery, window: Window) -> str:
    """Gmail's search for the window and the sender; the subject and label are checked otherwise (module docstring)."""
    since = int(window.since.timestamp())
    until = int(window.until.timestamp()) + 1
    terms = [f"after:{since - 1}", f"before:{until}"]
    if query.sender:
        terms.append(f"from:{_quoted(query.sender)}")
    return " ".join(terms)


def _tidy(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _tag_name(tag: str) -> tuple[str, bool]:
    """A tag's lower-case name and whether it closes, from the text between `<` and `>`."""
    closing = tag.startswith("/")
    words = tag.lstrip("/").split(None, 1)
    return (words[0].rstrip("/").lower() if words else ""), closing


def html_to_text(html: str) -> str:
    """The text of an HTML mail body, whitespace tidied, in one linear scan.

    Not `html.parser`: its handling of unterminated tags rescans the input and is quadratic on older Pythons. Text
    between tags is kept, block tags become line breaks, the content of `script`, `style`, `title` and `template` up
    to its own end tag (a `/>` closes none of them) and comments are dropped, and an unterminated tag or comment ends
    the text. Entities are decoded once, at the end.
    """
    parts: list[str] = []
    hidden: str | None = None
    position, size = 0, len(html)
    while position < size:
        opening = html.find("<", position)
        if opening == -1:
            if hidden is None:
                parts.append(html[position:])
            break
        if hidden is None:
            parts.append(html[position:opening])
        if html.startswith("<!--", opening):
            ending = html.find("-->", opening + 4)
            position = size if ending == -1 else ending + 3
            continue
        closing = html.find(">", opening + 1)
        if closing == -1:
            break
        name, ends = _tag_name(html[opening + 1 : closing])
        if hidden is not None:
            hidden = None if ends and name == hidden else hidden  # only its own end tag ends it
        elif name in _HIDDEN and not ends:  # `<script/>` is no empty element in HTML: it hides what follows
            hidden = name
        elif name in _BLOCK:
            parts.append("\n")
        position = closing + 1
    return _tidy(unescape("".join(parts)))


def _walk(payload: Json) -> Iterator[Json]:
    """The MIME parts of a payload, depth first, without recursion.

    Yields:
        Each part, the payload first.

    Raises:
        HeinzelError: If the message has more than `MAX_PARTS` parts.
    """
    pending, seen = [payload], 0
    while pending:
        if seen == MAX_PARTS:
            msg = f"The message has more than {MAX_PARTS} MIME parts; it is too large to read."
            raise HeinzelError(msg)
        part = pending.pop()
        seen += 1
        yield part
        pending += reversed(_objects(part.get("parts")))


def _charset(part: Json) -> str:
    """The part's charset from its `Content-Type`, if Python knows it; UTF-8 otherwise."""
    content_type = next(
        (
            str(h.get("value") or "")
            for h in _objects(part.get("headers"))
            if str(h.get("name")).lower() == "content-type"
        ),
        "",
    )
    header = Message()
    header["Content-Type"] = content_type or "text/plain"
    charset = header.get_content_charset() or "utf-8"
    try:
        found = codecs.lookup(charset)
    except (LookupError, UnicodeError):
        return "utf-8"
    # Only text encodings: `zlib`, `base64`, `hex` or `rot13` are byte or str transforms, the escape codecs no charset.
    if not getattr(found, "_is_text_encoding", False) or found.name.replace("-", "_") in _NOT_TEXT:
        return "utf-8"
    return found.name


def _body(part: Json) -> str:
    data = _object(part.get("body")).get("data")
    if not isinstance(data, str) or not data:
        return ""
    try:
        raw = base64.b64decode(data + "=" * (-len(data) % 4), altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        return ""
    try:
        return raw.decode(_charset(part), errors="replace")
    except (LookupError, UnicodeError, ValueError):
        return raw.decode("utf-8", errors="replace")


def _text(payload: Json) -> str:
    """The message's text: its first text/plain part, else its first text/html part as text; not attachments.

    Raises:
        HeinzelError: If the message has too many parts, or the HTML or the text is too long to read.
    """
    plain = html = None
    for part in _walk(payload):
        if part.get("filename"):
            continue
        mime = str(part.get("mimeType") or "").lower()
        if mime == "text/plain" and plain is None:
            plain = _body(part)
        elif mime == "text/html" and html is None:
            html = _body(part)
    if plain is None and html is not None:
        if len(html) > MAX_HTML:
            msg = f"The message's HTML passes {MAX_HTML:,} characters; it is too large to read."
            raise HeinzelError(msg)
        plain = html_to_text(html)
    text = clean_text((plain or "").replace("\r\n", "\n"))
    if len(text) > MAX_TEXT:
        msg = f"The message's text passes {MAX_TEXT:,} characters; it is too large to read."
        raise HeinzelError(msg)
    return text


def _raw_headers(payload: Json) -> dict[str, list[str]]:
    """Every header's raw values by lower-case name, in order."""
    found: dict[str, list[str]] = {}
    for header in _objects(payload.get("headers")):
        name = str(header.get("name") or "").lower()
        if name:
            found.setdefault(name, []).append(str(header.get("value") or ""))
    return found


def _headers(raw: dict[str, list[str]]) -> dict[str, str]:
    """Decoded header values, repeated ones joined with `, `; `MailContent` keeps only those without addresses."""
    return {name: ", ".join(_line(_decoded(value)) for value in values) for name, values in raw.items()}


def _label_id(label: str, names: dict[str, str]) -> str | None:
    """The id of the label named `label`, ignoring case; None if there is none.

    Raises:
        HeinzelError: For Spam and Trash, which Gmail lists only when asked to and nothing here reads.
    """
    if label.casefold() in {"spam", "trash"}:
        msg = "Mail in Spam or Trash is not read; ask for another label."
        raise HeinzelError(msg)
    return next((i for i, name in names.items() if name.casefold() == label.casefold()), None)


_REPLY_HEADERS = ("From", "To", "Cc", "Subject", "Reply-To", "Message-ID", "References")
_MESSAGE_ID = re.compile(r"^<[\x21-\x3b\x3d\x3f-\x7e]{1,250}>$")
"""A `Message-ID` as RFC 5322 writes it: `<...>`, printable ASCII, no spaces or angle brackets inside."""


def _check_id(value: str, what: str) -> None:
    if not _ID.fullmatch(value):
        msg = f"{clean_text(value)[:100]!r} is not a Gmail {what} id."
        raise HeinzelError(msg)


def _re(subject: str) -> str:
    """The subject of a reply: `Re: <subject>`, without stacking a second `Re:`."""
    return subject if subject[:3].casefold() == "re:" else f"Re: {subject}".strip()


def _is_system(label_id: str) -> bool:
    """Whether `label_id` is one of Gmail's own labels (`INBOX`, `SPAM`, `CATEGORY_SOCIAL`), not the user's."""
    return (label_id.isupper() and "_" not in label_id) or label_id.startswith("CATEGORY_")


def _label_ids(labels: list[str], names: dict[str, str]) -> list[str]:
    """The ids of labels named `labels`, ignoring case.

    Raises:
        HeinzelError: For a label that doesn't exist, or a system label `relabel` may not set.
    """
    found: list[str] = []
    for label in labels:
        wanted = label.strip()
        if not wanted:
            continue
        # Your own label first: one named like a system label (a label "Spam") is yours to set.
        matches = sorted((i for i, name in names.items() if name.casefold() == wanted.casefold()), key=_is_system)
        label_id = matches[0] if matches else None
        if label_id is None:
            msg = f"Gmail has no label {clean_text(wanted)[:100]!r}; create it in Gmail first."
            raise HeinzelError(msg)
        if _is_system(label_id) and label_id not in FILED:
            msg = f"{label_id} can't be set or removed here; only {', '.join(sorted(FILED))} and your own labels."
            raise HeinzelError(msg)
        if label_id not in found:
            found.append(label_id)
    return found


def _plain(addresses: list[str]) -> list[str]:
    """The plain addresses among `addresses` (`plain_addresses`), each once; what isn't one is left out."""
    found: list[str] = []
    for address in addresses:
        try:
            found += plain_addresses([address])
        except ValueError:
            continue  # a header's oddity is nobody to write to
    return list(dict.fromkeys(found))


class Gmail:
    """Reads the signed-in user's Gmail, keeps drafts, sends them and files conversations."""

    def __init__(self, session: GoogleSession) -> None:
        """Build the adapter; the kernel injects the session."""
        self._session = session
        self._names: dict[str, str] | None = None
        self._address: str | None = None

    def messages(self, query: str, window: Window) -> list[MailMessage]:
        """Messages that match `query` (see `MailQuery`) and were received within `window`, oldest first.

        Raises:
            HeinzelError: If the query or window is malformed, the sign-in is missing or lacks the Gmail scope, Google
                fails, or the window holds more than `MAX_MESSAGES` messages (narrow it).
        """
        try:
            parsed = MailQuery.parse(query)
        except ValueError as exc:
            raise HeinzelError(str(exc)) from None
        if window.since > window.until:
            msg = f"The window starts ({window.since.isoformat()}) after it ends ({window.until.isoformat()})."
            raise HeinzelError(msg)
        if window.since == window.until:
            return []
        names = self._labels()
        params: dict[str, object] = {"userId": "me", "q": gmail_search(parsed, window), "maxResults": MAX_MESSAGES}
        if parsed.label is not None:
            label = _label_id(parsed.label, names)
            if label is None:
                return []  # no label of that name: nothing is filed under it
            params["labelIds"] = [label]
        found: dict[str, MailMessage] = {}
        deadline = time.monotonic() + DEADLINE
        for message_id in self._ids(params):
            if time.monotonic() > deadline:
                msg = f"Reading the window's messages takes more than {DEADLINE:g} seconds; narrow the window or query."
                raise HeinzelError(msg)
            message = self._message(self._get(message_id, "metadata"), names)
            if message is not None and window.since <= message.received_at < window.until and parsed.matches(message):
                found[message.id] = message
        return sorted(found.values(), key=lambda m: (m.received_at, m.id))

    def _ids(self, params: dict[str, object]) -> list[str]:
        """The ids Gmail lists for `params`, each once, in order.

        Raises:
            HeinzelError: If there are more than `MAX_MESSAGES`.
        """
        found: list[str] = []
        for items, _ in self._session.pages("Gmail", LIST, params, "the mail in that window", "messages"):
            found += [str(i["id"]) for i in items if isinstance(i.get("id"), str) and _ID.fullmatch(i["id"])]
            if len(found) > MAX_MESSAGES:
                msg = f"The window holds more than {MAX_MESSAGES} matching messages; narrow the window or the query."
                raise HeinzelError(msg)
        return list(dict.fromkeys(found))

    def read(self, message: str) -> MailContent:
        """The message `message` (a `MailMessage.id`) with its text and headers, address headers left out.

        Raises:
            HeinzelError: If the id is malformed, the sign-in fails, or the message is too large to read.
        """
        data = self._get(message, "full")
        found = self._message(data, self._labels())
        if found is None:
            msg = f"Gmail's answer for message {message} lacks its id or time."
            raise HeinzelError(msg)
        if {label.casefold() for label in found.labels} & {"spam", "trash"}:
            msg = f"Message {message} is in Spam or Trash, which is not read."
            raise HeinzelError(msg)
        payload = _object(data.get("payload"))
        headers = {k: v for k, v in _raw_headers(payload).items() if k not in _ADDRESSED}
        return MailContent(message=found, text=_text(payload), headers=_headers(headers))

    def thread(self, thread: str) -> MailThread:
        """The conversation `thread` with every message's text, oldest first; messages in Spam or Trash left out.

        Raises:
            HeinzelError: If the id is malformed, the conversation is longer than `MAX_THREAD` messages or only in
                Spam or Trash, a message is too large, or the sign-in fails.
        """
        _check_id(thread, "conversation")
        data = self._session.get("Gmail", THREAD, {"userId": "me", "id": thread, "format": "full"}, f"thread {thread}")
        items = _objects(data[0].get("messages"))
        if len(items) > MAX_THREAD:
            msg = f"The conversation has more than {MAX_THREAD} messages; open it in Gmail."
            raise HeinzelError(msg)
        names = self._labels()
        contents = [c for c in (self._content(item, names) for item in items) if c is not None]
        contents.sort(key=lambda c: (c.message.received_at, c.message.id))
        if not contents:
            msg = f"Gmail has no conversation {thread} outside Spam and Trash."
            raise HeinzelError(msg)
        return MailThread(id=thread, subject=contents[0].message.subject, messages=contents)

    def draft(self, draft: str) -> DraftedMail:
        """The draft `draft` as it stands in Gmail, possibly edited there since it was made.

        Raises:
            HeinzelError: If the id is malformed, there is no such draft, it is too large, or the sign-in fails.
        """
        _check_id(draft, "draft")
        data = self._session.get("Gmail", DRAFT, {"userId": "me", "id": draft, "format": "full"}, f"draft {draft}")[0]
        content = self._content(_object(data.get("message")), self._labels(), drafts=True)
        if content is None:
            msg = f"Gmail's answer for draft {draft} lacks its message."
            raise HeinzelError(msg)
        return DraftedMail(
            id=draft, content=content, url=f"https://mail.google.com/mail/#drafts?compose={content.message.id}"
        )

    def create_draft(self, draft: MailDraft) -> DraftedMail:
        """Keep `draft` as a Gmail draft, unsent; a reply joins the conversation of the message it answers.

        Raises:
            HeinzelError: If the message answered can't be read, the reply would go nowhere, the sign-in lacks
                Gmail's modify scope (the message names the sign-in), or Google refuses.
        """
        email = EmailMessage(policy=policy.SMTP)
        body: dict[str, object] = {}
        to, cc, subject = list(draft.to), list(draft.cc), draft.subject
        if draft.reply_to is not None:
            original = self._get(draft.reply_to, "metadata", headers=_REPLY_HEADERS)
            raw = _raw_headers(_object(original.get("payload")))
            answered = self._message(original, self._labels())
            if answered is None:
                msg = f"Gmail's answer for message {draft.reply_to} lacks its id or time."
                raise HeinzelError(msg)
            if not to:
                to, cc = self._recipients(raw, cc, everyone=draft.reply_all)
            subject = subject or _re(answered.subject)
            message_id = _line(_decoded((raw.get("message-id") or [""])[0]))
            if _MESSAGE_ID.fullmatch(message_id):
                email["In-Reply-To"] = message_id
                references = _line(_decoded(" ".join(raw.get("references", []))))
                kept = [r for r in references.split() if _MESSAGE_ID.fullmatch(r)][-20:]
                email["References"] = " ".join([*kept, message_id])
            if answered.thread is not None:
                body["threadId"] = answered.thread
        if not to:
            msg = "The reply would go to nobody: the message has no sender to answer. Give `to`."
            raise HeinzelError(msg)
        email["To"] = ", ".join(to)
        if cc:
            email["Cc"] = ", ".join(cc)
        email["Subject"] = subject
        email.set_content(draft.text)
        body["raw"] = base64.urlsafe_b64encode(email.as_bytes()).decode("ascii")
        answer = self._session.write("Gmail", CREATE, {"userId": "me"}, {"message": body}, "the new draft")
        created = answer.get("id")
        if not isinstance(created, str) or not _ID.fullmatch(created):
            msg = "Gmail's answer to the new draft lacks its id; look in Drafts."
            raise HeinzelError(msg)
        try:
            return self.draft(created)
        except HeinzelError as exc:  # it exists: a retry would make a second one
            msg = f"The draft {created} was made, but reading it back failed ({exc}); look in Drafts, don't redo it."
            raise HeinzelError(msg) from None

    def send_draft(self, draft: str, seen: str) -> SentMail:
        """Send the draft `draft` if its message is still `seen`, the one the person was shown.

        Raises:
            HeinzelError: If an id is malformed, there is no such draft, it changed since it was shown, the sign-in
                lacks Gmail's modify scope, Google refuses, or gws doesn't answer in time (then look in Sent first).
        """
        _check_id(draft, "draft")
        _check_id(seen, "message")
        current = self.draft(draft)
        if current.content.message.id != seen:
            msg = f"The draft {draft} changed since you saw it; nothing was sent. Look at it again, then send it."
            raise HeinzelError(msg)
        answer = self._session.write("Gmail", SEND, {"userId": "me"}, {"id": draft}, f"draft {draft}")
        sent, thread = answer.get("id"), answer.get("threadId")
        if not isinstance(sent, str) or not _ID.fullmatch(sent):
            msg = "Gmail sent the draft but its answer lacks the message's id; look in Sent."
            raise HeinzelError(msg)
        return SentMail(
            id=sent,
            thread=thread if isinstance(thread, str) and _ID.fullmatch(thread) else None,
            url=f"https://mail.google.com/mail/#all/{sent}",
        )

    def relabel(self, thread: str, add: list[str], remove: list[str]) -> MailLabels:
        """File the conversation `thread`: add `add` and remove `remove`, labels by name, ignoring case.

        Raises:
            HeinzelError: If the id is malformed, nothing changes, a label doesn't exist or may not be set (Spam,
                Trash, Drafts, Sent), the sign-in lacks Gmail's modify scope, or Google refuses.
        """
        _check_id(thread, "conversation")
        names = self._labels()
        adding, removing = _label_ids(add, names), _label_ids(remove, names)
        if not adding and not removing:
            msg = "Name a label to add or remove, e.g. INBOX to archive or UNREAD to mark it read."
            raise HeinzelError(msg)
        body = {"addLabelIds": adding, "removeLabelIds": removing}
        answer = self._session.write("Gmail", MODIFY, {"userId": "me", "id": thread}, body, f"thread {thread}")
        labels: dict[str, None] = {}
        for item in _objects(answer.get("messages")):
            ids = item.get("labelIds")
            for label in cast("list[object]", ids if isinstance(ids, list) else []):
                labels[names.get(str(label), _line(label))] = None
        return MailLabels(thread=thread, labels=list(labels))

    def _content(self, data: Json, names: dict[str, str], *, drafts: bool = False) -> MailContent | None:
        """A message of a thread or draft with its text; None for one without id or time, or in Spam or Trash."""
        found = self._message(data, names)
        if found is None:
            return None
        if {label.casefold() for label in found.labels} & {"spam", "trash"}:
            return None
        if not drafts and "DRAFT" in found.labels:
            return None  # an unsent reply in the conversation is the user's, not the thread's
        payload = _object(data.get("payload"))
        headers = {k: v for k, v in _raw_headers(payload).items() if k not in _ADDRESSED}
        return MailContent(message=found, text=_text(payload), headers=_headers(headers))

    def _recipients(self, raw: dict[str, list[str]], cc: list[str], *, everyone: bool) -> tuple[list[str], list[str]]:
        """Whom a reply goes to: the Reply-To, else the sender; with `everyone`, the other recipients in Cc too.

        Raises:
            HeinzelError: If that is more than `MAX_ATTENDEES` people.
        """
        to = _plain([p.email for p in _people(", ".join(raw.get("reply-to") or raw.get("from", []))) if p.email])
        others = _plain([p.email for p in _people(", ".join([*raw.get("to", []), *raw.get("cc", [])])) if p.email])
        me = self._me() if everyone or to else ""
        if to == [me]:  # your own message: answer whom it went to, as Gmail does
            to, others = [a for a in others if a != me] or to, []
        if everyone:
            cc = [*cc, *(a for a in others if a != me and a not in to)]
        cc = list(dict.fromkeys(cc))
        if len(to) + len(cc) > MAX_ATTENDEES:
            msg = f"The reply would go to more than {MAX_ATTENDEES} people; give `to` and `cc` yourself."
            raise HeinzelError(msg)
        return to, cc

    def _me(self) -> str:
        """The signed-in user's address, from Gmail's profile; read once per adapter."""
        if self._address is None:
            data = self._session.get("Gmail", PROFILE, {"userId": "me"}, "your address")[0]
            self._address = _line(data.get("emailAddress")).lower()
        return self._address

    def _get(self, message: str, format_: str, *, headers: tuple[str, ...] = ("From", "To", "Cc", "Subject")) -> Json:
        if not _ID.fullmatch(message):
            msg = f"{clean_text(message)[:100]!r} is not a Gmail message id."
            raise HeinzelError(msg)
        params: dict[str, object] = {"userId": "me", "id": message, "format": format_}
        if format_ == "metadata":
            params["metadataHeaders"] = list(headers)
        return self._session.get("Gmail", GET, params, f"message {message}")[0]

    def _labels(self) -> dict[str, str]:
        """Label names by id, read once per adapter: system labels are their own names, user labels have ids."""
        if self._names is None:
            data = self._session.get("Gmail", LABELS, {"userId": "me"}, "the labels")[0]
            self._names = {
                str(label["id"]): _line(label.get("name") or label["id"])
                for label in _objects(data.get("labels"))
                if isinstance(label.get("id"), str)
            }
        return self._names

    @staticmethod
    def _message(data: Json, names: dict[str, str]) -> MailMessage | None:
        message_id, millis = data.get("id"), data.get("internalDate")
        if not isinstance(message_id, str) or not _ID.fullmatch(message_id):
            return None
        try:
            received = datetime.fromtimestamp(int(str(millis)) / 1000, UTC)
        except (ValueError, OverflowError, OSError):
            return None
        raw = _raw_headers(_object(data.get("payload")))
        senders = _people(", ".join(raw.get("from", [])))
        recipients = _people(", ".join([*raw.get("to", []), *raw.get("cc", [])]))
        subject = _headers({"subject": raw.get("subject", [])[:1]}).get("subject", "")
        thread = data.get("threadId")
        ids = data.get("labelIds")
        labels = [
            names.get(str(label), _line(label)) for label in cast("list[object]", ids if isinstance(ids, list) else [])
        ]
        return MailMessage(
            id=message_id,
            thread=thread if isinstance(thread, str) and _ID.fullmatch(thread) else None,
            sender=senders[0] if senders else Participant(),
            recipients=recipients,
            subject=subject,
            received_at=received,
            labels=labels,
            url=f"https://mail.google.com/mail/#all/{message_id}",
        )
