import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from heinzel_atlassian import AtlassianSettings, Jira
from heinzel_atlassian._jira import MAX_ISSUES, jql_string, search_jql

from heinzel import HeinzelError, Window

pytestmark = pytest.mark.unit

SITE = "https://acme.atlassian.net"
OCTOBER = Window(since=datetime(2026, 10, 1, tzinfo=UTC), until=datetime(2026, 11, 1, tzinfo=UTC))
OCTOBER_JQL = 'project = "WID" AND updated >= 1790812800000 AND updated < 1793491200000 ORDER BY updated ASC, key ASC'


def adapter(fake, **settings) -> Jira:
    return Jira(AtlassianSettings(**({"site_url": SITE} | settings)), fake)


def test_the_sample_search_maps_to_issues(fake_acli):
    fake_acli.search(fake_acli.sample("jira_workitem_search.json"))
    issues = adapter(fake_acli).issues_updated("WID", OCTOBER)
    assert [(i.key, i.title, i.state, i.assignee, i.labels) for i in issues] == [
        ("WID-3", "Widget export drops the last row", "In Progress", "Wilma Widget", ["export", "ui"]),
        ("WID-4", "Document the widget API", "Done", None, []),
    ]
    assert issues[0].updated_at == datetime(2026, 10, 5, 10, tzinfo=UTC)
    assert issues[0].url == f"{SITE}/browse/WID-3"  # built on the site, not the answer's `self`


def test_acli_is_asked_with_an_argument_list_epoch_jql_and_the_timeout(fake_acli):
    fake_acli.search([])
    adapter(fake_acli, timeout=5).issues_updated("WID", OCTOBER)
    (call,) = fake_acli.calls
    assert call.args == [
        "jira",
        "workitem",
        "search",
        "--jql",
        OCTOBER_JQL,
        "--fields",
        "key,summary,status,updated,assignee,labels",
        "--paginate",
        "--json",
    ]
    assert call.timeout == 5


def test_issues_updated_in_the_window_oldest_first(fake_acli):
    issue = fake_acli.issue
    fake_acli.search(
        [
            issue(1, updated="2026-09-30T23:59:59.999+0000"),  # just before the window: out
            issue(3, updated="2026-10-05T12:34:56.789+0200", labels=("backend", "urgent")),
            issue(2, updated="2026-10-01T02:00:00.000+0200", status="Done", assignee="Wilma Widget"),  # at `since`
            issue(4, updated="2026-11-01T00:00:00.000+0000"),  # at `until`: out
        ]
    )
    issues = adapter(fake_acli).issues_updated("WID", OCTOBER)
    assert [(i.key, i.state, i.assignee, i.labels) for i in issues] == [
        ("WID-2", "Done", "Wilma Widget", []),
        ("WID-3", "To Do", None, ["backend", "urgent"]),
    ]
    assert issues[1].updated_at == datetime(2026, 10, 5, 10, 34, 56, 789000, tzinfo=UTC)


def test_an_answer_with_an_issues_object_is_read_too(fake_acli):
    fake_acli.search({"issues": [fake_acli.issue(1, updated="2026-10-02T00:00:00.000+0000")], "isLast": True})
    assert [i.key for i in adapter(fake_acli).issues_updated("WID", OCTOBER)] == ["WID-1"]


def test_too_many_issues_ask_for_a_shorter_window(fake_acli):
    fake_acli.search([fake_acli.issue(n, updated="2026-10-02T00:00:00.000+0000") for n in range(1, MAX_ISSUES + 2)])
    with pytest.raises(HeinzelError, match="shorter window"):
        adapter(fake_acli).issues_updated("WID", OCTOBER)


def test_exactly_the_most_issues_are_all_returned(fake_acli):
    fake_acli.search([fake_acli.issue(n, updated="2026-10-02T00:00:00.000+0000") for n in range(1, MAX_ISSUES + 1)])
    assert len(adapter(fake_acli).issues_updated("WID", OCTOBER)) == MAX_ISSUES


def test_pages_printed_back_to_back_are_read_as_one_answer(fake_acli):
    first = [fake_acli.issue(1, updated="2026-10-02T00:00:00.000+0000")]
    second = {"issues": [fake_acli.issue(2, updated="2026-10-03T00:00:00.000+0000")]}
    fake_acli.answer(fake_acli.SEARCH, f"{json.dumps(first)}\n{json.dumps(second)}\n")
    assert [i.key for i in adapter(fake_acli).issues_updated("WID", OCTOBER)] == ["WID-1", "WID-2"]


