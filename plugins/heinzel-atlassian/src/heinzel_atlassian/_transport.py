"""The one place that sends HTTP requests: a GET with urllib from the standard library, nothing else.

Tests replace it through the kernel (`kernel.bind(Transport, fake)`), so nothing else in the plugin touches the
network. A token in the headers never leaves the origin of the URL asked for: redirects elsewhere are refused.
"""

import http.client
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from email.message import Message
from typing import Any, override
from urllib.parse import urlsplit

MAX_BYTES = 10_000_000
"""Largest answer read; a page of 100 issues with five fields, or one long Confluence page, stays well below it."""


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
    """Sends GET requests over HTTPS and returns every answer, error statuses included, as a `Response`."""

    def get(self, url: str, headers: Mapping[str, str], timeout: float) -> Response:
        """GET `url` with `headers`, following redirects within its origin.

        Raises:
            OSError: If the host cannot be reached, the connection breaks or the answer doesn't arrive in time.
        """
        opener = urllib.request.build_opener(_SameOriginRedirects(origin(url)))
        request = urllib.request.Request(url, headers=dict(headers))  # noqa: S310 - https, checked by the settings
        try:
            with opener.open(request, timeout=timeout) as answer:
                return Response(answer.status, _headers(answer.headers), answer.read(MAX_BYTES + 1))
        except urllib.error.HTTPError as exc:  # an error status is an answer, judged by the caller
            with exc:
                return Response(exc.code, _headers(exc.headers), exc.read(MAX_BYTES + 1))
        except http.client.HTTPException as exc:  # e.g. a truncated answer: as unreachable as a refused connection
            raise OSError(exc) from exc
