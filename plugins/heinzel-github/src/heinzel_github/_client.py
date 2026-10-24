"""A small client for GitHub's REST API: authentication, pagination, and errors people can act on.

Every failure becomes a `HeinzelError` that says what happened and what to do. Messages never contain the token:
headers are never part of a message, and GitHub's own message text is redacted in case it echoes the token.
"""

import json
import re
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Any, cast
from urllib.parse import urlencode, urlsplit

from heinzel import HeinzelError
from heinzel_github._transport import MAX_BYTES, Response, Transport, origin

API_VERSION = "2022-11-28"
"""The REST API version requested, so GitHub doesn't change answers under us."""
MAX_PAGES = 100
"""Pages followed for one call (10,000 items at 100 a page) before asking for a narrower window."""
TOKEN_FIX = "hz system credential set github-token --service github"  # noqa: S105 - a command, not a secret
MAX_SAID = 300
"""Characters of GitHub's own error message shown, after redaction."""
_NEXT = re.compile(r'<([^>]+)>\s*;\s*rel="next"')


def _user_agent() -> str:
    try:
        return f"heinzel-github/{version('heinzel-github')}"
    except PackageNotFoundError:  # pragma: no cover - only when running from an uninstalled source tree
        return "heinzel-github"


def next_link(header: str | None) -> str | None:
    """The `rel="next"` URL of a `Link` header, or None on the last page."""
    found = _NEXT.search(header or "")
    return found.group(1) if found else None


class GitHubClient:
    """GETs JSON from one GitHub REST API, with a token if there is one, anonymously otherwise."""

    def __init__(self, api_url: str, token: str | None, transport: Transport, timeout: float) -> None:
        """Talk to `api_url` (no trailing slash) through `transport`.

        Raises:
            HeinzelError: If the token isn't one line of printable ASCII once stripped; never with the value.
        """
        self._api_url = api_url
        self._token = _clean(token)
        self._transport = transport
        self._timeout = timeout

    def pages(
        self, path: str, params: Mapping[str, str], *, too_many: str = "ask for a shorter window"
    ) -> Iterator[list[dict[str, Any]]]:
        """Each page of a list endpoint such as `/repos/acme/widgets/pulls`, following `Link: rel="next"`.

        Stop iterating early to stop requesting pages. `too_many` is the advice given when there are more than
        `MAX_PAGES` pages; what helps depends on which bound of the window the paging depends on.

        Yields:
            The items of one page.

        Raises:
            HeinzelError: If GitHub can't be reached, refuses, rate-limits, answers unexpectedly, or there are more
                than `MAX_PAGES` pages.
        """
        url: str | None = f"{self._api_url}{path}?{urlencode(params)}"
        for _ in range(MAX_PAGES):
            if url is None:
                return
            response = self._get(url)
            items = _json(response.body)
            if not isinstance(items, list) or not all(isinstance(item, dict) for item in cast("list[object]", items)):
                msg = f"GitHub answered {path} with something other than a list; is {self._api_url} a GitHub API?"
                raise HeinzelError(msg)
            yield cast("list[dict[str, Any]]", items)  # checked above
            url = next_link(response.headers.get("link"))
            if url is not None and not url.startswith(f"{self._api_url}/"):
                # The token must not follow a link to another host.
                msg = f"GitHub's next page for {path} points away from {self._api_url}; refusing to follow it."
                raise HeinzelError(msg)
        if url is not None:
            msg = f"{path} has more than {MAX_PAGES} pages of results; {too_many}."
            raise HeinzelError(msg)

    def _get(self, url: str) -> Response:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": _user_agent(),
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            response = self._transport.get(url, headers, self._timeout)
        except OSError as exc:
            msg = f"Could not reach {origin(url)}: {_why(exc)}. Check the network, or [github] api_url."
            raise HeinzelError(msg) from None
        if len(response.body) > MAX_BYTES:
            msg = f"GitHub's answer for {urlsplit(url).path} is larger than {MAX_BYTES} bytes."
            raise HeinzelError(msg)
        if response.status != 200:  # noqa: PLR2004 - every list endpoint answers 200 OK
            raise HeinzelError(self._problem(urlsplit(url).path, response))
        return response

    def _problem(self, path: str, response: Response) -> str:
        """What went wrong, for a person: never the token, never a header value besides rate-limit times."""
        status, headers = response.status, response.headers
        said = self._redact(_message(response.body))[:MAX_SAID]  # redact the whole text, then cut
        if limited := self._rate_limited(path, status, headers, said):
            return limited
        if status == 401:  # noqa: PLR2004 - Unauthorized
            return f"GitHub rejected the token (401: {said}); replace it with `{TOKEN_FIX}`."
        if status == 404:  # noqa: PLR2004 - Not Found
            access = (
                "the token can't see it; grant it access to the repository"
                if self._token
                else f"private repositories need a token: `{TOKEN_FIX}`"
            )
            return f"GitHub has no {path}: check the owner/repo spelling, or {access}."
        if status == 403:  # noqa: PLR2004 - Forbidden
            need = (
                "the token needs read access to metadata, pull requests and issues"
                if self._token
                else f"no token is stored: `{TOKEN_FIX}`"
            )
            return f"GitHub refused {path} (403: {said}); {need}."
        if 300 <= status < 400:  # noqa: PLR2004 - a redirect Transport refused: it leaves the API's origin
            where = _site(headers.get("location", ""))
            return (
                f"GitHub redirected {path} to {where}, away from {origin(self._api_url)}; refused, as redirects never "
                "leave the API's origin. Check [github] api_url."
            )
        return f"GitHub answered {path} with {status}: {said}."

    def _rate_limited(self, path: str, status: int, headers: Mapping[str, str], said: str) -> str | None:
        """The message for a primary or secondary rate limit, or None if the answer is no rate limit."""
        exhausted = headers.get("x-ratelimit-remaining") == "0"
        # Secondary rate limits may come without these headers; GitHub's message then says "rate limit".
        signs = exhausted or "retry-after" in headers or "rate limit" in said.lower()
        if not (status == 429 or (status == 403 and signs)):  # noqa: PLR2004 - Too Many Requests, Forbidden
            return None
        if "retry-after" in headers:
            when = f"retry after {headers['retry-after']} seconds"
        elif exhausted:
            when = f"it resets at {_reset(headers.get('x-ratelimit-reset'))}"
        else:
            when = "wait at least a minute before trying again"
        more = "" if self._token else f"; anonymous requests get 60 an hour, store a token with `{TOKEN_FIX}`"
        return f"GitHub's rate limit is reached for {path}: {when}{more}."

    def _redact(self, text: str) -> str:
        return text.replace(self._token, "***") if self._token else text


