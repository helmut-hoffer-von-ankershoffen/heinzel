"""The one place that sends HTTP requests, with urllib from the standard library, and that waits.

Tests replace it through the kernel (`kernel.bind(Transport, fake)`), so nothing else in the plugin touches the
network or the clock. A token in the headers never leaves the origin of the URL asked for: redirects elsewhere are
refused.
"""

import http.client
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from email.message import Message
from typing import Any, override
from urllib.parse import urlsplit

from heinzel import HeinzelError

MAX_BYTES = 5_000_000
"""Largest answer read; a page of 200 messages, or of 200 channels, stays well below it."""


@dataclass(frozen=True, slots=True)
class Response:
    """An HTTP answer: status, headers with lower-case names, and at most `MAX_BYTES` + 1 of the body."""

    status: int
    headers: Mapping[str, str]
    body: bytes


def origin(url: str) -> str:
    """`scheme://host[:port]` of a URL."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


class _SameOriginRedirects(urllib.request.HTTPRedirectHandler):
    """Follows redirects within one origin only.

    urllib's default handler copies the Authorization header to any redirect target, another host or plain http. A
    redirect elsewhere is refused, which surfaces as the redirect's own status.
    """

    def __init__(self, allowed: str) -> None:
        self._allowed = allowed

    @override
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        if origin(newurl) != self._allowed:
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _headers(message: Message) -> dict[str, str]:
    return {name.lower(): value for name, value in message.items()}


class Transport:
    """Sends requests over HTTPS and returns every answer, error statuses included, as a `Response`."""

    def send(self, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float) -> Response:
        """GET `url`, or POST `body` to it when given, with `headers`, following redirects within its origin.

        Raises:
            OSError: If the host cannot be reached, the connection breaks or the answer doesn't arrive in time.
            HeinzelError: If http.client refuses the request (a header or the URL holds characters HTTP forbids);
                never with the header or URL, which may hold the token.
        """
        opener = urllib.request.build_opener(_SameOriginRedirects(origin(url)))
        request = urllib.request.Request(url, data=body, headers=dict(headers))  # noqa: S310 - https, checked by the settings
        try:
            with opener.open(request, timeout=timeout) as answer:
                return Response(answer.status, _headers(answer.headers), answer.read(MAX_BYTES + 1))
        except urllib.error.HTTPError as exc:  # an error status is an answer, judged by the caller
            with exc:
                return Response(exc.code, _headers(exc.headers), exc.read(MAX_BYTES + 1))
        except http.client.HTTPException as exc:  # e.g. a truncated answer: as unreachable as a refused connection
            raise OSError(exc) from exc
        except ValueError as exc:  # UnicodeError included; http.client's message quotes the offending header value
            msg = (
                f"The request to {origin(url)} is not valid HTTP ({type(exc).__name__}): a header or the URL holds "
                "characters HTTP forbids. Store the Slack token again with `hz system credential set slack-token`."
            )
            raise HeinzelError(msg) from None

    def sleep(self, seconds: float) -> None:
        """Wait `seconds`, as Slack's `Retry-After` asks before a rate-limited request is sent again."""
        time.sleep(seconds)
