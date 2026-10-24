import http.client
import urllib.error

import pytest
from heinzel_github import GITHUB_API, Response
from heinzel_github._client import MAX_PAGES, TOKEN_FIX, GitHubClient, next_link
from heinzel_github._transport import MAX_BYTES

from heinzel import HeinzelError

pytestmark = pytest.mark.unit

PATH = "/repos/acme/widgets/issues"
SECRET = "ghp_secret-value"


def chained() -> OSError:
    """What Transport raises for a broken answer: an OSError caused by http.client's exception."""
    error = OSError(http.client.IncompleteRead(b""))
    error.__cause__ = http.client.IncompleteRead(b"")
    return error


def items(fake, token: str | None = None) -> list:
    client = GitHubClient(GITHUB_API, token, fake, 7.0)
    return [item for page in client.pages(PATH, {"per_page": "100"}) for item in page]


def message(fake, token: str | None = None) -> str:
    with pytest.raises(HeinzelError) as caught:
        items(fake, token)
    return str(caught.value)


def test_requests_name_the_api_version_and_the_plugin(fake_github):
    fake_github.serve(PATH, [{"id": 1}])
    assert items(fake_github) == [{"id": 1}]
    headers = fake_github.requests[0].headers
    assert headers["Accept"] == "application/vnd.github+json"
    assert headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert headers["User-Agent"].startswith("heinzel-github/")
    assert fake_github.requests[0].timeout == 7.0


def test_the_rate_limit_says_when_it_resets_and_suggests_a_token_when_anonymous(fake_github):
    fake_github.fail(
        PATH,
        403,
        {"message": "API rate limit exceeded"},
        {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1793534400"},
    )
    text = message(fake_github)
    assert "rate limit is reached" in text
    assert "2026-11-01 12:00 UTC" in text
    assert "hz system credential set github-token" in text


def test_the_rate_limit_with_a_token_does_not_suggest_one(fake_github):
    fake_github.fail(PATH, 429, {}, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "soon"})
    text = message(fake_github, SECRET)
    assert "resets at an unknown time" in text
    assert "credential set" not in text


def test_a_secondary_rate_limit_says_when_to_retry(fake_github):
    fake_github.fail(PATH, 403, {"message": "secondary rate limit"}, {"retry-after": "60"})
    assert "retry after 60 seconds" in message(fake_github, SECRET)


def test_a_secondary_rate_limit_without_headers_is_recognised_by_its_message(fake_github):
    fake_github.fail(PATH, 403, {"message": "You have exceeded a secondary Rate Limit."})
    text = message(fake_github, SECRET)
    assert "rate limit is reached" in text
    assert "wait at least a minute" in text


def test_too_many_requests_is_a_rate_limit_even_without_headers(fake_github):
    fake_github.fail(PATH, 429, {})
    assert "rate limit is reached" in message(fake_github, SECRET)


def test_a_rejected_token_is_named_never_shown(fake_github):
    fake_github.fail(PATH, 401, {"message": f"Bad credentials: {SECRET}"})
    text = message(fake_github, SECRET)
    assert "rejected the token" in text
    assert "hz system credential set github-token" in text
    assert SECRET not in text
    assert "Bad credentials: ***" in text


def test_redaction_covers_a_token_that_straddles_the_cut(fake_github):
    fake_github.fail(PATH, 401, {"message": "x" * 295 + SECRET + " tail"})
    text = message(fake_github, SECRET)
    assert SECRET[:6] not in text
    assert "x***" in text


def test_a_token_is_stripped_of_surrounding_whitespace_before_use(fake_github):
    fake_github.serve(PATH, [])
    items(fake_github, "tok\r\n")
    assert fake_github.requests[0].headers["Authorization"] == "Bearer tok"


def test_a_blank_token_means_anonymous(fake_github):
    fake_github.serve(PATH, [])
    items(fake_github, " \n")
    assert "Authorization" not in fake_github.requests[0].headers


@pytest.mark.parametrize("token", ["to\rk", "tok\r\nX-Injected: 1", "tok\x00", "tök", "tok\tx"])
def test_a_token_that_is_not_one_line_of_printable_ascii_is_refused_without_showing_it(fake_github, token):
    with pytest.raises(HeinzelError) as caught:
        GitHubClient(GITHUB_API, token, fake_github, 7.0)
    text = str(caught.value)
    assert TOKEN_FIX in text
    assert "printable ASCII" in text
    assert token not in text
    assert repr(token) not in text
    assert "tok" not in text.replace("token", "")
    assert fake_github.requests == []


