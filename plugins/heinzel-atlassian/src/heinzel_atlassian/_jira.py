"""The Jira adapter of the `tracker` capability (ADR 0028), read mode only.

Issues come from Jira Cloud's enhanced search, `GET /rest/api/3/search/jql`, paged with `nextPageToken`
(ADR 0034). The JQL compares `updated` with epoch milliseconds, which
Jira reads as instants, so the account's time zone doesn't shift the window; each issue's `updated` is checked against
the window again before it is kept.
"""

import math
import re
from datetime import UTC, datetime
from typing import Any, cast

from heinzel import Credentials, HeinzelError, Issue, Window
from heinzel_atlassian._client import clean, connect
from heinzel_atlassian._models import AtlassianSettings
from heinzel_atlassian._transport import Transport

SEARCH = "/rest/api/3/search/jql"
FIELDS = "summary,status,updated,assignee,labels"
MAX_RESULTS = "100"
MAX_PAGES = 100
"""Pages followed for one call (10,000 issues at 100 a page) before asking for a narrower window."""
MINUTE_MS = 60_000
_PROJECT = re.compile(r"^[A-Z][A-Z0-9_]{1,254}$")
_ISSUE = re.compile(r"^[A-Z][A-Z0-9_]{1,254}-[0-9]{1,18}$")


def jql_string(text: str) -> str:
    """`text` as a JQL string literal: in double quotes, with backslashes and quotes escaped."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def search_jql(project: str, since: datetime, until: datetime) -> str:
    """The JQL for issues of `project` (a validated key) updated in `[since, until)`, oldest first.

    Unquoted numbers are epoch milliseconds to JQL, independent of the account's time zone; quoted dates are not.
    The bounds round outwards to whole minutes, JQL's resolution for dates, so however Jira rounds a bound no issue
    of the window is left out; the adapter filters the exact window afterwards.
    """
    start = math.floor(since.timestamp() * 1000 / MINUTE_MS) * MINUTE_MS
    end = math.ceil(until.timestamp() * 1000 / MINUTE_MS) * MINUTE_MS
    return f"project = {jql_string(project)} AND updated >= {start} AND updated < {end} ORDER BY updated ASC, key ASC"


class Jira:
    """Reads issues of a Jira project, given by its key such as `WID`, updated within a window."""

    def __init__(self, settings: AtlassianSettings, credentials: Credentials, transport: Transport) -> None:
        """Build the adapter; the kernel injects the settings, the credentials and the transport."""
        self._settings = settings
        self._credentials = credentials
        self._transport = transport

    def issues_updated(self, project: str, window: Window) -> list[Issue]:
        """Issues of `project`, a Jira project key, updated within `window`, oldest first, each once.

        An issue updated again while the pages are read can come back on a later page; the latest copy is kept.

        Raises:
            HeinzelError: If the key or window is malformed, the site, email or token is missing, or Jira fails.
        """
        key = _project(project)
        since, until = _bounds(window)
        if since == until:
            return []  # the half-open window is empty: nothing to ask
        client = connect(self._settings, self._credentials, self._transport)
        site = cast("str", self._settings.site_url)  # connect() checked it
        params = {"jql": search_jql(key, since, until), "fields": FIELDS, "maxResults": MAX_RESULTS}
        subject = f"project {key}"
        found: dict[str, Issue] = {}
        seen: set[str] = set()
        for _ in range(MAX_PAGES):
            page = client.get("Jira", SEARCH, params, subject)
            for item in _issues(page):
                issue = _issue(site, item)
                if issue.updated_at >= until:
                    return _oldest_first(found)  # sorted by update, oldest first: the rest is later still
                if issue.updated_at >= since:
                    _keep_latest(found, issue)
            token = page.get("nextPageToken")
            if page.get("isLast") is True or not isinstance(token, str) or not token:
                return _oldest_first(found)
            if token in seen:
                msg = f"Jira repeated a page of the search for {subject}; stopping instead of looping. Try again."
                raise HeinzelError(msg)
            seen.add(token)
            params = {**params, "nextPageToken": token}
        msg = f"The search for {subject} has more than {MAX_PAGES} pages of issues; ask for a shorter window."
        raise HeinzelError(msg)


def _keep_latest(found: dict[str, Issue], issue: Issue) -> None:
    kept = found.get(issue.key)
    if kept is None or issue.updated_at >= kept.updated_at:
        found[issue.key] = issue


def _oldest_first(found: dict[str, Issue]) -> list[Issue]:
    return sorted(found.values(), key=lambda issue: issue.updated_at)  # stable: Jira's order among equal times


def _project(text: str) -> str:
    key = text.strip().upper()
    if not _PROJECT.fullmatch(key):
        msg = (
            f"{text!r} is not a Jira project key; write the key, e.g. WID: a letter, then letters, digits or "
            "underscores."
        )
        raise HeinzelError(msg)
    return key


def _bounds(window: Window) -> tuple[datetime, datetime]:
    # Window's times are timezone-aware by type (AwareDatetime), so only their order needs checking.
    if window.since > window.until:
        msg = f"The window starts ({window.since.isoformat()}) after it ends ({window.until.isoformat()})."
        raise HeinzelError(msg)
    return window.since.astimezone(UTC), window.until.astimezone(UTC)


def _issues(page: dict[str, Any]) -> list[dict[str, Any]]:
    issues = page.get("issues")
    if not isinstance(issues, list) or not all(isinstance(i, dict) for i in cast("list[object]", issues)):
        msg = "Jira's search answer lacks a list of issues; is atlassian.site_url an Atlassian Cloud site?"
        raise HeinzelError(msg)
    return cast("list[dict[str, Any]]", issues)


def _name(value: object, key: str) -> str | None:
    found = cast("dict[str, Any]", value).get(key) if isinstance(value, dict) else None
    text = clean(str(found)) if found else ""
    return text or None


def _issue(site: str, item: dict[str, Any]) -> Issue:
    try:
        key = str(item["key"])
        fields = cast("dict[str, Any]", item["fields"])
        if not _ISSUE.fullmatch(key):
            raise ValueError(key)  # noqa: TRY301 - one message for every malformed issue
        labels = cast("list[object]", fields.get("labels") or [])
        return Issue(
            key=key,
            title=clean(str(fields["summary"])),
            url=f"{site}/browse/{key}",  # built, never taken from the answer
            state=_name(fields.get("status"), "name") or "unknown",
            updated_at=datetime.fromisoformat(str(fields["updated"])).astimezone(UTC),
            assignee=_name(fields.get("assignee"), "displayName"),
            labels=[text for label in labels if (text := clean(str(label)))],
        )
    except (KeyError, TypeError, ValueError):
        msg = "Jira's answer lacks fields of an issue; is atlassian.site_url an Atlassian Cloud site?"
        raise HeinzelError(msg) from None
