from datetime import UTC, datetime, timedelta, timezone

import pytest
from heinzel_github import GitHub, GithubSettings, _client
from heinzel_github._client import MAX_PAGES

from heinzel import HeinzelError, Window

pytestmark = pytest.mark.unit

PULLS = "repos/acme/widgets/pulls"
ISSUES = "repos/acme/widgets/issues"
OCTOBER = Window(since=datetime(2026, 10, 1, tzinfo=UTC), until=datetime(2026, 11, 1, tzinfo=UTC))


def adapter(fake, **settings) -> GitHub:
    return GitHub(GithubSettings(**settings), fake)


@pytest.fixture
def pages_of(monkeypatch):
    """Make a full page `size` items, so a few items span pages; GitHub's is 100."""
    return lambda size: monkeypatch.setattr(_client, "PER_PAGE", size)


def test_merged_pull_requests_keeps_those_merged_in_the_window_oldest_first(fake_gh):
    pull = fake_gh.pull
    fake_gh.serve(
        PULLS,
        [
            pull(9, updated_at="2026-11-02T00:00:00Z", merged_at="2026-11-01T00:00:00Z"),  # merged at `until`: out
            pull(8, updated_at="2026-11-01T10:00:00Z", merged_at="2026-10-20T00:00:00Z", labels=("bug", "ui")),
            pull(7, updated_at="2026-10-15T00:00:00Z", merged_at=None),  # closed unmerged
            pull(6, updated_at="2026-10-10T00:00:00Z", merged_at="2026-10-01T00:00:00Z", user=None),  # at `since`: in
        ],
    )
    pulls = adapter(fake_gh).merged_pull_requests("acme/widgets", OCTOBER)
    assert [(p.number, p.merged_at, p.author, p.labels) for p in pulls] == [
        (6, datetime(2026, 10, 1, tzinfo=UTC), None, []),
        (8, datetime(2026, 10, 20, tzinfo=UTC), "ada", ["bug", "ui"]),
    ]
    assert pulls[1].url == "https://github.com/acme/widgets/pull/8"
    assert pulls[1].repository == "acme/widgets"
    assert pulls[1].title == "Pull 8"
    assert fake_gh.calls[0].fields == {
        "state": "closed",
        "sort": "updated",
        "direction": "desc",
        "per_page": "100",
        "page": "1",
    }
    assert len(fake_gh.calls) == 1  # a short page is the last


def test_merged_pull_requests_follows_pages_and_stops_before_the_window(fake_gh, pages_of):
    pages_of(2)
    pull = fake_gh.pull
    fake_gh.serve(
        PULLS,
        [
            pull(4, updated_at="2026-10-31T00:00:00Z", merged_at=None),
            pull(3, updated_at="2026-10-30T00:00:00Z", merged_at="2026-10-29T00:00:00Z"),
        ],
        [
            pull(2, updated_at="2026-10-02T00:00:00Z", merged_at="2026-10-02T00:00:00Z"),
            pull(1, updated_at="2026-09-30T00:00:00Z", merged_at="2026-10-05T00:00:00Z"),  # impossible, proves the stop
        ],
        [pull(0, updated_at="2026-09-01T00:00:00Z", merged_at="2026-09-01T00:00:00Z")],
    )
    pulls = adapter(fake_gh).merged_pull_requests("acme/widgets", OCTOBER)
    assert [p.number for p in pulls] == [2, 3]
    assert [c.fields["page"] for c in fake_gh.calls] == ["1", "2"]  # page 3 never asked for


def test_merged_pull_requests_counts_a_pull_request_seen_on_two_pages_once(fake_gh, pages_of):
    pages_of(2)
    pull = fake_gh.pull
    shifted = pull(5, updated_at="2026-10-10T00:00:00Z", merged_at="2026-10-09T00:00:00Z")
    unmerged = pull(6, updated_at="2026-10-11T00:00:00Z", merged_at=None)
    fake_gh.serve(
        PULLS,
        [unmerged, shifted],
        [shifted, pull(4, updated_at="2026-10-05T00:00:00Z", merged_at="2026-10-04T00:00:00Z")],
    )
    assert [p.number for p in adapter(fake_gh).merged_pull_requests("acme/widgets", OCTOBER)] == [4, 5]


