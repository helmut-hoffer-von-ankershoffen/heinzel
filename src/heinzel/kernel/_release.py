"""The latest release of Heinzelmännchen, asked of the package source the installation came from (ADR 0027).

Asking is an ordinary call to the package source, made only when someone runs `hz system release` (later also by a
daemon job): a GET to the GitHub releases API with a `heinzel/<version>` User-Agent and, if one is configured (also
from GH_TOKEN or GITHUB_TOKEN), the user's GitHub token; nothing else. The answer is cached, so `hz --version` and
`hz system info` can say that a newer release exists without any network access of their own.
"""

import http.client
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, override
from urllib.parse import urlsplit

from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, Field, SecretStr

from heinzel.kernel._installation import CORE_DISTRIBUTION
from heinzel.kernel._settings import Settings

GITHUB_API = "https://api.github.com"
DEFAULT_REPOSITORY = "helmut-hoffer-von-ankershoffen/heinzel"
"""Where releases come from when the installation doesn't say, e.g. in a development checkout."""
TIMEOUT = 5
"""Seconds to wait for the package source before giving up with a warning."""
CACHE_FILENAME = "release.json"
CI_VARIABLES = ("CI", "CONTINUOUS_INTEGRATION", "BUILD_NUMBER", "GITHUB_ACTIONS", "GITLAB_CI", "TF_BUILD")
"""Environment variables CI systems set; no hint is shown when one of them is."""

MAX_BYTES = 1_000_000
"""Largest answer read from GitHub; a release is a few kilobytes."""
GITHUB_RELEASES = "https://github.com/"
_SEGMENT = re.compile(r"[A-Za-z0-9_.-]+")

Fetch = Callable[[str, SecretStr | None, float], Any]


