import json

import pytest
from heinzel_atlassian import AtlassianSettings, Confluence
from heinzel_atlassian._confluence import page_id_of

from heinzel import HeinzelError

pytestmark = pytest.mark.unit

SITE = "https://acme.atlassian.net"
VIEW = ["confluence", "page", "view", "--id", "123456", "--body-format", "storage", "--json"]


def adapter(fake, **settings) -> Confluence:
    return Confluence(AtlassianSettings(**({"site_url": SITE} | settings)), fake)


def test_read_a_page_by_id(fake_acli):
    fake_acli.page("123456", title="Widget plan", storage="<h2>Goal</h2><p>Ship <em>it</em>.</p>", version=19)
    document = adapter(fake_acli).read("123456")
    assert document.ref == "123456"
    assert document.title == "Widget plan"
    assert document.text == "## Goal\n\nShip _it_.\n"
    assert document.revision == "19"
    # Built on the configured site; the answer's `_links.base` is ignored.
    assert document.url == f"{SITE}/wiki/spaces/WID/pages/123456/Widget+plan"
    (call,) = fake_acli.calls
    assert call.args == VIEW
    assert call.timeout == 60.0


@pytest.mark.parametrize(
    "ref",
    [
        f"{SITE}/wiki/spaces/WID/pages/123456/Widget+plan",
        f"{SITE}/wiki/spaces/WID/pages/123456",
        f"{SITE}/wiki/spaces/~5b10ac8d/pages/edit-v2/123456",
        f"{SITE}/wiki/pages/viewpage.action?pageId=123456",
        "https://ACME.atlassian.net/wiki/spaces/WID/pages/123456?focusedCommentId=1#comment-1",
        " 123456 ",
    ],
)
def test_page_urls_on_the_configured_site(ref):
    assert page_id_of(ref, SITE) == "123456"


@pytest.mark.parametrize(
    "ref",
    [
        "https://evil.example/wiki/spaces/WID/pages/123456",
        "https://other.atlassian.net/wiki/spaces/WID/pages/123456",
        "https://acme.atlassian.net.evil.example/wiki/spaces/WID/pages/123456",
        "http://acme.atlassian.net/wiki/spaces/WID/pages/123456",
        "https://ada@acme.atlassian.net/wiki/spaces/WID/pages/123456",
        "https://acme.atlassian.net:8443/wiki/spaces/WID/pages/123456",
    ],
)
def test_urls_elsewhere_are_refused_before_any_request(fake_acli, ref):
    with pytest.raises(HeinzelError, match=f"not a page on {SITE}"):
        adapter(fake_acli).read(ref)
    assert fake_acli.calls == []


@pytest.mark.parametrize(
    ("ref", "message"),
    [
        ("Widget plan", "neither a Confluence page id"),
        ("../123", "neither a Confluence page id"),
        (f"{SITE}/wiki/x/AbCdEf", "short link"),
        (f"{SITE}/wiki/spaces/WID/overview", "not a Confluence page URL"),
        (f"{SITE}/wiki/pages/viewpage.action?pageId=1&pageId=2", "not a Confluence page URL"),
        (f"{SITE}/wiki/pages/viewpage.action?pageId=../1", "not a Confluence page URL"),
        (f"{SITE}/browse/WID-1", "not a Confluence page URL"),
    ],
)
def test_other_refs_are_refused_with_the_shape_to_use(fake_acli, ref, message):
    with pytest.raises(HeinzelError, match=message):
        adapter(fake_acli).read(ref)
    assert fake_acli.calls == []


def test_a_url_without_a_configured_site_is_refused():
    with pytest.raises(HeinzelError, match=r"atlassian\.site_url is not set"):
        page_id_of(f"{SITE}/wiki/spaces/WID/pages/1", None)


def test_the_sample_page_maps_to_a_document(fake_acli):
    fake_acli.answer(fake_acli.PAGE, fake_acli.sample("confluence_page_view.json"))
    document = adapter(fake_acli).read("123456")
    assert (document.title, document.text, document.revision) == ("Widget plan", "# Goal\n\nShip the widget.\n", "3")
    assert document.url == f"{SITE}/wiki/spaces/WID/pages/123456/Widget+plan"


def test_without_a_site_acli_does_not_run(fake_acli):
    with pytest.raises(HeinzelError, match=r"hz system config set atlassian\.site_url"):
        adapter(fake_acli, site_url=None).read("123456")
    assert fake_acli.calls == []


def test_logged_out_names_the_confluence_login(fake_acli):
    fake_acli.fail(fake_acli.PAGE, "✗ Error: unauthorized: use 'acli confluence auth login' to authenticate\n")
    with pytest.raises(HeinzelError, match="Log in: acli confluence auth login"):
        adapter(fake_acli).read("123456")


