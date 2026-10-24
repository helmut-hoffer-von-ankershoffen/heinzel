import base64

import pytest
from heinzel_atlassian import Response
from heinzel_atlassian._client import AtlassianClient, clean
from heinzel_atlassian._transport import MAX_BYTES

from heinzel import HeinzelError

pytestmark = pytest.mark.unit

SITE = "https://acme.atlassian.net"
EMAIL = "ada@acme.example"
SECRET = "fake-token-client-secret"
BASIC = base64.b64encode(f"{EMAIL}:{SECRET}".encode()).decode()
PATH = "/rest/api/3/search/jql"


def get(fake, product: str = "Jira") -> dict:
    return AtlassianClient(SITE, EMAIL, SECRET, fake, 7.0).get(product, PATH, {"jql": "x"}, "project WID")


def message(fake, product: str = "Jira") -> str:
    with pytest.raises(HeinzelError) as caught:
        get(fake, product)
    text = str(caught.value)
    assert SECRET not in text
    assert BASIC not in text
    return text


def test_requests_carry_basic_auth_json_and_the_plugin(fake_atlassian):
    fake_atlassian.answer(PATH, {"issues": []})
    assert get(fake_atlassian) == {"issues": []}
    request = fake_atlassian.requests[0]
    assert request.headers["Authorization"] == f"Basic {BASIC}"
    assert request.headers["Accept"] == "application/json"
    assert request.headers["User-Agent"].startswith("heinzel-atlassian/")
    assert request.timeout == 7.0
    assert request.url == f"{SITE}{PATH}?jql=x"


def test_the_rate_limit_says_when_to_retry(fake_atlassian):
    fake_atlassian.fail(PATH, 429, {"message": "Rate limit exceeded"}, {"retry-after": "30"})
    assert message(fake_atlassian) == "Atlassian's rate limit is reached for project WID: retry after 30 seconds."
    assert len(fake_atlassian.requests) == 1  # no retry, no sleep


def test_the_rate_limit_without_retry_after(fake_atlassian):
    fake_atlassian.fail(PATH, 429, {}, {"retry-after": "soon"})
    assert "retry in a minute" in message(fake_atlassian)


@pytest.mark.parametrize("echo", [SECRET, BASIC, f"{EMAIL}:{SECRET}", f"Basic {BASIC}"])
def test_a_rejected_token_is_named_never_shown(fake_atlassian, echo):
    fake_atlassian.fail(PATH, 401, {"errorMessages": [f"Bad credentials: {echo}"]})
    text = message(fake_atlassian)
    assert "rejected the email and token" in text
    assert "hz system credential set atlassian-token" in text
    assert "atlassian.email" in text
    assert "without scopes" in text
    assert "***" in text


def test_forbidden_names_the_permission_per_product(fake_atlassian):
    fake_atlassian.fail(PATH, 403, {"errorMessages": ["You do not have permission"]})
    assert "Browse projects" in message(fake_atlassian)
    assert "You do not have permission" in message(fake_atlassian)
    fake_atlassian.fail(PATH, 403, {"errors": [{"title": "Forbidden", "detail": "No access to space"}]})
    text = message(fake_atlassian, "Confluence")
    assert "View on its space" in text
    assert "No access to space" in text


def test_not_found_names_access(fake_atlassian):
    fake_atlassian.fail(PATH, 404, {})
    assert "or ask for access for the account in atlassian.email" in message(fake_atlassian)


def test_an_unknown_project_in_jql_is_a_bad_request_with_jiras_words(fake_atlassian):
    fake_atlassian.fail(
        PATH,
        400,
        {"errorMessages": ["The value 'WID' does not exist for the field 'project'."], "errors": {"jql": "bad"}},
    )
    text = message(fake_atlassian)
    assert "400: The value 'WID' does not exist for the field 'project'.; bad" in text
    assert "check that it exists and is visible" in text


@pytest.mark.parametrize(
    ("body", "said"),
    [
        (b"<html>oops</html>", "no details"),
        (b"[1]", "no details"),
        (b'{"message": "Internal"}', "Internal"),
        (b'{"errors": [{"status": 500}]}', "no details"),
    ],
)
def test_other_errors_show_the_status_and_no_raw_body(fake_atlassian, body, said):
    fake_atlassian.routes[PATH, None] = Response(502, {}, body)
    text = message(fake_atlassian)
    assert "502" in text
    assert said in text
    assert "oops" not in text


def test_an_unreachable_site_is_a_clear_error(fake_atlassian):
    fake_atlassian.error = TimeoutError("timed out")
    assert f"Could not reach {SITE}: TimeoutError" in message(fake_atlassian)


def test_an_answer_that_is_not_a_json_object_is_refused(fake_atlassian):
    fake_atlassian.routes[PATH, None] = Response(200, {}, b"[]")
    assert "something other than an object" in message(fake_atlassian)
    fake_atlassian.routes[PATH, None] = Response(200, {}, b"<html/>")
    assert "not JSON" in message(fake_atlassian)


def test_an_oversized_answer_is_refused(fake_atlassian):
    fake_atlassian.routes[PATH, None] = Response(200, {}, b" " * (MAX_BYTES + 1))
    assert "larger than" in message(fake_atlassian)


DIRTY = "\x00a\x07\tb\nc\r\x1b[31md\x7f\x85e\x9f\u202ef\u2066g\u2069\u202ah"


def test_clean_removes_controls_and_bidi_overrides_but_keeps_newlines_and_tabs():
    assert clean(DIRTY) == "a\tb\nc[31mdefgh"
    assert clean("Grüße, 東京 \u2013 ok") == "Grüße, 東京 \u2013 ok"


def test_atlassians_words_in_an_error_are_cleaned(fake_atlassian):
    fake_atlassian.fail(PATH, 403, {"errorMessages": [DIRTY]})
    assert "(403: a\tb\nc[31mdefgh)" in message(fake_atlassian)


@pytest.mark.parametrize("secret", [SECRET, BASIC, f"{EMAIL}:{SECRET}"])
@pytest.mark.parametrize("cut", [3, 10, len(SECRET) - 3])
def test_a_secret_across_the_cut_is_redacted_before_it(fake_atlassian, secret, cut):
    prefix = "x" * (300 - cut)  # the cut at 300 characters falls `cut` characters into the secret
    fake_atlassian.fail(PATH, 403, {"errorMessages": [prefix + secret + " and more"]})
    text = message(fake_atlassian)
    assert prefix + "***" in text
    assert all(secret[i : i + 3] not in text.split(prefix, 1)[1] for i in range(len(secret) - 2))
