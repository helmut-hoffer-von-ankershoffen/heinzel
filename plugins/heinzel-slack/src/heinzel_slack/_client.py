"""A small client for Slack's Web API: authentication, cursors, rate limits, budgets and errors people can act on.

Every failure becomes a `HeinzelError` that says what happened and what to do. Messages never contain the token:
headers are never part of a message, the token is refused before any request unless it is one line of printable
ASCII, and whatever Slack says is cleaned, redacted and only then cut.
"""

import json
import re
from collections.abc import Iterator, Mapping
from importlib.metadata import PackageNotFoundError, version
from typing import Any, cast
from urllib.parse import urlencode, urlsplit

from heinzel import Credentials, HeinzelError, clean_text
from heinzel_slack._models import READ_SCOPES, SlackSettings, TokenKind
from heinzel_slack._module import TOKEN
from heinzel_slack._transport import MAX_BYTES, Response, Transport, origin

TOKEN_FIX = "hz system credential set slack-token"  # noqa: S105 - a command, not a secret
MAX_PAGES = 100
"""Pages followed for one list before asking for a narrower window."""
MAX_REQUESTS = 600
"""Requests one call of the adapter may send; a digest of a busy channel with many threads stays well below it."""
MAX_TOTAL_BYTES = 50_000_000
"""Bytes one call of the adapter may read, across all its requests."""
MAX_RETRIES = 3
"""Times a rate-limited request is sent again, after waiting as long as Slack's `Retry-After` says."""
MAX_WAIT = 60
"""Longest wait honoured for one rate limit, in seconds; a longer one fails with the time to retry."""
DEFAULT_WAIT = 5
"""Seconds waited when a rate limit comes without a usable `Retry-After`."""
MAX_SAID = 120
"""Characters of Slack's own words shown, cut after cleaning and redaction so no part of a secret survives the cut."""
_CODE = re.compile(r"^[a-z0-9_.]{1,64}$")
_SCOPES = re.compile(r"^[a-z0-9_.:,]{1,200}$")
_PREFIXES: Mapping[TokenKind, tuple[str, ...]] = {"user": ("xoxp-", "xoxe.xoxp-"), "bot": ("xoxb-", "xoxe.xoxb-")}
_REJECTED = frozenset(
    {"invalid_auth", "not_authed", "token_revoked", "token_expired", "account_inactive", "no_permission"}
)


class SlackError(HeinzelError):
    """Slack answered with `ok: false`; `code` is its error code, such as `channel_not_found`."""

    def __init__(self, message: str, code: str) -> None:
        """An error for people, with Slack's `code` for callers that tolerate some."""
        super().__init__(message)
        self.code = code


def _user_agent() -> str:
    try:
        return f"heinzel-slack/{version('heinzel-slack')}"
    except PackageNotFoundError:  # pragma: no cover - only when running from an uninstalled source tree
        return "heinzel-slack"