def test_not_found_suggests_a_token_when_anonymous_and_access_when_not(fake_github):
    fake_github.fail(PATH, 404, {"message": "Not Found"})
    assert "private repositories need a token" in message(fake_github)
    assert "grant it access" in message(fake_github, SECRET)


def test_forbidden_names_the_permissions_needed(fake_github):
    fake_github.fail(PATH, 403, {"message": "Resource not accessible by personal access token"})
    text = message(fake_github, SECRET)
    assert "metadata, pull requests and issues" in text
    assert "Resource not accessible" in text


def test_forbidden_without_a_token_says_none_is_stored(fake_github):
    fake_github.fail(PATH, 403, {"message": "Forbidden"})
    text = message(fake_github)
    assert "no token is stored" in text
    assert TOKEN_FIX in text
    assert "the token needs" not in text


@pytest.mark.parametrize(
    ("location", "shown"),
    [
        ("https://user:pw@evil.example:8443/x?page=2", "https://evil.example:8443"),
        ("http://api.github.com/x", "http://api.github.com"),
        ("", "another address"),
        ("https://evil.example:bad/x", "https://evil.example"),
    ],
)
def test_a_refused_redirect_says_it_left_the_origin(fake_github, location, shown):
    fake_github.fail(PATH, 302, {}, {"location": location} if location else {})
    text = message(fake_github, SECRET)
    assert f"redirected {PATH} to {shown}, away from https://api.github.com" in text
    assert "pw" not in text
    assert "page=2" not in text


@pytest.mark.parametrize(("body", "said"), [(b"<html>oops</html>", "no details"), (b"[1]", "no details")])
def test_other_errors_show_the_status_and_no_raw_body(fake_github, body, said):
    fake_github.routes[PATH] = [Response(502, {}, body)]
    text = message(fake_github)
    assert "502" in text
    assert said in text
    assert "oops" not in text


@pytest.mark.parametrize(
    ("error", "said"),
    [
        (TimeoutError("timed out"), "TimeoutError: timed out"),
        (TimeoutError(), "TimeoutError."),
        (urllib.error.URLError("no route"), "URLError: no route"),
        (urllib.error.URLError(ConnectionRefusedError(61, "refused")), "ConnectionRefusedError: [Errno 61] refused"),
        (chained(), "IncompleteRead: IncompleteRead(0 bytes read)"),
    ],
)
def test_an_unreachable_host_names_the_original_error(fake_github, error, said):
    fake_github.error = error
    assert f"Could not reach https://api.github.com: {said}" in message(fake_github)


def test_an_answer_that_is_not_a_list_of_objects_is_refused(fake_github):
    fake_github.routes[PATH] = [Response(200, {}, b'{"message": "hi"}')]
    assert "something other than a list" in message(fake_github)
    fake_github.routes[PATH] = [Response(200, {}, b"not json")]
    assert "not JSON" in message(fake_github)


def test_an_oversized_answer_is_refused(fake_github):
    fake_github.routes[PATH] = [Response(200, {}, b" " * (MAX_BYTES + 1))]
    assert "larger than" in message(fake_github)


@pytest.mark.parametrize(
    "link", ["https://evil.example/x?page=2", "https://api.github.com.evil.example/repos/acme/widgets/issues?page=2"]
)
def test_a_next_link_to_another_host_is_not_followed(fake_github, link):
    fake_github.routes[PATH] = [Response(200, {"link": f'<{link}>; rel="next"'}, b"[]")]
    assert "refusing to follow" in message(fake_github, SECRET)
    assert len(fake_github.requests) == 1


def test_too_many_pages_ask_for_a_shorter_window(fake_github):
    fake_github.serve(PATH, *([{"id": n}] for n in range(MAX_PAGES + 1)))
    assert "shorter window" in message(fake_github)
    assert len(fake_github.requests) == MAX_PAGES


def test_exactly_the_maximum_number_of_pages_is_fine(fake_github):
    fake_github.serve(PATH, *([{"id": n}] for n in range(MAX_PAGES)))
    assert len(items(fake_github)) == MAX_PAGES


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, None),
        ('<https://api.github.com/x?page=3>; rel="last"', None),
        (
            '<https://api.github.com/x?page=1>; rel="prev", <https://api.github.com/x?page=3>; rel="next"',
            "https://api.github.com/x?page=3",
        ),
    ],
)
def test_next_link(header, expected):
    assert next_link(header) == expected
