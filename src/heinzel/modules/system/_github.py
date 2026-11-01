"""The one way the system module and its submodules talk to GitHub: a GET to api.github.com that returns JSON.

Shared by `hz system release` (never with a token, ADR 0031) and `hz system plugin search` (with the user's token, if
configured). The token, if any, never leaves api.github.com: redirects elsewhere are refused.
"""

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, override

from pydantic import SecretStr

from heinzel import __version__

GITHUB_API = "https://api.github.com"
MAX_BYTES = 1_000_000
"""Largest answer read from GitHub; a release or a page of search results is far smaller."""


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
        OSError: If GitHub cannot be reached or answers with an error.
    """
    opener = urllib.request.build_opener(_GitHubApiRedirects)
    try:
        with opener.open(request, timeout=timeout) as response:
            body: bytes = response.read(MAX_BYTES + 1)
            return body
    except http.client.HTTPException as exc:  # e.g. a truncated answer: as unreachable as a refused connection
        raise OSError(exc) from exc


class _AssetRedirects(urllib.request.HTTPRedirectHandler):
    """A release asset redirects to GitHub's storage: follow only there, and only over HTTPS."""

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
        if not asset_host(newurl):
            raise urllib.error.HTTPError(req.full_url, code, "unexpected asset redirect", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def asset_host(url: str) -> bool:
    """Whether `url` is HTTPS on github.com or GitHub's release storage."""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    return parts.scheme == "https" and (host == "github.com" or host.endswith(".githubusercontent.com"))


def read_asset(url: str, timeout: float) -> bytes:
    """A public release asset, without a token; at most `MAX_BYTES` + 1.

    Raises:
        OSError: If GitHub cannot be reached, answers with an error or redirects elsewhere.
        ValueError: If `url` is not on github.com over HTTPS.
    """
    if not asset_host(url):
        msg = f"not a GitHub asset URL: {url}"
        raise ValueError(msg)
    request = urllib.request.Request(url, headers={"User-Agent": f"heinzel/{__version__}"})  # noqa: S310 - https, checked
    opener = urllib.request.build_opener(_AssetRedirects)
    try:
        with opener.open(request, timeout=timeout) as response:
            body: bytes = response.read(MAX_BYTES + 1)
            return body
    except http.client.HTTPException as exc:
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