class SlackClient:
    """Calls one Slack Web API with one token, within a request and byte budget per adapter call."""

    def __init__(self, api_url: str, token: str, transport: Transport, timeout: float) -> None:
        """Talk to `api_url` (no trailing slash) with `token` through `transport`; `token` is already checked."""
        self._api_url = api_url
        self._token = token
        self._transport = transport
        self._timeout = timeout
        self._requests = 0
        self._bytes = 0

    def call(self, method: str, params: Mapping[str, str], subject: str) -> dict[str, Any]:
        """GET a Web API `method` such as `conversations.history` with `params`; the answer, which said `ok`.

        Args:
            method: The Web API method.
            params: Query parameters.
            subject: What was asked for, for messages, e.g. `channel C0123456789`.

        Raises:
            SlackError: If Slack answers with `ok: false`, with its error code.
            HeinzelError: If Slack can't be reached, refuses, rate-limits too long, answers unexpectedly, or the
                call's budget of requests or bytes is spent.
        """
        return self._send(method, f"{self._api_url}/{method}?{urlencode(params)}", None, subject)

    def post(self, method: str, payload: Mapping[str, object], subject: str) -> dict[str, Any]:
        """POST `payload` as JSON to a Web API `method` such as `chat.postMessage`; the answer, which said `ok`.

        Raises:
            HeinzelError: As `call` does.
        """
        body = json.dumps(payload).encode()
        return self._send(method, f"{self._api_url}/{method}", body, subject)

    def pages(
        self, method: str, params: Mapping[str, str], key: str, subject: str, too_many: str
    ) -> Iterator[list[dict[str, Any]]]:
        """Each page of a cursor-paged method, the list under `key` of every answer.

        Stop iterating early to stop requesting pages. `too_many` is the advice given after `MAX_PAGES` pages.

        Yields:
            The items of one page.

        Raises:
            HeinzelError: As `call` does, if a page lacks the list, Slack repeats a cursor, or there are more than
                `MAX_PAGES` pages.
        """
        query = dict(params)
        seen: set[str] = set()
        for _ in range(MAX_PAGES):
            page = self.call(method, query, subject)
            items = page.get(key)
            if not isinstance(items, list) or not all(isinstance(i, dict) for i in cast("list[object]", items)):
                msg = f"Slack's answer to {method} for {subject} lacks a list of {key}; is slack.api_url Slack's API?"
                raise HeinzelError(msg)
            yield cast("list[dict[str, Any]]", items)  # checked above
            metadata = page.get("response_metadata")
            cursor = cast("dict[str, Any]", metadata).get("next_cursor") if isinstance(metadata, dict) else None
            if not isinstance(cursor, str) or not cursor:
                return
            if cursor in seen:
                msg = f"Slack repeated a page of {method} for {subject}; stopping instead of looping. Try again."
                raise HeinzelError(msg)
            seen.add(cursor)
            query["cursor"] = cursor
        msg = f"{method} for {subject} has more than {MAX_PAGES} pages; {too_many}."
        raise HeinzelError(msg)

    def _send(self, method: str, url: str, body: bytes | None, subject: str) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._token}", "User-Agent": _user_agent()}
        if body is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        for attempt in range(MAX_RETRIES + 1):
            response = self._exchange(method, url, headers, body)
            if response.status != 429:  # noqa: PLR2004 - Too Many Requests
                return self._answer(method, subject, response)
            wait = _retry_after(response.headers)
            if wait > MAX_WAIT or attempt == MAX_RETRIES:
                msg = f"Slack's rate limit for {method} is reached ({subject}): retry after {wait} seconds."
                raise HeinzelError(msg)
            self._transport.sleep(wait)
        raise AssertionError  # pragma: no cover - the loop returns or raises

    def _exchange(self, method: str, url: str, headers: Mapping[str, str], body: bytes | None) -> Response:
        if self._requests >= MAX_REQUESTS or self._bytes > MAX_TOTAL_BYTES:
            msg = (
                f"This read needs more than {MAX_REQUESTS} requests or {MAX_TOTAL_BYTES} bytes from Slack; ask for a "
                "shorter window, or lower slack.thread_lookback_days."
            )
            raise HeinzelError(msg)
        self._requests += 1
        try:
            response = self._transport.send(url, headers, body, self._timeout)
        except OSError as exc:
            msg = f"Could not reach {origin(url)} for {method}: {_why(exc)}. Check the network, or slack.api_url."
            raise HeinzelError(msg) from None
        self._bytes += len(response.body)
        if len(response.body) > MAX_BYTES:
            msg = f"Slack's answer to {method} is larger than {MAX_BYTES} bytes."
            raise HeinzelError(msg)
        return response

    def _answer(self, method: str, subject: str, response: Response) -> dict[str, Any]:
        if 300 <= response.status < 400:  # noqa: PLR2004 - a redirect Transport refused: it leaves the API's origin
            where = _site(response.headers.get("location", ""))
            msg = (
                f"Slack redirected {method} to {where}, away from {origin(self._api_url)}; refused, as redirects never "
                "leave the API's origin. Check slack.api_url."
            )
            raise HeinzelError(msg)
        if response.status != 200:  # noqa: PLR2004 - the Web API answers 200 OK, also for its own errors
            msg = f"Slack answered {method} for {subject} with HTTP {response.status}; try again later."
            raise HeinzelError(msg)
        try:
            data = json.loads(response.body)
        except ValueError:
            data = None
        if not isinstance(data, dict):
            msg = f"Slack's answer to {method} is not a JSON object; is slack.api_url Slack's Web API?"
            raise HeinzelError(msg)
        answer = cast("dict[str, Any]", data)
        if answer.get("ok") is not True:
            code = _code(answer.get("error"))
            raise SlackError(self._problem(method, subject, code, answer), code)
        return answer

    def _problem(self, method: str, subject: str, code: str, answer: dict[str, Any]) -> str:  # noqa: PLR0911 - one per error
        """What went wrong, for a person: Slack's error code, never the token, never free text from the answer."""
        if code in _REJECTED:
            return f"Slack rejected the token ({code}); store a new one with `{TOKEN_FIX}`."
        if code == "missing_scope":
            needed = str(answer.get("needed") or "")
            scope = needed if _SCOPES.fullmatch(needed) else "one this method needs"
            return (
                f"The Slack token lacks the scope {scope} for {method}: add it to your Slack app's user token scopes "
                f"(api.slack.com/apps, OAuth & Permissions), reinstall the app and store the new token with "
                f"`{TOKEN_FIX}`."
            )
        if code == "not_allowed_token_type":
            return f"Slack doesn't accept this kind of token for {method}; check slack.token_kind and the token."
        if code in {"channel_not_found", "not_in_channel"}:
            return (
                f"Slack has no {subject} the token can see ({code}): check the id or #name, join the channel, or for a "
                "bot token add the bot to it."
            )
        if code == "is_archived":
            return f"The {subject} is archived; Slack accepts no posts there."
        if code == "thread_not_found":
            return f"Slack has no such thread in {subject}; check the thread id (a message ts)."
        if code in {"msg_too_long", "no_text"}:
            return f"Slack refused the text for {subject} ({code}); post a shorter, non-empty text."
        if code == "ratelimited":
            return f"Slack's rate limit for {method} is reached ({subject}); try again in a minute."
        return f"Slack answered {method} for {subject} with the error {self._said(code)}."

    def _said(self, text: str) -> str:
        return clean_text(text).replace(self._token, "***")[:MAX_SAID]  # redact whole, then cut