def test_an_answer_for_another_page_is_refused(fake_acli):
    fake_acli.page("654321")
    with pytest.raises(HeinzelError, match="answered page 123456 with page '654321'"):
        adapter(fake_acli).read("123456")


def test_a_page_url_keeps_its_ref(fake_acli):
    fake_acli.page("123456")
    ref = f"{SITE}/wiki/spaces/WID/pages/123456/Plan"
    assert adapter(fake_acli).read(ref).ref == ref


def test_without_webui_link_or_version_the_url_falls_back_to_the_page_id(fake_acli):
    fake_acli.answer(
        fake_acli.PAGE, {"title": "T", "body": {"storage": {"value": "<p>x</p>"}}, "_links": {"webui": "//x"}}
    )
    document = adapter(fake_acli).read("123456")
    assert document.url == f"{SITE}/wiki/pages/viewpage.action?pageId=123456"
    assert document.revision is None


@pytest.mark.parametrize(
    "body",
    [
        [],
        42,
        {"title": "T"},
        {"title": "T", "body": {"view": {}}},
        {"body": {"storage": {}}},
        {"title": 1, "body": {"storage": {"value": "<p>x</p>"}}},
        {"title": "T", "body": {"storage": {"value": {"nested": "x"}}}},
        {"title": "T", "body": "x"},
    ],
)
def test_an_unexpected_page_is_a_clear_error(fake_acli, body):
    fake_acli.answer(fake_acli.PAGE, body)
    with pytest.raises(HeinzelError, match="lacks the title or storage body"):
        adapter(fake_acli).read("123456")


def test_a_missing_page_names_the_access_fix(fake_acli):
    fake_acli.fail(fake_acli.PAGE, "✗ Error: page not found (404)\n")
    with pytest.raises(HeinzelError, match="Confluence has no page 123456, or the account acli is logged in as"):
        adapter(fake_acli).read("123456")


def test_page_title_text_and_url_are_cleaned_of_controls_and_bidi_overrides(fake_acli):
    fake_acli.page("123456", title="Plan\u202e\x1b", storage="<p>Hi\x00 \u2066there\x9b</p><pre>a\x07\tb</pre>")
    document = adapter(fake_acli).read("123456")
    assert document.title == "Plan"
    assert document.text == "Hi there\n\n```\na\tb\n```\n"
    assert document.url == f"{SITE}/wiki/spaces/WID/pages/123456/Plan%E2%80%AE%1B"  # percent-encoded, so inert


def test_a_page_from_another_site_is_refused_with_both_fixes(fake_acli):
    page = fake_acli.sample("confluence_page_view.json")
    page["_links"]["base"] = "https://other.atlassian.net/wiki"
    fake_acli.answer(fake_acli.PAGE, page)
    with pytest.raises(HeinzelError) as caught:
        adapter(fake_acli).read("123456")
    assert "acli answered from other.atlassian.net, not https://acme.atlassian.net" in str(caught.value)
    assert "acli confluence auth switch" in str(caught.value)
    assert "hz system config set atlassian.site_url https://other.atlassian.net" in str(caught.value)


def test_a_page_through_the_gateway_is_accepted(fake_acli):
    page = fake_acli.sample("confluence_page_view.json")
    page["_links"]["base"] = "https://api.atlassian.com/ex/confluence/0f1e2d3c/wiki"
    fake_acli.answer(fake_acli.PAGE, page)
    assert adapter(fake_acli).read("123456").title == "Widget plan"


@pytest.mark.parametrize(
    "webui",
    [
        "/spaces/WID/pages/654321/Other",  # another page
        "/spaces/WID/pages/123456/[click](https://evil.example)",
        "/spaces/W I D/pages/123456/Plan",
        "/x/AbC",
        "//evil.example/spaces/WID/pages/123456",
    ],
)
def test_a_link_of_another_shape_falls_back_to_the_page_id(fake_acli, webui):
    page = fake_acli.sample("confluence_page_view.json")
    page["_links"]["webui"] = webui
    fake_acli.answer(fake_acli.PAGE, page)
    assert adapter(fake_acli).read("123456").url == f"{SITE}/wiki/pages/viewpage.action?pageId=123456"


def test_two_documents_for_one_page_are_refused(fake_acli):
    page = json.dumps(fake_acli.sample("confluence_page_view.json"))
    fake_acli.answer(fake_acli.PAGE, f"{page}\n{page}")
    with pytest.raises(HeinzelError, match="lacks the title or storage body"):
        adapter(fake_acli).read("123456")
