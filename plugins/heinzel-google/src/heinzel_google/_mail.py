"""The Gmail adapter of the `mail` capability (ADR 0028), read mode only, through `gws gmail ...` (ADR 0038).

`messages` lists the ids Gmail finds for the window (`after:`/`before:` in epoch seconds, widened a second), the
sender (`from:`) and the label (by id, `labelIds`, exact), reads each one's metadata (`users messages get`, format
`metadata`) and keeps those received within the window that `MailQuery.matches`, oldest first. The subject is checked
here only: Gmail's `subject:` matches whole words, the protocol any part of the subject. `read` reads one message in
full and takes its text part, or its HTML part turned into text, in the part's own charset; attachments are left out.
Senders and recipients become `Participant`s, parsed from the raw headers so that an encoded display name can't pose
as an address; the workflow packet builder drops their addresses before anything reaches an agent's prompt. Nothing
sends, files or deletes mail.
"""

import base64
import binascii
import codecs
import re
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from email.header import decode_header, make_header
from email.message import Message
from email.utils import getaddresses
from html.parser import HTMLParser
from typing import Any, cast, override

from heinzel import HeinzelError, MailContent, MailMessage, MailQuery, Participant, Window, clean_text
from heinzel_google._gws import GoogleSession

LIST = ("gmail", "users", "messages", "list")
GET = ("gmail", "users", "messages", "get")
LABELS = ("gmail", "users", "labels", "list")
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
    """The people of a raw `From`, `To` or `Cc` value: addresses parsed before any display name is decoded."""
    found = []
    for name, address in getaddresses([raw]):
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


class _Text(HTMLParser):
    """HTML to plain text: the text of the document with line breaks at blocks; scripts, styles and titles dropped."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden = 0

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _HIDDEN:
            self._hidden += 1
        elif tag == "body":
            self._hidden = 0  # whatever the head left open ends here
        elif tag in _BLOCK:
            self.parts.append("\n")

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag in _HIDDEN:
            self._hidden = max(0, self._hidden - 1)
        elif tag in _BLOCK:
            self.parts.append("\n")

    @override
    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self.parts.append(data)


def _tidy(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def html_to_text(html: str) -> str:
    """The text of an HTML mail body, whitespace tidied; one pass of the standard library's parser, no fallback."""
    parser = _Text()
    parser.feed(html)
    parser.close()
    return _tidy("".join(parser.parts))


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


class Gmail:
    """Reads the signed-in user's Gmail."""

    def __init__(self, session: GoogleSession) -> None:
        """Build the adapter; the kernel injects the session."""
        self._session = session
        self._names: dict[str, str] | None = None

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
        payload = _object(data.get("payload"))
        headers = {k: v for k, v in _raw_headers(payload).items() if k not in _ADDRESSED}
        return MailContent(message=found, text=_text(payload), headers=_headers(headers))

    def _get(self, message: str, format_: str) -> Json:
        if not _ID.fullmatch(message):
            msg = f"{clean_text(message)[:100]!r} is not a Gmail message id."
            raise HeinzelError(msg)
        params: dict[str, object] = {"userId": "me", "id": message, "format": format_}
        if format_ == "metadata":
            params["metadataHeaders"] = ["From", "To", "Cc", "Subject"]
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