def connect(settings: SlackSettings, credentials: Credentials, transport: Transport) -> SlackClient:
    """A client for the configured API with the stored token.

    Raises:
        HeinzelError: Before any request, if no token is stored, or the stored one isn't one line of printable ASCII
            or not the configured kind of token; never with the value.
    """
    token = _checked(credentials.get(TOKEN), settings.token_kind)
    return SlackClient(settings.api_url, token, transport, settings.timeout)


def _checked(token: str | None, kind: TokenKind) -> str:
    """The token without surrounding whitespace, refused unless it is one line of printable ASCII of the right kind.

    A pasted line break would otherwise reach http.client, whose error message quotes the header with the token.

    Raises:
        HeinzelError: If there is no token, or it is malformed; never with the value.
    """
    stripped = (token or "").strip()
    if not stripped:
        msg = (
            f"The Slack adapter needs a token; there is no anonymous access. Store a {kind} token with "
            f"`{TOKEN_FIX}` (scopes for reading: {', '.join(READ_SCOPES)})."
        )
        raise HeinzelError(msg)
    if not (stripped.isascii() and stripped.isprintable()) or " " in stripped:
        msg = f"The stored Slack token is not one line of printable ASCII; store it again with `{TOKEN_FIX}`."
        raise HeinzelError(msg)
    prefixes = _PREFIXES[kind]
    if not stripped.startswith(prefixes):
        msg = (
            f"The stored Slack token is not a {kind} token ({prefixes[0]}...), which slack.token_kind asks for; store "
            f"one with `{TOKEN_FIX}`, or change slack.token_kind."
        )
        raise HeinzelError(msg)
    return stripped


def _code(error: object) -> str:
    """Slack's error code if it looks like one (`channel_not_found`); never other text from the answer."""
    text = str(error) if error is not None else ""
    return text if _CODE.fullmatch(text) else "unknown_error"


def _retry_after(headers: Mapping[str, str]) -> int:
    value = headers.get("retry-after", "").strip()
    return int(value) if value.isdigit() else DEFAULT_WAIT


def _why(exc: OSError) -> str:
    """The original error's type and text: urllib wraps it as `reason`, Transport chains it as the cause."""
    reason: object = getattr(exc, "reason", None)
    if isinstance(reason, str):
        return f"{type(exc).__name__}: {clean_text(reason)}"
    cause = reason if isinstance(reason, BaseException) else exc.__cause__ or exc
    text = clean_text(str(cause))
    return f"{type(cause).__name__}: {text}" if text else type(cause).__name__


def _site(url: str) -> str:
    """`scheme://host[:port]` of a URL, without any user or password in it; for messages."""
    parts = urlsplit(url)
    try:
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        port = ""
    if not (parts.scheme and parts.hostname):
        return "another address"
    return clean_text(f"{parts.scheme}://{parts.hostname}{port}")
