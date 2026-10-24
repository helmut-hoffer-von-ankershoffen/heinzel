"""The Jira adapter of the `tracker` capability (ADR 0028), read mode only, through acli.

Issues come from `acli jira workitem search --jql <jql> --fields <fields> --paginate --json`; acli owns
authentication and paging. The JQL compares `updated` with epoch milliseconds, which Jira reads as instants, so the
account's time zone doesn't shift the window; each issue's `updated` is checked against the window again before it
is kept.
"""

import math
import re
from datetime import UTC, datetime
from typing import Any, cast

from heinzel import HeinzelError, Issue, Window, clean_text
from heinzel_atlassian._acli import Acli
from heinzel_atlassian._client import AcliClient, require_site, same_site
from heinzel_atlassian._models import AtlassianSettings

FIELDS = "key,summary,status,updated,assignee,labels"
MAX_ISSUES = 1000
"""Issues one call returns at most before asking for a narrower window."""
MINUTE_MS = 60_000
_PROJECT = re.compile(r"^[A-Z][A-Z0-9_]{1,254}$")
_ISSUE = re.compile(r"^[A-Z][A-Z0-9_]{1,254}-[0-9]{1,18}$")
_UNEXPECTED = "acli's search answer is not a list of Jira issues; is acli up to date?"


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

    def __init__(self, settings: AtlassianSettings, acli: Acli) -> None:
        """Build the adapter; the kernel injects the settings and acli."""
        self._settings = settings
        self._acli = acli

    def issues_updated(self, project: str, window: Window) -> list[Issue]:
        """Issues of `project`, a Jira project key, updated within `window`, oldest first, each once.

        An issue updated again while acli pages through the search can come back twice; the latest copy is kept.

        Raises:
            HeinzelError: If the key or window is malformed, the site is not set, acli is missing or logged out, Jira
                fails, or more than `MAX_ISSUES` issues match.
        """
        key = _project(project)
        since, until = _bounds(window)
        if since == until:
            return []  # the half-open window is empty: nothing to ask
        site = require_site(self._settings)
        args = ["jira", "workitem", "search", "--jql", search_jql(key, since, until), "--fields", FIELDS]
        args += ["--paginate", "--json"]
        subject = f"project {key}"
        items = [
            i for d in AcliClient(self._acli, self._settings.timeout).json("Jira", args, subject) for i in _issues(d)
        ]
        if len(items) > MAX_ISSUES:
            msg = f"More than {MAX_ISSUES} issues of {subject} were updated in the window; ask for a shorter window."
            raise HeinzelError(msg)
        found: dict[str, Issue] = {}
        for item in items:
            same_site("Jira", site, item.get("self"), "/rest")
            issue = _issue(site, key, item)
            if since <= issue.updated_at < until:
                _keep_latest(found, issue)
        return _oldest_first(found)


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


def _issues(answer: object) -> list[dict[str, Any]]:
    """The issues in one JSON document acli printed: a list of issues, or an object holding them under `issues`.

    Raises:
        HeinzelError: If the document is neither.
    """
    issues = cast("dict[str, Any]", answer).get("issues") if isinstance(answer, dict) else answer
    if not isinstance(issues, list) or not all(isinstance(i, dict) for i in cast("list[object]", issues)):
        raise HeinzelError(_UNEXPECTED)
    return cast("list[dict[str, Any]]", issues)


def _name(value: object, key: str) -> str | None:
    found = cast("dict[str, Any]", value).get(key) if isinstance(value, dict) else None
    text = clean_text(found) if isinstance(found, str) else ""
    return text or None


def _issue(site: str, project: str, item: dict[str, Any]) -> Issue:
    """One issue of acli's answer, checked field by field; its key must be of `project`.

    Raises:
        HeinzelError: If a field is missing, has the wrong type, the key is of another project, or `updated` is no
            time with an offset.
    """
    key, fields = item.get("key"), item.get("fields")
    if isinstance(key, str) and _ISSUE.fullmatch(key) and key.startswith(f"{project}-") and isinstance(fields, dict):
        fields = cast("dict[str, Any]", fields)
        summary, labels = fields.get("summary"), cast("object", fields.get("labels") or [])
        updated = _instant(fields.get("updated"))
        if isinstance(summary, str) and isinstance(labels, list) and updated is not None:
            return Issue(
                key=key,
                title=clean_text(summary),
                url=f"{site}/browse/{key}",  # built, never taken from the answer
                state=_name(fields.get("status"), "name") or "unknown",
                updated_at=updated,
                assignee=_name(fields.get("assignee"), "displayName"),
                labels=[
                    text
                    for label in cast("list[object]", labels)
                    if isinstance(label, str) and (text := clean_text(label))
                ],
            )
    msg = "acli's search answer lacks fields of a Jira issue; is acli up to date?"
    raise HeinzelError(msg)


def _instant(value: object) -> datetime | None:
    """`value` as a UTC time if it is an ISO time with an offset (`2026-10-05T12:34:56.789+0200`), else None."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
