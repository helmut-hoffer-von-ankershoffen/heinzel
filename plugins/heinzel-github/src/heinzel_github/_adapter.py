"""The GitHub adapter of the `code-host` and `tracker` capabilities (ADR 0028), read mode only.

Every request goes through the GitHub CLI (`gh api`), which owns authentication: the adapter acts as the person
`gh` is logged in as, with no app or token of its own (ADR "Integrations go through vendor CLIs as an ordinary
employee"). Merged pull requests come from the pulls list, not the search API (ADR 0033):
`repos/{owner}/{repo}/pulls?state=closed&sort=updated&direction=desc`, kept when `merged_at` falls in the window,
paging stops at the first pull request updated before the window starts, because merging updates a pull request.
Issues come from `repos/{owner}/{repo}/issues?state=all&since=...&sort=updated&direction=asc`, without the pull
requests GitHub mixes into that list. Every string returned passes `clean_text`.
"""

import re
from datetime import UTC, datetime
from typing import Any, cast

from heinzel import HeinzelError, Issue, PullRequest, Window, clean_text
from heinzel_github._client import GhClient
from heinzel_github._gh import Gh
from heinzel_github._models import GithubSettings

_REPOSITORY = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")


class GitHub:
    """Reads merged pull requests and updated issues of a GitHub repository, given as `owner/repo`."""

    def __init__(self, settings: GithubSettings, gh: Gh) -> None:
        """Build the adapter; the kernel injects the settings and the `gh` runner."""
        self._settings = settings
        self._gh = gh

    def merged_pull_requests(self, repository: str, window: Window) -> list[PullRequest]:
        """Pull requests of `repository` merged within `window`, oldest first.

        Raises:
            HeinzelError: If the repository or window is malformed, or GitHub fails (see `GhClient.pages`).
        """
        path = f"repos/{_repository(repository)}/pulls"
        since, until = _bounds(window)
        params = {"state": "closed", "sort": "updated", "direction": "desc"}
        # Paging stops only on `since`: every pull request updated since then is read, also after `until`.
        too_many = (
            "paging covers every pull request updated since the window starts, "
            "so ask for a window that starts more recently"
        )
        found: dict[int, PullRequest] = {}  # by number: an update between two requests shifts the list
        for page in self._client().pages(path, params, too_many=too_many):
            for item in page:
                if _time(item, "updated_at") < since:
                    # Sorted by update, newest first, and merging updates a pull request: nothing older qualifies.
                    return _oldest_first(found)
                if item.get("merged_at") and since <= (merged := _time(item, "merged_at")) < until:
                    pull = _pull_request(repository, item, merged)
                    found.setdefault(pull.number, pull)
        return _oldest_first(found)

    def issues_updated(self, project: str, window: Window) -> list[Issue]:
        """Issues (not pull requests) of `project`, a repository as `owner/repo`, updated within `window`, oldest first.

        Raises:
            HeinzelError: If the repository or window is malformed, or GitHub fails (see `GhClient.pages`).
        """
        path = f"repos/{_repository(project)}/issues"
        since, until = _bounds(window)
        params = {
            "state": "all",
            "since": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sort": "updated",
            "direction": "asc",
        }
        found: list[Issue] = []
        for page in self._client().pages(path, params, too_many="ask for a shorter window"):
            for item in page:
                updated = _time(item, "updated_at")
                if updated >= until:
                    return found  # sorted by update, oldest first: the rest is later still
                if "pull_request" not in item and updated >= since:
                    found.append(_issue(project, item, updated))
        return found

    def _client(self) -> GhClient:
        return GhClient(self._gh, self._settings.host, self._settings.timeout)


def _oldest_first(found: dict[int, PullRequest]) -> list[PullRequest]:
    return sorted(found.values(), key=lambda pull: (pull.merged_at, pull.number))


def _repository(text: str) -> str:
    if not _REPOSITORY.fullmatch(text) or text.split("/")[1] in {".", ".."}:
        msg = f"{text!r} is not a GitHub repository; write it as owner/repo, e.g. acme/widgets."
        raise HeinzelError(msg)
    return text


def _bounds(window: Window) -> tuple[datetime, datetime]:
    # Window's times are timezone-aware by type (AwareDatetime), so only their order needs checking.
    if window.since > window.until:
        msg = f"The window starts ({window.since.isoformat()}) after it ends ({window.until.isoformat()})."
        raise HeinzelError(msg)
    return window.since.astimezone(UTC), window.until.astimezone(UTC)


def _time(item: dict[str, Any], key: str) -> datetime:
    try:
        return datetime.fromisoformat(str(item[key])).astimezone(UTC)
    except (KeyError, ValueError):
        msg = f"GitHub's answer lacks a valid {key}; is github.host a GitHub server?"
        raise HeinzelError(msg) from None


def _labels(item: dict[str, Any]) -> list[str]:
    labels = cast("list[object]", item.get("labels") or [])
    return [
        clean_text(str(cast("dict[str, Any]", label)["name"]) if isinstance(label, dict) else str(label))
        for label in labels
    ]


def _login(person: object) -> str | None:
    login = cast("dict[str, Any]", person).get("login") if isinstance(person, dict) else None
    return clean_text(str(login)) if login else None


def _number(item: dict[str, Any]) -> int:
    number = item["number"]
    if not isinstance(number, int) or isinstance(number, bool):
        raise TypeError(number)
    return number


def _text(item: dict[str, Any], key: str) -> str:
    value = item[key]
    if not isinstance(value, str):
        raise TypeError(key)
    return clean_text(value)


def _url(item: dict[str, Any]) -> str:
    url = _text(item, "html_url")
    if not url.startswith("https://"):  # shown as a link: no javascript: or other schemes
        raise ValueError(url)
    return url


def _pull_request(repository: str, item: dict[str, Any], merged: datetime) -> PullRequest:
    try:
        return PullRequest(
            repository=repository,
            number=_number(item),
            title=_text(item, "title"),
            url=_url(item),
            author=_login(item.get("user")),
            merged_at=merged,
            labels=_labels(item),
        )
    except (KeyError, TypeError, ValueError):
        msg = "GitHub's answer lacks fields of a pull request; is github.host a GitHub server?"
        raise HeinzelError(msg) from None


def _issue(repository: str, item: dict[str, Any], updated: datetime) -> Issue:
    try:
        return Issue(
            key=f"{repository}#{_number(item)}",
            title=_text(item, "title"),
            url=_url(item),
            state=_text(item, "state"),
            updated_at=updated,
            assignee=_login(item.get("assignee")),
            labels=_labels(item),
        )
    except (KeyError, TypeError, ValueError):
        msg = "GitHub's answer lacks fields of an issue; is github.host a GitHub server?"
        raise HeinzelError(msg) from None
