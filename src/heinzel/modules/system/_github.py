"""The one way the system module and its submodules talk to GitHub: a GET to api.github.com that returns JSON.

Shared by `hz system release` (never with a token, ADR 0031) and `hz system plugin search` (with the user's token, if
configured). The token, if any, never leaves api.github.com: redirects elsewhere are refused.
"""

import http.client
import json
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
