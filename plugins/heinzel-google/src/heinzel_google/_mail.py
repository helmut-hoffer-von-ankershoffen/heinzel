"""The Gmail adapter of the `mail` capability (ADR 0028), read mode only, through `gws gmail ...` (ADR 0035).

`messages` translates the `MailQuery` into Gmail's own search (`from:`, `subject:`, `label:`, and `after:`/`before:`
in epoch seconds for the window), lists the matching ids (`users messages list`), reads each one's metadata
(`users messages get`, format `metadata`) and keeps those whose received time lies in the window and that
`MailQuery.matches`, oldest first. `read` reads one message in full and takes its text part, or its HTML part turned
into text; attachments are left out. Senders and recipients become `Participant`s; the workflow packet builder drops
their addresses before anything reaches an agent's prompt. Nothing sends, files or deletes mail.
"""

import base64
import binascii
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from email.header import decode_header, make_header
from email.utils import getaddresses
from html.parser import HTMLParser
from typing import Any, cast, override

from heinzel import HeinzelError, MailContent, MailMessage, MailQuery, Participant, Window, clean_text
from heinzel_google._gws import GoogleSession

LIST = ("gmail", "users", "messages", "list")
GET = ("gmail", "users", "messages", "get")
LABELS = ("gmail", "users", "labels", "list")
MAX_MESSAGES = 500
"""Messages read for one window; more asks for a narrower window or query."""
MAX_TEXT = 2_000_000
"""Characters of one message's text; a longer message is refused, not cut (the `Mail` protocol)."""
MAX_PARTS = 500
"""MIME parts looked at in one message."""
_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_BLOCK = frozenset({"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "table", "hr"})

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
    """A header value with its RFC 2047 encoded words decoded; as it is if they are malformed."""
    try:
        return str(make_header(decode_header(value)))
    except (ValueError, LookupError, UnicodeDecodeError):
        return value


def _people(value: str) -> list[Participant]:
    found = []
    for name, address in getaddresses([value]):
        display = _line(_decoded(name))
        email = _line(address).lower() or None
        if display or email:
            found.append(Participant(name=display if display and "@" not in display else None, email=email))
    return found


def _quoted(value: str) -> str:
    return '"' + value.replace("\\", " ").replace('"', " ") + '"'


def gmail_search(query: MailQuery, window: Window) -> str:
    """Gmail's search for `query` within `window`: the window to whole seconds, widened, and checked again after."""
    since = int(window.since.timestamp())
    until = int(window.until.timestamp()) + 1
    terms = [f"after:{since - 1}", f"before:{until}"]
    if query.sender:
        terms.append(f"from:{_quoted(query.sender)}")
    if query.subject:
        terms.append(f"subject:{_quoted(query.subject)}")
    if query.label:
        terms.append(f"label:{_quoted(query.label)}")
    return " ".join(terms)


class _Text(HTMLParser):
    """HTML to plain text: the text of the document with line breaks at blocks; scripts and styles dropped."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden = 0

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "head"}:
            self._hidden += 1
        elif tag in _BLOCK:
            self.parts.append("\n")

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "head"}:
            self._hidden = max(0, self._hidden - 1)
        elif tag in _BLOCK:
            self.parts.append("\n")

    @override
    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """The text of an HTML mail body, whitespace tidied."""
    parser = _Text()
    parser.feed(html)
    parser.close()
    lines = [" ".join(line.split()) for line in "".join(parser.parts).split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _walk(payload: Json) -> Iterator[Json]:
    """The MIME parts of a payload, depth first, at most `MAX_PARTS`, without recursion.

    Yields:
        Each part, the payload first.
    """
    pending, seen = [payload], 0
    while pending and seen < MAX_PARTS:
        part = pending.pop()
        seen += 1
        yield part
        pending += reversed(_objects(part.get("parts")))


def _body(part: Json) -> str:
    data = _object(part.get("body")).get("data")
    if not isinstance(data, str) or not data:
        return ""
    try:
        raw = base64.b64decode(data + "=" * (-len(data) % 4), altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        return ""
    return raw.decode("utf-8", errors="replace")


def _text(payload: Json) -> str:
    """The message's text: its first text/plain part, else its first text/html part as text; not attachments.

    Raises:
        HeinzelError: If the HTML or the text is too long to read.
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
        if len(html) > 4 * MAX_TEXT:
            msg = f"The message's HTML passes {4 * MAX_TEXT:,} characters; it is too large to read."
            raise HeinzelError(msg)
        plain = html_to_text(html)
    text = clean_text((plain or "").replace("\r\n", "\n"))
    if len(text) > MAX_TEXT:
        msg = f"The message's text passes {MAX_TEXT:,} characters; it is too large to read."
        raise HeinzelError(msg)
    return text


def _headers(payload: Json) -> dict[str, str]:
    found: dict[str, str] = {}
    for header in _objects(payload.get("headers")):
        name = str(header.get("name") or "").lower()
        if name and name not in found:
            found[name] = _line(_decoded(str(header.get("value") or "")))
    return found


class Gmail:
    """Reads the signed-in user's Gmail."""

    def __init__(self, session: GoogleSession) -> None:
        """Build the adapter; the kernel injects the session."""
        self._session = session

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
        params: dict[str, object] = {"userId": "me", "q": gmail_search(parsed, window), "maxResults": 100}
        ids: list[str] = []
        for items, _ in self._session.pages("Gmail", LIST, params, "the mail in that window", "messages"):
            ids += [str(item["id"]) for item in items if isinstance(item.get("id"), str) and _ID.fullmatch(item["id"])]
            if len(ids) > MAX_MESSAGES:
                msg = f"The window holds more than {MAX_MESSAGES} matching messages; narrow the window or the query."
                raise HeinzelError(msg)
        found: dict[str, MailMessage] = {}
        for message_id in dict.fromkeys(ids):
            message = self._message(self._get(message_id, "metadata"), names)
            if message is None or not window.since <= message.received_at < window.until:
                continue
            if parsed.matches(message):
                found[message.id] = message
        return sorted(found.values(), key=lambda m: (m.received_at, m.id))

    def read(self, message: str) -> MailContent:
        """The message `message` (a `MailMessage.id`) with its text and headers, address headers left out.

        Raises:
            HeinzelError: If the id is malformed, the sign-in fails, or the text passes `MAX_TEXT`.
        """
        data = self._get(message, "full")
        found = self._message(data, self._labels())
        if found is None:
            msg = f"Gmail's answer for message {message} lacks its id or time."
            raise HeinzelError(msg)
        payload = _object(data.get("payload"))
        return MailContent(message=found, text=_text(payload), headers=_headers(payload))

    def _get(self, message: str, format_: str) -> Json:
        if not _ID.fullmatch(message):
            msg = f"{clean_text(message)[:100]!r} is not a Gmail message id."
            raise HeinzelError(msg)
        params: dict[str, object] = {"userId": "me", "id": message, "format": format_}
        if format_ == "metadata":
            params["metadataHeaders"] = ["From", "To", "Cc", "Subject"]
        return self._session.get("Gmail", GET, params, f"message {message}")[0]

    def _labels(self) -> dict[str, str]:
        """Label names by id: system labels are their own names (`INBOX`), user labels have ids (`Label_12`)."""
        data = self._session.get("Gmail", LABELS, {"userId": "me"}, "the labels")[0]
        return {
            str(label["id"]): _line(label.get("name") or label["id"])
            for label in _objects(data.get("labels"))
            if isinstance(label.get("id"), str)
        }

    @staticmethod
    def _message(data: Json, names: dict[str, str]) -> MailMessage | None:
        message_id, millis = data.get("id"), data.get("internalDate")
        if not isinstance(message_id, str) or not _ID.fullmatch(message_id):
            return None
        try:
            received = datetime.fromtimestamp(int(str(millis)) / 1000, UTC)
        except (ValueError, OverflowError, OSError):
            return None
        headers = _headers(_object(data.get("payload")))
        senders = _people(headers.get("from", ""))
        recipients = _people(", ".join(h for h in (headers.get("to", ""), headers.get("cc", "")) if h))
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
            subject=headers.get("subject", ""),
            received_at=received,
            labels=labels,
            url=f"https://mail.google.com/mail/u/0/#all/{message_id}",
        )
