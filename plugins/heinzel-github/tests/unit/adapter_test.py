from datetime import UTC, datetime, timedelta, timezone

import pytest
from heinzel_github import TOKEN, GitHub, GithubSettings
from heinzel_github._client import MAX_PAGES

from heinzel import Credentials, HeinzelError, Window
from heinzel.testing import MemoryBackend

pytestmark = pytest.mark.unit

PULLS = "/repos/acme/widgets/pulls"
ISSUES = "/repos/acme/widgets/issues"
OCTOBER = Window(since=datetime(2026, 10, 1, tzinfo=UTC), until=datetime(2026, 11, 1, tzinfo=UTC))


def adapter(fake, token: str | None = None, **settings) -> GitHub:
    credentials = Credentials(MemoryBackend(), [("github", TOKEN)])
    if token:
        credentials.set(TOKEN, token)
    return GitHub(GithubSettings(**settings), credentials, fake)


def test_merged_pull_requests_keeps_those_merged_in_the_window_oldest_first(fake_github):
    pull = fake_github.pull
    fake_github.serve(
        PULLS,
        [
            pull(9, updated_at="2026-11-02T00:00:00Z", merged_at="2026-11-01T00:00:00Z"),  # merged at `until`: out
            pull(8, updated_at="2026-11-01T10:00:00Z", merged_at="2026-10-20T00:00:00Z", labels=("bug", "ui")),
            pull(7, updated_at="2026-10-15T00:00:00Z", merged_at=None),  # closed unmerged
            pull(6, updated_at="2026-10-10T00:00:00Z", merged_at="2026-10-01T00:00:00Z", user=None),  # at `since`: in
        ],
    )
    pulls = adapter(fake_github).merged_pull_requests("acme/widgets", OCTOBER)
    assert [(p.number, p.merged_at, p.author, p.labels) for p in pulls] == [
        (6, datetime(2026, 10, 1, tzinfo=UTC), None, []),
        (8, datetime(2026, 10, 20, tzinfo=UTC), "ada", ["bug", "ui"]),
    ]
    assert pulls[1].url == "https://github.com/acme/widgets/pull/8"
    assert pulls[1].repository == "acme/widgets"
    assert pulls[1].title == "Pull 8"
    assert fake_github.requests[0].query == {
        "state": "closed",
        "sort": "updated",
        "direction": "desc",
        "per_page": "100",
    }


def test_merged_pull_requests_follows_pages_and_stops_before_the_window(fake_github):
    pull = fake_github.pull
    fake_github.serve(
        PULLS,
        [pull(3, updated_at="2026-10-30T00:00:00Z", merged_at="2026-10-29T00:00:00Z")],
        [
            pull(2, updated_at="2026-10-02T00:00:00Z", merged_at="2026-10-02T00:00:00Z"),
            pull(1, updated_at="2026-09-30T00:00:00Z", merged_at="2026-10-05T00:00:00Z"),  # impossible, proves the stop
        ],
        [pull(0, updated_at="2026-09-01T00:00:00Z", merged_at="2026-09-01T00:00:00Z")],
    )
    pulls = adapter(fake_github).merged_pull_requests("acme/widgets", OCTOBER)
    assert [p.number for p in pulls] == [2, 3]
    assert [r.query.get("page") for r in fake_github.requests] == [None, "2"]  # page 3 never asked for


def test_merged_pull_requests_counts_a_pull_request_seen_on_two_pages_once(fake_github):
    pull = fake_github.pull
    shifted = pull(5, updated_at="2026-10-10T00:00:00Z", merged_at="2026-10-09T00:00:00Z")
    fake_github.serve(
        PULLS, [shifted], [shifted, pull(4, updated_at="2026-10-05T00:00:00Z", merged_at="2026-10-04T00:00:00Z")]
    )
    assert [p.number for p in adapter(fake_github).merged_pull_requests("acme/widgets", OCTOBER)] == [4, 5]


def test_too_many_pages_of_pull_requests_ask_for_a_window_that_starts_more_recently(fake_github):
    recent = fake_github.pull(1, updated_at="2026-10-30T00:00:00Z", merged_at=None)
    fake_github.serve(PULLS, *([recent] for _ in range(MAX_PAGES + 1)))
    with pytest.raises(HeinzelError, match="a window that starts more recently"):
        adapter(fake_github).merged_pull_requests("acme/widgets", OCTOBER)


def test_too_many_pages_of_issues_ask_for_a_shorter_window(fake_github):
    issue = fake_github.issue(1, updated_at="2026-10-02T00:00:00Z")
    fake_github.serve(ISSUES, *([issue] for _ in range(MAX_PAGES + 1)))
    with pytest.raises(HeinzelError, match="ask for a shorter window"):
        adapter(fake_github).issues_updated("acme/widgets", OCTOBER)


def test_merged_pull_requests_of_an_empty_repository(fake_github):
    fake_github.serve(PULLS, [])
    assert adapter(fake_github).merged_pull_requests("acme/widgets", OCTOBER) == []


def test_window_bounds_in_other_timezones_are_compared_as_instants(fake_github):
    fake_github.serve(PULLS, [fake_github.pull(1, updated_at="2026-10-01T00:30:00Z", merged_at="2026-10-01T00:30:00Z")])
    berlin = timezone(timedelta(hours=2))
    window = Window(since=datetime(2026, 10, 1, 2, 0, tzinfo=berlin), until=datetime(2026, 10, 1, 3, 0, tzinfo=berlin))
    assert [p.number for p in adapter(fake_github).merged_pull_requests("acme/widgets", window)] == [1]