def _clean(token: str | None) -> str | None:
    """The token without surrounding whitespace, or None; refused unless it is one line of printable ASCII.

    A pasted line break would otherwise reach http.client, whose error message quotes the header with the token.

    Raises:
        HeinzelError: If the stripped token holds a control or non-ASCII character; never with the value.
    """
    if token is None or not (stripped := token.strip()):
        return None
    if not (stripped.isascii() and stripped.isprintable()):
        msg = f"The stored GitHub token is not one line of printable ASCII; store it again with `{TOKEN_FIX}`."
        raise HeinzelError(msg)
    return stripped


def _why(exc: OSError) -> str:
    """The original error's type and text: urllib wraps it as `reason`, Transport chains it as the cause."""
    reason: object = getattr(exc, "reason", None)
    if isinstance(reason, str):
        return f"{type(exc).__name__}: {reason}"
    cause = reason if isinstance(reason, BaseException) else exc.__cause__ or exc
    text = str(cause)
    return f"{type(cause).__name__}: {text}" if text else type(cause).__name__


def _site(url: str) -> str:
    """`scheme://host[:port]` of a URL, without any user or password in it; for messages."""
    parts = urlsplit(url)
    try:
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        port = ""
    return f"{parts.scheme}://{parts.hostname}{port}" if parts.scheme and parts.hostname else "another address"


def _json(body: bytes) -> Any:  # noqa: ANN401 - free-form JSON
    try:
        return json.loads(body)
    except ValueError:
        msg = "GitHub's answer is not JSON; is [github] api_url a GitHub REST API?"
        raise HeinzelError(msg) from None


def _message(body: bytes) -> str:
    """GitHub's `message` from an error body, or a placeholder; never the raw body, which could be anything."""
    try:
        data = json.loads(body)
    except ValueError:
        return "no details"
    message = cast("dict[str, Any]", data).get("message") if isinstance(data, dict) else None
    return str(message) if message else "no details"


def _reset(epoch: str | None) -> str:
    try:
        return datetime.fromtimestamp(int(epoch or ""), UTC).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return "an unknown time"