def test_an_issue_of_another_project_is_refused(fake_acli):
    fake_acli.search([fake_acli.issue(1, updated="2026-10-02T00:00:00.000+0000", project="OPS")])
    with pytest.raises(HeinzelError, match="is acli up to date"):
        adapter(fake_acli).issues_updated("WID", OCTOBER)


def test_issues_from_another_site_are_refused_with_both_fixes(fake_acli):
    item = fake_acli.issue(1, updated="2026-10-02T00:00:00.000+0000")
    item["self"] = "https://other.atlassian.net/rest/api/3/issue/10001"
    fake_acli.search([item])
    with pytest.raises(HeinzelError) as caught:
        adapter(fake_acli).issues_updated("WID", OCTOBER)
    assert "acli answered from other.atlassian.net" in str(caught.value)
    assert "acli jira auth switch" in str(caught.value)


@pytest.mark.parametrize(
    "link", ["https://api.atlassian.com/ex/jira/0f1e2d3c/rest/api/3/issue/10001", None, 7, "", f"{SITE}/rest/x"]
)
def test_issues_through_the_gateway_or_without_a_link_are_accepted(fake_acli, link):
    item = fake_acli.issue(1, updated="2026-10-02T00:00:00.000+0000")
    item["self"] = link
    fake_acli.search([item])
    assert [i.key for i in adapter(fake_acli).issues_updated("WID", OCTOBER)] == ["WID-1"]


@pytest.mark.parametrize(
    "link", [f"{SITE}.evil.example/rest/1", "https://acme.atlassian.net:8443/rest/1", "not a url at all"]
)
def test_links_that_only_look_like_the_site_are_refused(fake_acli, link):
    item = fake_acli.issue(1, updated="2026-10-02T00:00:00.000+0000")
    item["self"] = link
    fake_acli.search([item])
    with pytest.raises(HeinzelError, match="acli answered from"):
        adapter(fake_acli).issues_updated("WID", OCTOBER)


def test_an_empty_project(fake_acli):
    fake_acli.search([])
    assert adapter(fake_acli).issues_updated("WID", OCTOBER) == []


def test_a_lower_case_key_is_upper_cased(fake_acli):
    fake_acli.search([])
    adapter(fake_acli).issues_updated(" wid ", OCTOBER)
    assert fake_acli.calls[0].args[4] == OCTOBER_JQL


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
        "--help",
        "",
        "Ä" * 3,
        "A" * 256,
    ],
)
def test_a_malformed_project_key_is_refused_before_acli_runs(fake_acli, project):
    with pytest.raises(HeinzelError, match="not a Jira project key"):
        adapter(fake_acli).issues_updated(project, OCTOBER)
    assert fake_acli.calls == []


def test_jql_strings_escape_quotes_and_backslashes():
    assert jql_string("WID") == '"WID"'
    assert jql_string('a"b\\c') == '"a\\"b\\\\c"'


def test_jql_uses_unquoted_epoch_milliseconds_widened_to_whole_minutes():
    since = datetime(2026, 10, 1, 0, 0, 59, 999500, tzinfo=UTC)
    until = datetime(2026, 10, 2, 0, 0, 0, 1500, tzinfo=UTC)
    assert search_jql("WID", since, until) == (
        'project = "WID" AND updated >= 1790812800000 AND updated < 1790899260000 ORDER BY updated ASC, key ASC'
    )