def test_issues_updated_skips_pull_requests_and_stops_at_the_end_of_the_window(fake_github):
    issue = fake_github.issue
    fake_github.serve(
        ISSUES,
        [
            issue(1, updated_at="2026-10-02T00:00:00Z", assignee="grace", labels=("bug",)),
            issue(2, updated_at="2026-10-03T00:00:00Z", pull_request=True),
            issue(3, updated_at="2026-10-04T00:00:00Z", state="closed"),
        ],
        [issue(4, updated_at="2026-11-01T00:00:00Z"), issue(5, updated_at="2026-11-02T00:00:00Z")],
        [issue(6, updated_at="2026-11-03T00:00:00Z")],
    )
    issues = adapter(fake_github).issues_updated("acme/widgets", OCTOBER)
    assert [(i.key, i.state, i.assignee, i.labels) for i in issues] == [
        ("acme/widgets#1", "open", "grace", ["bug"]),
        ("acme/widgets#3", "closed", None, []),
    ]
    assert issues[0].url == "https://github.com/acme/widgets/issues/1"
    assert issues[0].updated_at == datetime(2026, 10, 2, tzinfo=UTC)
    assert fake_github.requests[0].query == {
        "state": "all",
        "since": "2026-10-01T00:00:00Z",
        "sort": "updated",
        "direction": "asc",
        "per_page": "100",
    }
    assert len(fake_github.requests) == 2


def test_issues_updated_ignores_anything_before_the_window(fake_github):
    fake_github.serve(ISSUES, [fake_github.issue(1, updated_at="2026-09-30T23:59:59Z")])
    assert adapter(fake_github).issues_updated("acme/widgets", OCTOBER) == []


@pytest.mark.parametrize(
    "repository",
    ["widgets", "acme/", "/widgets", "acme/widgets/extra", "ac me/widgets", "a/b?x=1", "acme/.", "acme/.."],
)
def test_a_malformed_repository_is_refused_before_any_request(fake_github, repository):
    with pytest.raises(HeinzelError, match="owner/repo"):
        adapter(fake_github).merged_pull_requests(repository, OCTOBER)
    with pytest.raises(HeinzelError, match="owner/repo"):
        adapter(fake_github).issues_updated(repository, OCTOBER)
    assert fake_github.requests == []


def test_repository_names_with_dots_are_fine(fake_github):
    fake_github.serve("/repos/acme/.github/issues", [])
    fake_github.serve("/repos/acme/...x/issues", [])
    assert adapter(fake_github).issues_updated("acme/.github", OCTOBER) == []
    assert adapter(fake_github).issues_updated("acme/...x", OCTOBER) == []


def test_a_naive_window_is_refused(fake_github):
    window = Window(since=datetime(2026, 10, 1), until=datetime(2026, 11, 1))  # noqa: DTZ001 - the point
    with pytest.raises(HeinzelError, match="timezone-aware"):
        adapter(fake_github).merged_pull_requests("acme/widgets", window)


def test_a_reversed_window_is_refused(fake_github):
    window = Window(since=OCTOBER.until, until=OCTOBER.since)
    with pytest.raises(HeinzelError, match="starts"):
        adapter(fake_github).issues_updated("acme/widgets", window)


@pytest.mark.parametrize(
    "item",
    [
        {"number": 1, "updated_at": "2026-10-02T00:00:00Z", "merged_at": "2026-10-02T00:00:00Z"},  # no title
        {"number": 1, "updated_at": "yesterday"},
        {"number": 1},
    ],
)
def test_an_unexpected_pull_request_is_a_clear_error(fake_github, item):
    fake_github.serve(PULLS, [item])
    with pytest.raises(HeinzelError, match="GitHub REST API"):
        adapter(fake_github).merged_pull_requests("acme/widgets", OCTOBER)


def test_an_unexpected_issue_is_a_clear_error(fake_github):
    fake_github.serve(ISSUES, [{"number": "x", "updated_at": "2026-10-02T00:00:00Z", "title": "t"}])
    with pytest.raises(HeinzelError, match="fields of an issue"):
        adapter(fake_github).issues_updated("acme/widgets", OCTOBER)


def test_labels_given_as_plain_strings_are_kept(fake_github):
    item = fake_github.issue(1, updated_at="2026-10-02T00:00:00Z")
    item["labels"] = ["triage"]
    fake_github.serve(ISSUES, [item])
    assert adapter(fake_github).issues_updated("acme/widgets", OCTOBER)[0].labels == ["triage"]


def test_the_stored_token_is_sent_and_anonymous_works_without_one(fake_github):
    fake_github.serve(ISSUES, [])
    adapter(fake_github).issues_updated("acme/widgets", OCTOBER)
    adapter(fake_github, token="ghp_secret").issues_updated("acme/widgets", OCTOBER)
    anonymous, authenticated = fake_github.requests
    assert "Authorization" not in anonymous.headers
    assert authenticated.headers["Authorization"] == "Bearer ghp_secret"


def test_the_configured_api_url_and_timeout_are_used(fake_github):
    fake_github.api = "https://ghe.example.com/api/v3"
    fake_github.serve("/api/v3/repos/acme/widgets/issues", [])
    adapter(fake_github, api_url="https://ghe.example.com/api/v3/", timeout=5).issues_updated("acme/widgets", OCTOBER)
    assert fake_github.requests[0].url.startswith("https://ghe.example.com/api/v3/repos/acme/widgets/issues?")
    assert fake_github.requests[0].timeout == 5
