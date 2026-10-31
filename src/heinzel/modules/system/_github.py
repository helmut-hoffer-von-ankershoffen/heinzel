"""The one way the system module and its submodules talk to GitHub: a GET to api.github.com that returns JSON.

Shared by `hz system release` (never with a token, ADR 0031) and `hz system plugin search` (with the user's token, if
configured). The token, if any, never leaves api.github.com: redirects elsewhere are refused.
"""

import http.client
import json
import time
import urllib.error
import urllib.request
from typing import Any, override

from pydantic import SecretStr

from heinzel import __version__

GITHUB_API = "https://api.github.com"
MAX_BYTES = 1_000_000
"""Largest answer read from GitHub; a release or a page of search results is far smaller."""


_CONDITIONAL: dict[str, tuple[str, bytes]] = {}
"""The last ETag and body per request, so a repeat asks "changed?" and a 304 costs no rate limit."""
_CONDITIONAL_LIMIT = 256


class RateLimitedError(OSError):
    """GitHub refused a request because the caller's request limit is used up, until `reset` (epoch seconds)."""

    def __init__(self, reset: int | None, *, authenticated: bool) -> None:
        super().__init__("GitHub's request limit is used up")
        self.reset = reset
        self.authenticated = authenticated

    def message(self) -> str:
        """What a person should read: when it returns, and how to lift it."""
        when = time.strftime("%H:%M", time.localtime(self.reset)) if self.reset else "later"
        advice = (
            "Try again then."
            if self.authenticated
            else "Try again then, or set github_token (or sign in with `gh auth login`) for a much higher limit."
        )
        return f"GitHub's request limit is used up until {when}. {advice}"


def rate_limit_in(text: str) -> bool:
    """Whether a gh/GitHub error text says the request limit is used up."""
    lowered = text.lower()
    return "rate limit" in lowered or "secondary rate" in lowered or "abuse detection" in lowered


class _GitHubApiRedirects(urllib.request.HTTPRedirectHandler):
    """Follows redirects within api.github.com only.

    urllib's default handler copies the Authorization header to any redirect target, another host or plain http.
    A redirect elsewhere is refused, which surfaces as the redirect's HTTPError.
    """

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
        if not newurl.startswith(f"{GITHUB_API}/"):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Never follows a redirect: the answer is the redirect itself."""

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
        return None


REDIRECTS = frozenset({301, 302, 307, 308})


def _head(url: str, timeout: float) -> tuple[int, str]:
    """HEAD `url` without following redirects: its status and `Location`; the seam tests fake.

    Raises:
        OSError: If github.com cannot be reached or answers with an error other than a redirect.
    """
    request = urllib.request.Request(  # noqa: S310 - https, checked by the caller
        url, method="HEAD", headers={"User-Agent": f"heinzel/{__version__}"}
    )
    try:
        with urllib.request.build_opener(_NoRedirects).open(request, timeout=timeout) as response:
            return response.status, ""
    except urllib.error.HTTPError as error:
        location = str(error.headers.get("Location", "")) if error.headers else ""
        error.close()
        if error.code in REDIRECTS:
            return error.code, location
        raise OSError(error) from error
    except http.client.HTTPException as exc:
        raise OSError(exc) from exc


def latest_release_url(repository: str, timeout: float) -> str:
    """The page of the repository's latest published release, from github.com's own redirect.

    `github.com/<repository>/releases/latest` redirects to the tag's page; asking it spends none of the anonymous REST
    API's 60 requests an hour per address, which a shared network or a busy day exhausts (the release check then
    never learns that a newer release exists). It sends only the `heinzel/<version>` User-Agent, no token.

    Raises:
        OSError: If github.com cannot be reached or answers with an error.
        ValueError: If the answer is no redirect to a tag page of this repository.
    """
    prefix = f"https://github.com/{repository}/releases/tag/"
    status, location = _head(f"https://github.com/{repository}/releases/latest", timeout)
    tag = location[len(prefix) :]
    tag_page = location.lower().startswith(prefix.lower()) and tag != "" and not set(tag) & set("/?#")
    if status not in REDIRECTS or not tag_page:
        msg = "the latest release did not redirect to a tag page of the repository"
        raise ValueError(msg)
    return location


def _open(request: urllib.request.Request, timeout: float) -> bytes:
    """Send a request to GitHub and read at most `MAX_BYTES` + 1 of the answer; the seam tests fake.

    Raises:
        RateLimitedError: If the request limit is used up.
        HTTPError: If GitHub answers with another error.
        OSError: If GitHub cannot be reached.
    """
    opener = urllib.request.build_opener(_GitHubApiRedirects)
    key = f"{request.full_url}\n{'auth' if request.has_header('Authorization') else 'anonymous'}"
    known = _CONDITIONAL.get(key)
    if known is not None:
        request.add_header("If-None-Match", known[0])
    try:
        with opener.open(request, timeout=timeout) as response:
            body: bytes = response.read(MAX_BYTES + 1)
            etag = getattr(response, "headers", {}).get("ETag")
            if etag and len(body) <= MAX_BYTES:
                if len(_CONDITIONAL) >= _CONDITIONAL_LIMIT:
                    _CONDITIONAL.pop(next(iter(_CONDITIONAL)))
                _CONDITIONAL[key] = (etag, body)
            return body
    except urllib.error.HTTPError as exc:
        if exc.code == http.HTTPStatus.NOT_MODIFIED and known is not None:
            exc.close()
            return known[1]
        headers = exc.headers
        remaining = headers.get("X-RateLimit-Remaining") if headers else None
        if exc.code == http.HTTPStatus.TOO_MANY_REQUESTS or (
            exc.code == http.HTTPStatus.FORBIDDEN and remaining == "0"
        ):
            reset = headers.get("X-RateLimit-Reset") if headers else None
            retry = headers.get("Retry-After") if headers else None
            when = (
                int(reset)
                if reset and reset.isdigit()
                else (int(time.time()) + int(retry) if retry and retry.isdigit() else None)
            )
            exc.close()
            raise RateLimitedError(when, authenticated=request.has_header("Authorization")) from None
        raise
    except http.client.HTTPException as exc:  # e.g. a truncated answer: as unreachable as a refused connection
        raise OSError(exc) from exc


def get_json(url: str, token: SecretStr | None, timeout: float) -> Any:  # noqa: ANN401 - free-form JSON
    """GET a GitHub API URL with a `heinzel/<version>` User-Agent and, if given, the token; parse the JSON answer.

    Raises:
        OSError: If GitHub cannot be reached or answers with an error, e.g. when rate-limited.
        ValueError: If the URL is not on api.github.com, or the answer is too large or not JSON.
    """
    if not url.startswith(f"{GITHUB_API}/"):
        msg = f"not a GitHub API URL: {url}"
        raise ValueError(msg)
    headers = {"Accept": "application/vnd.github+json", "User-Agent": f"heinzel/{__version__}"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token.get_secret_value()}"
    body = _open(urllib.request.Request(url, headers=headers), timeout)  # noqa: S310 - https, checked above
    if len(body) > MAX_BYTES:
        msg = f"the answer is larger than {MAX_BYTES} bytes"
        raise ValueError(msg)
    return json.loads(body)