def test_too_many_pages_of_pull_requests_ask_for_a_window_that_starts_more_recently(fake_gh, pages_of):
    pages_of(1)
    recent = fake_gh.pull(1, updated_at="2026-10-30T00:00:00Z", merged_at=None)
    fake_gh.serve(PULLS, *([recent] for _ in range(MAX_PAGES + 1)))
    with pytest.raises(HeinzelError, match="a window that starts more recently"):
        adapter(fake_gh).merged_pull_requests("acme/widgets", OCTOBER)


def test_too_many_pages_of_issues_ask_for_a_shorter_window(fake_gh, pages_of):
    pages_of(1)
    issue = fake_gh.issue(1, updated_at="2026-10-02T00:00:00Z")
    fake_gh.serve(ISSUES, *([issue] for _ in range(MAX_PAGES + 1)))
    with pytest.raises(HeinzelError, match="ask for a shorter window"):
        adapter(fake_gh).issues_updated("acme/widgets", OCTOBER)


def test_merged_pull_requests_of_an_empty_repository(fake_gh):
    fake_gh.serve(PULLS, [])
    assert adapter(fake_gh).merged_pull_requests("acme/widgets", OCTOBER) == []


def test_window_bounds_in_other_timezones_are_compared_as_instants(fake_gh):
    fake_gh.serve(PULLS, [fake_gh.pull(1, updated_at="2026-10-01T00:30:00Z", merged_at="2026-10-01T00:30:00Z")])
    berlin = timezone(timedelta(hours=2))
    window = Window(since=datetime(2026, 10, 1, 2, 0, tzinfo=berlin), until=datetime(2026, 10, 1, 3, 0, tzinfo=berlin))
    assert [p.number for p in adapter(fake_gh).merged_pull_requests("acme/widgets", window)] == [1]


def test_issues_updated_skips_pull_requests_and_stops_at_the_end_of_the_window(fake_gh, pages_of):
    pages_of(3)
    issue = fake_gh.issue
    fake_gh.serve(
        ISSUES,
        [
            issue(1, updated_at="2026-10-02T00:00:00Z", assignee="grace", labels=("bug",)),
            issue(2, updated_at="2026-10-03T00:00:00Z", pull_request=True),
            issue(3, updated_at="2026-10-04T00:00:00Z", state="closed"),
        ],
        [issue(4, updated_at="2026-11-01T00:00:00Z"), issue(5, updated_at="2026-11-02T00:00:00Z")],
        [issue(6, updated_at="2026-11-03T00:00:00Z")],
    )
    issues = adapter(fake_gh).issues_updated("acme/widgets", OCTOBER)
    assert [(i.key, i.state, i.assignee, i.labels) for i in issues] == [
        ("acme/widgets#1", "open", "grace", ["bug"]),
        ("acme/widgets#3", "closed", None, []),
    ]
    assert issues[0].url == "https://github.com/acme/widgets/issues/1"
    assert issues[0].updated_at == datetime(2026, 10, 2, tzinfo=UTC)
    assert fake_gh.calls[0].fields == {
        "state": "all",
        "since": "2026-10-01T00:00:00Z",
        "sort": "updated",
        "direction": "asc",
        "per_page": "3",
        "page": "1",
    }
    assert len(fake_gh.calls) == 2


def test_issues_updated_ignores_anything_before_the_window(fake_gh):
    fake_gh.serve(ISSUES, [fake_gh.issue(1, updated_at="2026-09-30T23:59:59Z")])
    assert adapter(fake_gh).issues_updated("acme/widgets", OCTOBER) == []


@pytest.mark.parametrize(
    "repository",
    ["widgets", "acme/", "/widgets", "acme/widgets/extra", "ac me/widgets", "a/b?x=1", "acme/.", "acme/.."],
)
def test_a_malformed_repository_is_refused_before_any_request(fake_gh, repository):
    with pytest.raises(HeinzelError, match="owner/repo"):
        adapter(fake_gh).merged_pull_requests(repository, OCTOBER)
    with pytest.raises(HeinzelError, match="owner/repo"):
        adapter(fake_gh).issues_updated(repository, OCTOBER)
    assert fake_gh.calls == []


