import pytest
from heinzel_atlassian import TOKEN, AtlassianSettings, Confluence
from heinzel_atlassian._confluence import page_id_of

from heinzel import Credentials, HeinzelError
from heinzel.testing import MemoryBackend

pytestmark = pytest.mark.unit

SITE = "https://acme.atlassian.net"
EMAIL = "ada@acme.example"
SECRET = "fake-token-confluence-secret"
PAGE = "/wiki/api/v2/pages/123456"


def adapter(fake, token: str | None = SECRET, **settings) -> Confluence:
    credentials = Credentials(MemoryBackend(), [("atlassian", TOKEN)])
    if token:
        credentials.set(TOKEN, token)
    return Confluence(AtlassianSettings(**({"site_url": SITE, "email": EMAIL} | settings)), credentials, fake)


def test_read_a_page_by_id(fake_atlassian):
    fake_atlassian.page("123456", title="Widget plan", storage="<h2>Goal</h2><p>Ship <em>it</em>.</p>", version=19)
    document = adapter(fake_atlassian).read("123456")
    assert document.ref == "123456"
    assert document.title == "Widget plan"
    assert document.text == "## Goal\n\nShip _it_.\n"
    assert document.revision == "19"
    # Built on the configured site; the answer's `_links.base` is ignored.
    assert document.url == f"{SITE}/wiki/spaces/WID/pages/123456/Widget plan"
    request = fake_atlassian.requests[0]
    assert request.path == PAGE
    assert request.query == {"body-format": "storage"}


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
def test_urls_elsewhere_are_refused_before_any_request(fake_atlassian, ref):
    with pytest.raises(HeinzelError, match=f"not a page on {SITE}"):
        adapter(fake_atlassian).read(ref)
    assert fake_atlassian.requests == []


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
def test_other_refs_are_refused_with_the_shape_to_use(fake_atlassian, ref, message):
    with pytest.raises(HeinzelError, match=message):
        adapter(fake_atlassian).read(ref)
    assert fake_atlassian.requests == []


def test_a_url_without_a_configured_site_is_refused():
    with pytest.raises(HeinzelError, match=r"atlassian\.site_url is not set"):
        page_id_of(f"{SITE}/wiki/spaces/WID/pages/1", None)


def test_without_credentials_nothing_is_sent(fake_atlassian):
    with pytest.raises(HeinzelError, match="hz system credential set atlassian-token"):
        adapter(fake_atlassian, token=None).read("123456")
    assert fake_atlassian.requests == []


def test_a_page_url_keeps_its_ref(fake_atlassian):
    fake_atlassian.page("123456")
    ref = f"{SITE}/wiki/spaces/WID/pages/123456/Plan"
    assert adapter(fake_atlassian).read(ref).ref == ref


def test_without_webui_link_or_version_the_url_falls_back_to_the_page_id(fake_atlassian):
    fake_atlassian.answer(PAGE, {"title": "T", "body": {"storage": {"value": "<p>x</p>"}}, "_links": {"webui": "//x"}})
    document = adapter(fake_atlassian).read("123456")
    assert document.url == f"{SITE}/wiki/pages/viewpage.action?pageId=123456"
    assert document.revision is None


@pytest.mark.parametrize("body", [{"title": "T"}, {"title": "T", "body": {"view": {}}}, {"body": {"storage": {}}}])
def test_an_unexpected_page_is_a_clear_error(fake_atlassian, body):
    fake_atlassian.answer(PAGE, body)
    with pytest.raises(HeinzelError, match="lacks the title or body"):
        adapter(fake_atlassian).read("123456")


def test_a_missing_page_names_the_access_fix(fake_atlassian):
    fake_atlassian.fail(PAGE, 404, {"errors": [{"status": 404, "code": "NOT_FOUND", "title": "Not Found"}]})
    with pytest.raises(HeinzelError, match="Confluence has no page 123456, or the account can't see it"):
        adapter(fake_atlassian).read("123456")


def test_page_title_text_and_url_are_cleaned_of_controls_and_bidi_overrides(fake_atlassian):
    fake_atlassian.page("123456", title="Plan\u202e\x1b", storage="<p>Hi\x00 \u2066there\x9b</p><pre>a\x07\tb</pre>")
    document = adapter(fake_atlassian).read("123456")
    assert document.title == "Plan"
    assert document.text == "Hi there\n\n```\na\tb\n```\n"
    assert document.url == f"{SITE}/wiki/spaces/WID/pages/123456/Plan"
