import base64
from datetime import UTC, datetime, timedelta, timezone

import pytest
from heinzel_atlassian import TOKEN, AtlassianSettings, Jira
from heinzel_atlassian._jira import MAX_PAGES, jql_string, search_jql

from heinzel import Credentials, HeinzelError, Window
from heinzel.testing import MemoryBackend

pytestmark = pytest.mark.unit

SITE = "https://acme.atlassian.net"
EMAIL = "ada@acme.example"
SECRET = "fake-token-jira-secret"
SEARCH = "/rest/api/3/search/jql"
OCTOBER = Window(since=datetime(2026, 10, 1, tzinfo=UTC), until=datetime(2026, 11, 1, tzinfo=UTC))
OCTOBER_JQL = 'project = "WID" AND updated >= 1790812800000 AND updated < 1793491200000 ORDER BY updated ASC, key ASC'


def adapter(fake, token: str | None = SECRET, **settings) -> Jira:
    credentials = Credentials(MemoryBackend(), [("atlassian", TOKEN)])
    if token:
        credentials.set(TOKEN, token)
    return Jira(AtlassianSettings(**({"site_url": SITE, "email": EMAIL} | settings)), credentials, fake)


def test_issues_updated_in_the_window_oldest_first(fake_atlassian):
    issue = fake_atlassian.issue
    fake_atlassian.search(
        [
            issue(1, updated="2026-09-30T23:59:59.999+0000"),  # just before the window: out
            issue(2, updated="2026-10-01T02:00:00.000+0200", status="Done", assignee="Grace Hopper"),  # at `since`
            issue(3, updated="2026-10-05T12:34:56.789+0200", labels=("backend", "urgent")),
            issue(4, updated="2026-11-01T00:00:00.000+0000"),  # at `until`: out, and the end
            issue(5, updated="2026-11-02T00:00:00.000+0000"),
        ]
    )
    issues = adapter(fake_atlassian).issues_updated("WID", OCTOBER)
    assert [(i.key, i.state, i.assignee, i.labels) for i in issues] == [
        ("WID-2", "Done", "Grace Hopper", []),
        ("WID-3", "To Do", None, ["backend", "urgent"]),
    ]
    assert issues[0].updated_at == datetime(2026, 10, 1, tzinfo=UTC)
    assert issues[1].updated_at == datetime(2026, 10, 5, 10, 34, 56, 789000, tzinfo=UTC)
    assert issues[1].title == "Issue 3"
    assert issues[1].url == f"{SITE}/browse/WID-3"  # built on the site, not the answer's `self`
    request = fake_atlassian.requests[0]
    assert request.url.startswith(f"{SITE}{SEARCH}?")
    assert request.query == {
        "jql": OCTOBER_JQL,
        "fields": "summary,status,updated,assignee,labels",
        "maxResults": "100",
    }


def test_pages_are_followed_by_token_until_the_last(fake_atlassian):
    issue = fake_atlassian.issue
    fake_atlassian.search(
        [issue(1, updated="2026-10-02T00:00:00.000+0000")],
        [issue(2, updated="2026-10-03T00:00:00.000+0000")],
        [issue(3, updated="2026-10-04T00:00:00.000+0000")],
    )
    issues = adapter(fake_atlassian).issues_updated("WID", OCTOBER)
    assert [i.key for i in issues] == ["WID-1", "WID-2", "WID-3"]
    assert [r.query.get("nextPageToken") for r in fake_atlassian.requests] == [None, "token-1", "token-2"]
    assert {r.query["jql"] for r in fake_atlassian.requests} == {OCTOBER_JQL}


def test_is_last_ends_paging_even_with_a_token(fake_atlassian):
    fake_atlassian.search(
        [fake_atlassian.issue(1, updated="2026-10-02T00:00:00.000+0000")], [], is_last=True
    )  # the first page carries a token and isLast
    assert len(adapter(fake_atlassian).issues_updated("WID", OCTOBER)) == 1
    assert len(fake_atlassian.requests) == 1


def test_a_short_page_with_a_token_is_not_the_end(fake_atlassian):
    fake_atlassian.search([], [fake_atlassian.issue(9, updated="2026-10-02T00:00:00.000+0000")], is_last=False)
    assert [i.key for i in adapter(fake_atlassian).issues_updated("WID", OCTOBER)] == ["WID-9"]


def test_paging_stops_at_the_first_issue_after_the_window(fake_atlassian):
    issue = fake_atlassian.issue
    fake_atlassian.search(
        [issue(1, updated="2026-10-02T00:00:00.000+0000"), issue(2, updated="2026-11-01T00:00:00.001+0000")],
        [issue(3, updated="2026-11-03T00:00:00.000+0000")],
    )
    assert [i.key for i in adapter(fake_atlassian).issues_updated("WID", OCTOBER)] == ["WID-1"]
    assert len(fake_atlassian.requests) == 1