def test_repository_names_with_dots_are_fine(fake_gh):
    fake_gh.serve("repos/acme/.github/issues", [])
    fake_gh.serve("repos/acme/...x/issues", [])
    assert adapter(fake_gh).issues_updated("acme/.github", OCTOBER) == []
    assert adapter(fake_gh).issues_updated("acme/...x", OCTOBER) == []


def test_a_reversed_window_is_refused(fake_gh):
    window = Window(since=OCTOBER.until, until=OCTOBER.since)
    with pytest.raises(HeinzelError, match="starts"):
        adapter(fake_gh).issues_updated("acme/widgets", window)


@pytest.mark.parametrize(
    "item",
    [
        {"number": 1, "updated_at": "2026-10-02T00:00:00Z", "merged_at": "2026-10-02T00:00:00Z"},  # no title
        {"number": 1, "updated_at": "yesterday"},
        {"number": 1},
    ],
)
def test_an_unexpected_pull_request_is_a_clear_error(fake_gh, item):
    fake_gh.serve(PULLS, [item])
    with pytest.raises(HeinzelError, match=r"is github\.host a GitHub"):
        adapter(fake_gh).merged_pull_requests("acme/widgets", OCTOBER)


def test_an_unexpected_issue_is_a_clear_error(fake_gh):
    fake_gh.serve(ISSUES, [{"number": "x", "updated_at": "2026-10-02T00:00:00Z", "title": "t"}])
    with pytest.raises(HeinzelError, match="fields of an issue"):
        adapter(fake_gh).issues_updated("acme/widgets", OCTOBER)


def test_labels_given_as_plain_strings_are_kept(fake_gh):
    item = fake_gh.issue(1, updated_at="2026-10-02T00:00:00Z")
    item["labels"] = ["triage"]
    fake_gh.serve(ISSUES, [item])
    assert adapter(fake_gh).issues_updated("acme/widgets", OCTOBER)[0].labels == ["triage"]


def test_titles_logins_and_labels_lose_control_and_bidirectional_characters(fake_gh):
    sneaky = fake_gh.pull(
        1,
        updated_at="2026-10-02T00:00:00Z",
        merged_at="2026-10-02T00:00:00Z",
        title="Fix\u202e gnp.exe\x1b[2J",
        user="ada\u200b",
        labels=("u\u2066i",),
    )
    fake_gh.serve(PULLS, [sneaky])
    pull = adapter(fake_gh).merged_pull_requests("acme/widgets", OCTOBER)[0]
    assert (pull.title, pull.author, pull.labels) == ("Fix gnp.exe[2J", "ada", ["ui"])


def test_an_issue_with_a_non_text_title_is_a_clear_error(fake_gh):
    item = fake_gh.issue(1, updated_at="2026-10-02T00:00:00Z")
    item["title"] = {"text": "nested"}
    fake_gh.serve(ISSUES, [item])
    with pytest.raises(HeinzelError, match="fields of an issue"):
        adapter(fake_gh).issues_updated("acme/widgets", OCTOBER)


def test_a_boolean_number_is_a_clear_error(fake_gh):
    item = fake_gh.pull(1, updated_at="2026-10-02T00:00:00Z", merged_at="2026-10-02T00:00:00Z")
    item["number"] = True
    fake_gh.serve(PULLS, [item])
    with pytest.raises(HeinzelError, match="fields of a pull request"):
        adapter(fake_gh).merged_pull_requests("acme/widgets", OCTOBER)


def test_a_link_that_is_not_https_is_a_clear_error(fake_gh):
    item = fake_gh.issue(1, updated_at="2026-10-02T00:00:00Z")
    item["html_url"] = "javascript:alert(1)"
    fake_gh.serve(ISSUES, [item])
    with pytest.raises(HeinzelError, match="fields of an issue"):
        adapter(fake_gh).issues_updated("acme/widgets", OCTOBER)


def test_the_configured_host_and_timeout_are_used(fake_gh):
    fake_gh.serve(ISSUES, [])
    adapter(fake_gh, host="GHE.example.com", timeout=5).issues_updated("acme/widgets", OCTOBER)
    call = fake_gh.calls[0]
    assert call.host == "ghe.example.com"
    assert 4.0 < call.timeout <= 5
    assert call.args[:3] == ["api", "--hostname", "ghe.example.com"]