class _GitHubApiRedirects(urllib.request.HTTPRedirectHandler):
    """Follows redirects within api.github.com only, so the token never goes anywhere else.

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


def installed_version() -> str:
    """The installed version of Heinzelmännchen."""
    try:
        return version(CORE_DISTRIBUTION)
    except PackageNotFoundError:  # pragma: no cover - only when importing from a source tree without installing
        return "0.0.0"


def github_get_json(url: str, token: SecretStr | None, timeout: float) -> Any:  # noqa: ANN401 - free-form JSON
    """GET a GitHub API URL and parse the JSON answer; the token, if any, goes to api.github.com only.

    The single way Heinzelmännchen talks to GitHub, and the seam tests fake.

    Raises:
        OSError: If GitHub cannot be reached or answers with an error, e.g. when rate-limited.
        ValueError: If the URL is not on api.github.com, or the answer is not JSON.
    """
    if not url.startswith(f"{GITHUB_API}/"):
        msg = f"not a GitHub API URL: {url}"
        raise ValueError(msg)
    headers = {"Accept": "application/vnd.github+json", "User-Agent": f"heinzel/{installed_version()}"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token.get_secret_value()}"
    body = _open(urllib.request.Request(url, headers=headers), timeout)  # noqa: S310 - https, checked above
    if len(body) > MAX_BYTES:
        msg = f"the answer is larger than {MAX_BYTES} bytes"
        raise ValueError(msg)
    return json.loads(body)


class ReleaseCheck(BaseModel):
    """The installed version, the latest release and whether updating would bring something new."""

    current: str = Field(description="Installed Heinzelmännchen version.")
    latest: str | None = Field(description="Version of the latest release; none if the source couldn't be asked.")
    update_available: bool = Field(description="Whether the latest release is newer than the installed version.")
    url: str | None = Field(description="Release notes of the latest release.")
    source: str = Field(description="Where the latest release was asked for.")
    warnings: list[str] = Field(description="Why the source could not be asked, e.g. offline or rate-limited.")


def repository_of(requirement: str | None) -> str:
    """`owner/name` of the GitHub repository Heinzelmännchen was installed from; the default one otherwise."""
    if requirement is None or "git+" not in requirement:
        return DEFAULT_REPOSITORY
    parts = urlsplit(requirement.split("git+", 1)[1].strip())
    path = parts.path.split("@", 1)[0].strip("/").removesuffix(".git").split("/")
    if parts.hostname != "github.com" or len(path) != 2:  # noqa: PLR2004 - owner and name
        return DEFAULT_REPOSITORY
    if not all(_SEGMENT.fullmatch(segment) and segment not in {".", ".."} for segment in path):
        return DEFAULT_REPOSITORY
    return "/".join(path)


def check_for_update(
    settings: Settings, requirement: str | None, *, fetch: Fetch | None = None, current: str | None = None
) -> ReleaseCheck:
    """Ask the package source for the latest release and cache the answer; offline or rate-limited is a warning.

    Args:
        settings: Kernel settings: the cache directory and the GitHub token.
        requirement: The requirement Heinzelmännchen was installed from, as in the uv tool receipt.
        fetch: Gets JSON from a URL; defaults to `github_get_json`, looked up at call time so tests can fake it.
        current: The installed version; defaults to the real one.
    """
    current = current or installed_version()
    source = f"{GITHUB_API}/repos/{repository_of(requirement)}/releases/latest"
    try:
        answer = (fetch or github_get_json)(source, settings.github_token, TIMEOUT)
        latest = str(Version(str(answer["tag_name"]).removeprefix("v")))
        # Only a link to github.com is shown; anything else in the answer stays out of the terminal.
        url = str(answer["html_url"]) if str(answer["html_url"]).startswith(GITHUB_RELEASES) else None
    # Isolation boundary: an unreachable, rate-limited or odd package source is a warning, never a crash.
    except (OSError, ValueError, KeyError, TypeError) as exc:  # InvalidVersion is a ValueError
        # An HTTPError reads best whole (it carries the status, e.g. 403 when rate-limited); other URLErrors by reason.
        reason: object = exc
        if isinstance(exc, urllib.error.URLError) and not isinstance(exc, urllib.error.HTTPError):
            reason = exc.reason
        return ReleaseCheck(
            current=current,
            latest=None,
            update_available=False,
            url=None,
            source=source,
            warnings=[f"Could not ask {source} for the latest release ({reason})."],
        )
    _write_cache(settings.cache_dir, {"latest": latest, "checked_at": datetime.now(UTC).isoformat()})
    return ReleaseCheck(
        current=current,
        latest=latest,
        update_available=_newer(latest, current),
        url=url,
        source=source,
        warnings=[],
    )


def update_hint(
    settings: Settings,
    *,
    current: str | None = None,
    isatty: Callable[[], bool] | None = None,
    environ: Mapping[str, str] = os.environ,
) -> str | None:
    """A one-line hint that a newer release is available, from the cache only; never any network access.

    None when the `update_check` setting is off, stdout is not a terminal, a CI variable is set, nothing is cached
    or the cached release is not newer than the installed one. `isatty` defaults to whether stdout is a terminal,
    asked at call time so redirected output counts.
    """
    in_ci = any(environ.get(name) for name in CI_VARIABLES)
    if not settings.update_check or in_ci or not (isatty or sys.stdout.isatty)():
        return None
    cached = _read_cache(settings.cache_dir)
    latest = cached.get("latest")
    if not isinstance(latest, str) or not _newer(latest, current or installed_version()):
        return None
    return f"v{latest} is available: hz system update"


def _newer(latest: str, current: str) -> bool:
    try:
        return Version(latest) > Version(current)
    except InvalidVersion:
        return False


def _read_cache(cache_dir: Path) -> dict[str, Any]:
    try:
        data = json.loads((cache_dir / CACHE_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # missing or corrupt: a cache can always be lost
        return {}
    return data if isinstance(data, dict) else {}  # pyright: ignore[reportUnknownVariableType] - free-form JSON


def _write_cache(cache_dir: Path, data: Mapping[str, str]) -> None:
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / CACHE_FILENAME).write_text(json.dumps(data), encoding="utf-8")
    except OSError:  # an unwritable cache only costs the hint, not the answer
        return