def test_a_repeated_page_token_stops_instead_of_looping(fake_atlassian):
    fake_atlassian.answer(SEARCH, {"issues": [], "nextPageToken": "same"})
    fake_atlassian.answer(SEARCH, {"issues": [], "nextPageToken": "same"}, "same")
    with pytest.raises(HeinzelError, match="repeated a page"):
        adapter(fake_atlassian).issues_updated("WID", OCTOBER)
    assert len(fake_atlassian.requests) == 2


def test_too_many_pages_ask_for_a_shorter_window(fake_atlassian):
    fake_atlassian.search(*([] for _ in range(MAX_PAGES + 1)))
    with pytest.raises(HeinzelError, match="shorter window"):
        adapter(fake_atlassian).issues_updated("WID", OCTOBER)
    assert len(fake_atlassian.requests) == MAX_PAGES


def test_an_empty_project(fake_atlassian):
    fake_atlassian.search([])
    assert adapter(fake_atlassian).issues_updated("WID", OCTOBER) == []


def test_a_lower_case_key_is_upper_cased(fake_atlassian):
    fake_atlassian.search([])
    adapter(fake_atlassian).issues_updated(" wid ", OCTOBER)
    assert fake_atlassian.requests[0].query["jql"] == OCTOBER_JQL


@pytest.mark.parametrize(
    "project",
    [
        "W",
        "1WID",
        "WID-1",
        'WID" OR project = "OPS',
        "WID OR 1=1",
        "WI D",
        'W"',
        "W\\",
        "",
        "Ä" * 3,
        "A" * 256,
    ],
)
def test_a_malformed_project_key_is_refused_before_any_request(fake_atlassian, project):
    with pytest.raises(HeinzelError, match="not a Jira project key"):
        adapter(fake_atlassian).issues_updated(project, OCTOBER)
    assert fake_atlassian.requests == []


def test_jql_strings_escape_quotes_and_backslashes():
    assert jql_string("WID") == '"WID"'
    assert jql_string('a"b\\c') == '"a\\"b\\\\c"'


def test_jql_uses_unquoted_epoch_milliseconds_widened_to_whole_minutes():
    since = datetime(2026, 10, 1, 0, 0, 59, 999500, tzinfo=UTC)  # 59.9995 s past the minute
    until = datetime(2026, 10, 2, 0, 0, 0, 1500, tzinfo=UTC)  # 1.5 ms past
    assert search_jql("WID", since, until) == (
        'project = "WID" AND updated >= 1790812800000 AND updated < 1790899260000 ORDER BY updated ASC, key ASC'
    )
    whole = datetime(2026, 10, 1, tzinfo=UTC)
    assert "updated >= 1790812800000 AND updated < 1790812800000" in search_jql("WID", whole, whole)


def test_the_window_stays_exact_inside_the_widened_minutes(fake_atlassian):
    issue = fake_atlassian.issue
    fake_atlassian.search(
        [
            issue(1, updated="2026-10-01T00:00:29.999+0000"),  # in the widened minute, before `since`: out
            issue(2, updated="2026-10-01T00:00:30.000+0000"),  # at `since`
            issue(3, updated="2026-10-01T00:05:14.999+0000"),
            issue(4, updated="2026-10-01T00:05:15.000+0000"),  # at `until`: out
        ]
    )
    window = Window(
        since=datetime(2026, 10, 1, 0, 0, 30, tzinfo=UTC), until=datetime(2026, 10, 1, 0, 5, 15, tzinfo=UTC)
    )
    assert [i.key for i in adapter(fake_atlassian).issues_updated("WID", window)] == ["WID-2", "WID-3"]
    assert "updated >= 1790812800000 AND updated < 1790813160000" in fake_atlassian.requests[0].query["jql"]


def test_an_issue_on_two_pages_is_kept_once_in_its_latest_state(fake_atlassian):
    issue = fake_atlassian.issue
    fake_atlassian.search(
        [
            issue(1, updated="2026-10-02T00:00:00.000+0000", status="To Do"),
            issue(2, updated="2026-10-03T00:00:00.000+0000"),
        ],
        [
            issue(1, updated="2026-10-04T00:00:00.000+0000", status="Done"),
            issue(3, updated="2026-10-04T00:00:00.000+0000"),
        ],
        [issue(1, updated="2026-10-03T12:00:00.000+0000", status="Stale")],
    )
    issues = adapter(fake_atlassian).issues_updated("WID", OCTOBER)
    assert [(i.key, i.state) for i in issues] == [("WID-2", "To Do"), ("WID-1", "Done"), ("WID-3", "To Do")]


