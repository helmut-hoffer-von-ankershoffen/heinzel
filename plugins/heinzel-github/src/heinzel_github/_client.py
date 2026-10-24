"""GitHub's REST API through `gh api`: pagination, schema checks, and errors people can act on.

Every failure becomes a `HeinzelError` that says what happened and what to do. `gh`'s messages are data from outside
the process: they are redacted of anything shaped like a GitHub token first, then stripped of control and
bidirectional characters, and only then cut to length, so a cut can never leave half a token behind.
"""

import json
import re
from collections.abc import Iterator, Mapping
from typing import Any, cast

from heinzel import HeinzelError, clean_text
from heinzel_github._gh import MAX_BYTES, Gh, GhResult, login_fix

API_VERSION = "2022-11-28"
"""The REST API version requested, so GitHub doesn't change answers under us."""
PER_PAGE = 100
MAX_PAGES = 100
"""Pages read for one call (10,000 items at 100 a page) before asking for a narrower window."""
MAX_SAID = 300
"""Characters of GitHub's or `gh`'s own error message shown, after redaction."""
NOT_LOGGED_IN = 4
"""`gh`'s exit code when it has no login for the host."""
_STATUS = re.compile(r"\(HTTP (\d{3})\)")
# Prefixed tokens anywhere, also glued to other text; a classic 40-hex token only as a word of its own.
_TOKEN = re.compile(r"(?:gh[opsur]_|github_pat_)[A-Za-z0-9_]{16,}|\b[0-9a-f]{40}\b")


def redact(text: str) -> str:
    """`text` with anything shaped like a GitHub token (`ghp_…`, `github_pat_…`, 40 hex digits) replaced by `***`."""
    return _TOKEN.sub("***", text)


def said(text: str) -> str:
    """A message from outside, safe to show: redacted, cleaned, then cut to `MAX_SAID` characters."""
    return clean_text(redact(text)).strip()[:MAX_SAID] or "no details"


class GhClient:
    """GETs JSON from GitHub's REST API with `gh api`, as the account `gh` is logged in with on one host.

    That login usually may write too; this client only ever sends GET, to paths the adapter builds under `repos/`.
    """

    def __init__(self, gh: Gh, host: str, timeout: float) -> None:
        """Read from `host` through `gh`, giving each call `timeout` seconds."""
        self._gh = gh
        self._host = host
        self._timeout = timeout

    def pages(
        self, path: str, params: Mapping[str, str], *, too_many: str = "ask for a shorter window"
    ) -> Iterator[list[dict[str, Any]]]:
        """Each page of a list endpoint such as `repos/acme/widgets/pulls`, 100 items a page, until a short page.

        Stop iterating early to stop asking for pages. `too_many` is the advice given when there are more than
        `MAX_PAGES` full pages; what helps depends on which bound of the window the paging depends on.

        Yields:
            The items of one page.

        Raises:
            HeinzelError: If `gh` is missing or not logged in, GitHub refuses, rate-limits or answers unexpectedly,
                or there are more than `MAX_PAGES` pages.
        """
        for page in range(1, MAX_PAGES + 1):
            items = self._list(path, {**params, "per_page": str(PER_PAGE), "page": str(page)})
            yield items
            if len(items) < PER_PAGE:
                return
        msg = f"{path} has {MAX_PAGES} or more full pages of results; {too_many}."
        raise HeinzelError(msg)

    def _list(self, path: str, params: Mapping[str, str]) -> list[dict[str, Any]]:
        args = [
            "api",
            "--hostname",
            self._host,
            "--method",
            "GET",
            "--header",
            "Accept: application/vnd.github+json",
            "--header",
            f"X-GitHub-Api-Version: {API_VERSION}",
            path,
        ]
        for key, value in params.items():
            args += ["--raw-field", f"{key}={value}"]  # raw: a value is never read as @file or a number
        result = self._gh.run(args, host=self._host, timeout=self._timeout)
        if result.returncode is None:
            msg = (
                f"`gh api {path}` did not finish within {self._timeout:g} seconds; check the network, or allow more "
                "time with `hz system config set github.timeout 60`."
            )
            raise HeinzelError(msg)
        if result.truncated:
            msg = f"GitHub's answer for {path} is larger than {MAX_BYTES} bytes; ask for a shorter window."
            raise HeinzelError(msg)
        if result.returncode != 0:
            raise HeinzelError(self._problem(path, result))
        items = _json(result.stdout, path)
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in cast("list[object]", items)):
            msg = (
                f"GitHub answered {path} with something other than a list of objects; is {self._host} a GitHub server?"
            )
            raise HeinzelError(msg)
        return cast("list[dict[str, Any]]", items)  # checked above

    def _problem(self, path: str, result: GhResult) -> str:  # noqa: PLR0911 - one message per kind of failure
        """What went wrong, for a person: GitHub's or `gh`'s words, redacted and cleaned, plus what to do."""
        host, stderr = self._host, result.stderr
        found = _STATUS.search(stderr)
        status = int(found.group(1)) if found else None
        text = said(_message(result.stdout) or _first_line(stderr))
        # gh adds "Try authenticating with: gh auth login" to a 401 too: the status decides first.
        if status == 401:  # noqa: PLR2004 - Unauthorized
            return (
                f"GitHub rejected gh's login for {host} (401: {text}); log in again with `{login_fix(host)}`, "
                "or replace GH_TOKEN or GH_ENTERPRISE_TOKEN if one is set, as it takes precedence."
            )
        if status is None and (result.returncode == NOT_LOGGED_IN or "gh auth login" in stderr):
            return f"`gh` is not logged in to {host}; run `{login_fix(host)}`, then try again."
        if status == 429 or (status == 403 and "rate limit" in text.lower()):  # noqa: PLR2004 - Too Many Requests
            return (
                f"GitHub's rate limit is reached for {path} ({status}: {text}); wait a few minutes and try again. "
                f"`gh api rate_limit --hostname {host}` shows when it resets."
            )
        account = f"the account gh is logged in with (`gh auth status --hostname {host}`)"
        if status == 404:  # noqa: PLR2004 - Not Found
            return f"GitHub has no {path}: check the owner/repo spelling, and that {account} can see it."
        if status == 403:  # noqa: PLR2004 - Forbidden
            return f"GitHub refused {path} (403: {text}); {account} needs read access to its pull requests and issues."
        if status is not None:
            return f"GitHub answered {path} with {status}: {text}."
        return (
            f"`gh api {path}` failed (exit code {result.returncode}): {text}. "
            f"Check the network and `gh auth status --hostname {host}`."
        )


def _json(body: bytes, path: str) -> Any:  # noqa: ANN401 - free-form JSON
    try:
        return json.loads(body)
    except ValueError:
        msg = f"`gh api {path}` printed something that is not JSON; is `gh` the GitHub CLI?"
        raise HeinzelError(msg) from None


def _message(body: bytes) -> str:
    """GitHub's `message` from an error body `gh` printed, or ""; never the raw body, which could be anything."""
    try:
        data = json.loads(body)
    except ValueError:
        return ""
    message = cast("dict[str, Any]", data).get("message") if isinstance(data, dict) else None
    return message if isinstance(message, str) else ""


def _first_line(text: str) -> str:
    return next((line for line in text.splitlines() if line.strip()), "")