def test_the_window_stays_exact_inside_the_widened_minutes(fake_acli):
    issue = fake_acli.issue
    fake_acli.search(
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
    assert [i.key for i in adapter(fake_acli).issues_updated("WID", window)] == ["WID-2", "WID-3"]
    assert "updated >= 1790812800000 AND updated < 1790813160000" in fake_acli.calls[0].args[4]


def test_an_issue_listed_twice_is_kept_once_in_its_latest_state(fake_acli):
    issue = fake_acli.issue
    fake_acli.search(
        [
            issue(1, updated="2026-10-02T00:00:00.000+0000", status="To Do"),
            issue(2, updated="2026-10-03T00:00:00.000+0000"),
            issue(1, updated="2026-10-04T00:00:00.000+0000", status="Done"),
            issue(1, updated="2026-10-03T12:00:00.000+0000", status="Stale"),
        ]
    )
    issues = adapter(fake_acli).issues_updated("WID", OCTOBER)
    assert [(i.key, i.state) for i in issues] == [("WID-2", "To Do"), ("WID-1", "Done")]


def test_an_empty_window_asks_nothing(fake_acli):
    window = Window(since=OCTOBER.since, until=OCTOBER.since)
    assert adapter(fake_acli, site_url=None).issues_updated("WID", window) == []
    assert fake_acli.calls == []


def test_window_bounds_in_other_timezones_are_the_same_instants(fake_acli):
    fake_acli.search([fake_acli.issue(1, updated="2026-10-01T00:30:00.000+0000")])
    berlin = timezone(timedelta(hours=2))
    window = Window(since=datetime(2026, 10, 1, 2, 0, tzinfo=berlin), until=datetime(2026, 10, 1, 3, 0, tzinfo=berlin))
    assert [i.key for i in adapter(fake_acli).issues_updated("WID", window)] == ["WID-1"]
    assert "updated >= 1790812800000 AND updated < 1790816400000" in fake_acli.calls[0].args[4]


def test_a_reversed_window_is_refused(fake_acli):
    with pytest.raises(HeinzelError, match="starts"):
        adapter(fake_acli).issues_updated("WID", Window(since=OCTOBER.until, until=OCTOBER.since))


def test_without_a_site_acli_does_not_run(fake_acli):
    with pytest.raises(HeinzelError, match=r"hz system config set atlassian\.site_url"):
        adapter(fake_acli, site_url=None).issues_updated("WID", OCTOBER)
    assert fake_acli.calls == []


def test_logged_out_names_the_login(fake_acli):
    fake_acli.fail(fake_acli.SEARCH, fake_acli.UNAUTHORIZED)
    with pytest.raises(HeinzelError, match="Log in: acli jira auth login"):
        adapter(fake_acli).issues_updated("WID", OCTOBER)


@pytest.mark.parametrize(
    "body",
    [
        "nope",
        {"issues": "nope"},
        [1],
        {},
        [{"key": "WID-1"}],
        [{"key": "WID-1", "fields": {"summary": "s", "updated": "yesterday"}}],
        [{"key": "not a key", "fields": {"summary": "s", "updated": "2026-10-02T00:00:00.000+0000"}}],
        [{"key": "WID-1", "fields": {"summary": "s", "updated": "2026-10-02T00:00:00"}}],  # naive time
        [{"key": "WID-1", "fields": {"summary": "s", "updated": 1790812800000}}],
        [{"key": "WID-1", "fields": {"summary": {"text": "s"}, "updated": "2026-10-02T00:00:00.000+0000"}}],
        [{"key": "WID-1", "fields": {"summary": "s", "updated": "2026-10-02T00:00:00.000+0000", "labels": "x"}}],
        [{"key": 1, "fields": {}}],
    ],
)
def test_an_unexpected_answer_is_a_clear_error(fake_acli, body):
    fake_acli.search(body)
    with pytest.raises(HeinzelError, match="is acli up to date"):
        adapter(fake_acli).issues_updated("WID", OCTOBER)


def test_an_issue_without_status_or_labels_still_maps(fake_acli):
    fake_acli.search([{"key": "WID-7", "fields": {"summary": "s", "updated": "2026-10-02T00:00:00.000+0000"}}])
    (issue,) = adapter(fake_acli).issues_updated("WID", OCTOBER)
    assert (issue.state, issue.assignee, issue.labels) == ("unknown", None, [])


def test_issue_text_is_cleaned_of_controls_and_bidi_overrides(fake_acli):
    dirty = "\x00a\x07\tb\nc\r\x1b[31md\x7f\x85e\x9f\u202ef\u2066g\u2069\u202ah\u200b\U000e0041"
    item = fake_acli.issue(1, updated="2026-10-02T00:00:00.000+0000", status=dirty, assignee=dirty)
    item["fields"]["summary"] = dirty
    item["fields"]["labels"] = [dirty, "\u202e", "ok"]
    fake_acli.search([item])
    (issue,) = adapter(fake_acli).issues_updated("WID", OCTOBER)
    clean = "a\tb\nc[31mdefgh"
    assert (issue.title, issue.state, issue.assignee, issue.labels) == (clean, clean, clean, [clean, "ok"])