def test_an_empty_window_asks_nothing(fake_atlassian):
    window = Window(since=OCTOBER.since, until=OCTOBER.since)
    assert adapter(fake_atlassian, token=None).issues_updated("WID", window) == []
    assert fake_atlassian.requests == []


def test_window_bounds_in_other_timezones_are_the_same_instants(fake_atlassian):
    fake_atlassian.search([fake_atlassian.issue(1, updated="2026-10-01T00:30:00.000+0000")])
    berlin = timezone(timedelta(hours=2))
    window = Window(since=datetime(2026, 10, 1, 2, 0, tzinfo=berlin), until=datetime(2026, 10, 1, 3, 0, tzinfo=berlin))
    assert [i.key for i in adapter(fake_atlassian).issues_updated("WID", window)] == ["WID-1"]
    assert "updated >= 1790812800000 AND updated < 1790816400000" in fake_atlassian.requests[0].query["jql"]


def test_a_naive_window_is_refused(fake_atlassian):
    window = Window(since=datetime(2026, 10, 1), until=datetime(2026, 11, 1))  # noqa: DTZ001 - the point
    with pytest.raises(HeinzelError, match="timezone-aware"):
        adapter(fake_atlassian).issues_updated("WID", window)


def test_a_reversed_window_is_refused(fake_atlassian):
    with pytest.raises(HeinzelError, match="starts"):
        adapter(fake_atlassian).issues_updated("WID", Window(since=OCTOBER.until, until=OCTOBER.since))


@pytest.mark.parametrize(
    ("settings", "token", "named"),
    [
        ({"site_url": None}, SECRET, "atlassian.site_url"),
        ({"email": None}, SECRET, "atlassian.email"),
        ({}, None, "hz system credential set atlassian-token"),
    ],
)
def test_without_site_email_or_token_nothing_is_sent(fake_atlassian, settings, token, named):
    with pytest.raises(HeinzelError, match="no anonymous access") as caught:
        adapter(fake_atlassian, token, **settings).issues_updated("WID", OCTOBER)
    assert named in str(caught.value)
    assert fake_atlassian.requests == []


def test_basic_auth_with_email_and_token(fake_atlassian):
    fake_atlassian.search([])
    adapter(fake_atlassian, timeout=5).issues_updated("WID", OCTOBER)
    request = fake_atlassian.requests[0]
    assert request.headers["Authorization"] == "Basic " + base64.b64encode(f"{EMAIL}:{SECRET}".encode()).decode()
    assert request.timeout == 5


@pytest.mark.parametrize(
    "body",
    [
        {"issues": "nope"},
        {"issues": [1]},
        {},
        {"issues": [{"key": "WID-1"}]},
        {"issues": [{"key": "WID-1", "fields": {"summary": "s", "updated": "yesterday"}}]},
        {"issues": [{"key": "not a key", "fields": {"summary": "s", "updated": "2026-10-02T00:00:00.000+0000"}}]},
    ],
)
def test_an_unexpected_answer_is_a_clear_error(fake_atlassian, body):
    fake_atlassian.answer(SEARCH, body)
    with pytest.raises(HeinzelError, match="Atlassian Cloud site"):
        adapter(fake_atlassian).issues_updated("WID", OCTOBER)


def test_an_issue_without_status_or_labels_still_maps(fake_atlassian):
    fake_atlassian.answer(
        SEARCH, {"issues": [{"key": "WID-7", "fields": {"summary": "s", "updated": "2026-10-02T00:00:00.000+0000"}}]}
    )
    (issue,) = adapter(fake_atlassian).issues_updated("WID", OCTOBER)
    assert (issue.state, issue.assignee, issue.labels) == ("unknown", None, [])


def test_issue_text_is_cleaned_of_controls_and_bidi_overrides(fake_atlassian):
    dirty = "\x00a\x07\tb\nc\r\x1b[31md\x7f\x85e\x9f\u202ef\u2066g\u2069\u202ah"
    item = fake_atlassian.issue(1, updated="2026-10-02T00:00:00.000+0000", status=dirty, assignee=dirty)
    item["fields"]["summary"] = dirty
    item["fields"]["labels"] = [dirty, "\u202e", "ok"]
    fake_atlassian.search([item])
    (issue,) = adapter(fake_atlassian).issues_updated("WID", OCTOBER)
    clean = "a\tb\nc[31mdefgh"
    assert (issue.title, issue.state, issue.assignee, issue.labels) == (clean, clean, clean, [clean, "ok"])


def test_a_status_or_assignee_of_only_controls_counts_as_none(fake_atlassian):
    fake_atlassian.search(
        [fake_atlassian.issue(1, updated="2026-10-02T00:00:00.000+0000", status="\x1b", assignee="\u202e")]
    )
    (issue,) = adapter(fake_atlassian).issues_updated("WID", OCTOBER)
    assert (issue.state, issue.assignee) == ("unknown", None)
