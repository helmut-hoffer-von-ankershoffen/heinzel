import json

import pytest
from heinzel_slack import TOKEN as TOKEN_LET
from heinzel_slack import Response, SlackError, SlackSettings
from heinzel_slack._client import (
    DEFAULT_WAIT,
    MAX_PAGES,
    MAX_REQUESTS,
    MAX_RETRIES,
    MAX_SAID,
    MAX_WAIT,
    SlackClient,
    _why,
    connect,
)
from heinzel_slack._transport import MAX_BYTES

from heinzel import Credentials, HeinzelError
from heinzel.testing import MemoryBackend

pytestmark = pytest.mark.unit

TOKEN = "xoxp-client-token-never-shown"
FIX = "hz system credential set slack-token"


def client(fake) -> SlackClient:
    return SlackClient("https://slack.com/api", TOKEN, fake, 7.0)


def stored(token: str | None) -> Credentials:
    backend = MemoryBackend()
    if token is not None:
        backend.set("slack", "slack-token", token)  # as a keychain or HZ_CREDENTIAL_* would hand it over
    return Credentials(backend, [("slack", TOKEN_LET)])


def test_a_call_gets_the_method_with_the_token_and_returns_the_answer(fake_slack):
    answer = client(fake_slack).call("auth.test", {"x": "1 2"}, "the token")
    assert answer["url"] == "https://acme.slack.com/"
    request = fake_slack.requests[0]
    assert request.url == "https://slack.com/api/auth.test?x=1+2"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["User-Agent"].startswith("heinzel-slack")
    assert request.body is None
    assert request.timeout == 7.0


def test_a_post_sends_json(fake_slack):
    client(fake_slack).post("chat.postMessage", {"channel": "C0123456789", "text": "hi"}, "channel C0123456789")
    request = fake_slack.requests[0]
    assert request.url == "https://slack.com/api/chat.postMessage"
    assert json.loads(request.body) == {"channel": "C0123456789", "text": "hi"}


def test_pages_follow_the_cursor_until_it_is_empty(fake_slack):
    fake_slack.channels = [{"id": f"C{n:010d}", "name": f"c{n}"} for n in range(5)]
    pages = list(client(fake_slack).pages("conversations.list", {"limit": "2"}, "channels", "#x", "advice"))
    assert [len(page) for page in pages] == [2, 2, 1]
    assert [r.query.get("cursor") for r in fake_slack.requests] == [None, "2", "4"]
    assert all(r.query["limit"] == "2" for r in fake_slack.requests)


def test_pages_stop_when_the_caller_stops(fake_slack):
    fake_slack.channels = [{"id": f"C{n:010d}", "name": f"c{n}"} for n in range(5)]
    next(client(fake_slack).pages("conversations.list", {}, "channels", "#x", "advice"))
    assert len(fake_slack.requests) == 1


def test_a_page_without_the_list_fails(fake_slack):
    fake_slack.queue("conversations.list", fake_slack.ok(channels="nope"))
    with pytest.raises(HeinzelError, match="lacks a list of channels"):
        list(client(fake_slack).pages("conversations.list", {}, "channels", "#x", "advice"))


def test_a_repeated_cursor_stops_instead_of_looping(fake_slack):
    page = fake_slack.ok(channels=[], response_metadata={"next_cursor": "same"})
    fake_slack.queue("conversations.list", page, page)
    with pytest.raises(HeinzelError, match="repeated a page"):
        list(client(fake_slack).pages("conversations.list", {}, "channels", "#x", "advice"))


def test_more_than_the_page_cap_fails_with_the_advice(fake_slack, monkeypatch):
    monkeypatch.setattr("heinzel_slack._client.MAX_PAGES", 2)
    fake_slack.channels = [{"id": f"C{n:010d}", "name": f"c{n}"} for n in range(5)]
    with pytest.raises(HeinzelError, match="more than 2 pages; give the id"):
        list(client(fake_slack).pages("conversations.list", {}, "channels", "#x", "give the id"))
    assert MAX_PAGES == 100


def test_a_rate_limit_is_waited_out_as_retry_after_says_and_retried(fake_slack):
    fake_slack.queue("auth.test", fake_slack.error("ratelimited", 429, {"retry-after": "3"}))
    fake_slack.queue("auth.test", fake_slack.error("ratelimited", 429, {}))
    assert client(fake_slack).call("auth.test", {}, "the token")["ok"] is True
    assert fake_slack.slept == [3, DEFAULT_WAIT]
    assert len(fake_slack.requests) == 3


def test_a_rate_limit_that_persists_fails_with_the_time_to_retry(fake_slack):
    limited = fake_slack.error("ratelimited", 429, {"retry-after": "1"})
    fake_slack.queue("auth.test", *[limited] * (MAX_RETRIES + 1))
    with pytest.raises(HeinzelError, match=r"rate limit for auth.test is reached .*retry after 1 seconds"):
        client(fake_slack).call("auth.test", {}, "the token")
    assert fake_slack.slept == [1] * MAX_RETRIES


def test_a_rate_limit_longer_than_the_longest_wait_fails_at_once(fake_slack):
    fake_slack.queue("auth.test", fake_slack.error("ratelimited", 429, {"retry-after": str(MAX_WAIT + 1)}))
    with pytest.raises(HeinzelError, match=f"retry after {MAX_WAIT + 1} seconds"):
        client(fake_slack).call("auth.test", {}, "the token")
    assert fake_slack.slept == []


def test_the_request_budget_bounds_one_call_of_the_adapter(fake_slack, monkeypatch):
    monkeypatch.setattr("heinzel_slack._client.MAX_REQUESTS", 2)
    slack = client(fake_slack)
    slack.call("auth.test", {}, "the token")
    slack.call("auth.test", {}, "the token")
    with pytest.raises(HeinzelError, match="more than 2 requests"):
        slack.call("auth.test", {}, "the token")
    assert len(fake_slack.requests) == 2
    assert MAX_REQUESTS == 600


def test_the_byte_budget_bounds_one_call_of_the_adapter(fake_slack, monkeypatch):
    monkeypatch.setattr("heinzel_slack._client.MAX_TOTAL_BYTES", 10)
    slack = client(fake_slack)
    slack.call("auth.test", {}, "the token")
    with pytest.raises(HeinzelError, match="bytes from Slack; ask for a shorter window"):
        slack.call("auth.test", {}, "the token")


def test_an_answer_over_the_size_limit_fails(fake_slack):
    fake_slack.queue("auth.test", Response(200, {}, b"x" * (MAX_BYTES + 1)))
    with pytest.raises(HeinzelError, match=f"larger than {MAX_BYTES} bytes"):
        client(fake_slack).call("auth.test", {}, "the token")


def test_unreachable_names_the_error_never_the_token(fake_slack):
    fake_slack.unreachable = ConnectionRefusedError("refused")
    with pytest.raises(HeinzelError, match=r"Could not reach https://slack.com for auth.test: .*refused") as caught:
        client(fake_slack).call("auth.test", {}, "the token")
    assert TOKEN not in str(caught.value)


def test_why_unwraps_urllib_and_chained_errors():
    import urllib.error

    assert _why(urllib.error.URLError("no route\x1b[31m")) == "URLError: no route[31m"
    assert _why(urllib.error.URLError(TimeoutError())) == "TimeoutError"
    chained = OSError("wrapped")
    chained.__cause__ = ValueError("inner")
    assert _why(chained) == "ValueError: inner"


def test_a_refused_redirect_names_the_target_site_only(fake_slack):
    fake_slack.queue("auth.test", Response(302, {"location": "https://user:pw@evil.example:8443/x?token=1"}, b""))
    with pytest.raises(HeinzelError, match=r"redirected auth.test to https://evil.example:8443, away") as caught:
        client(fake_slack).call("auth.test", {}, "the token")
    assert "pw" not in str(caught.value)


@pytest.mark.parametrize("location", ["", "https://evil.example:bad/x"])
def test_a_refused_redirect_without_a_usable_location(fake_slack, location):
    fake_slack.queue("auth.test", Response(301, {"location": location}, b""))
    with pytest.raises(HeinzelError, match=r"redirected auth.test to (another address|https://evil.example),"):
        client(fake_slack).call("auth.test", {}, "the token")


def test_an_http_error_status_says_to_try_later(fake_slack):
    fake_slack.queue("auth.test", Response(503, {}, b"down"))
    with pytest.raises(HeinzelError, match="with HTTP 503; try again later"):
        client(fake_slack).call("auth.test", {}, "the token")


@pytest.mark.parametrize("body", [b"<html>", b"[]"])
def test_an_answer_that_is_no_json_object_fails(fake_slack, body):
    fake_slack.queue("auth.test", Response(200, {}, body))
    with pytest.raises(HeinzelError, match="not a JSON object"):
        client(fake_slack).call("auth.test", {}, "the token")


@pytest.mark.parametrize(
    ("code", "data", "expected"),
    [
        ("invalid_auth", {}, f"rejected the token (invalid_auth); store a new one with `{FIX}`"),
        ("token_revoked", {}, "rejected the token (token_revoked)"),
        ("missing_scope", {"needed": "channels:history"}, "lacks the scope channels:history for conversations"),
        ("missing_scope", {"needed": "x y\r\nInjected"}, "lacks the scope one this method needs"),
        ("not_allowed_token_type", {}, "check slack.token_kind"),
        ("channel_not_found", {}, "no channel C0123456789 the token can see (channel_not_found)"),
        ("not_in_channel", {}, "join the channel"),
        ("is_archived", {}, "is archived"),
        ("thread_not_found", {}, "no such thread"),
        ("msg_too_long", {}, "post a shorter, non-empty text"),
        ("ratelimited", {}, "try again in a minute"),
        ("fatal_error", {}, "with the error fatal_error"),
        ("Evil <!channel> text", {}, "with the error unknown_error"),
        (None, {}, "with the error unknown_error"),
    ],
)
def test_slack_errors_say_what_to_do_with_the_code_and_never_free_text(fake_slack, code, data, expected):
    fake_slack.queue("conversations.history", fake_slack.error(code, **data))
    with pytest.raises(SlackError) as caught:
        client(fake_slack).call("conversations.history", {}, "channel C0123456789")
    assert expected in str(caught.value)
    assert "Injected" not in str(caught.value)
    assert "<!channel>" not in str(caught.value)


def test_said_cleans_and_redacts_the_token_before_cutting(fake_slack):
    said = client(fake_slack)._said("\u202e" + "x" * (MAX_SAID - 2) + TOKEN)
    assert said == "x" * (MAX_SAID - 2) + "**"  # cut first, and "xo" of the token would have survived


def test_connect_strips_the_token_and_builds_a_client(fake_slack):
    slack = connect(SlackSettings(), stored(f"  {TOKEN}\r\n"), fake_slack)
    slack.call("auth.test", {}, "the token")
    assert fake_slack.requests[0].headers["Authorization"] == f"Bearer {TOKEN}"


def test_connect_accepts_rotating_tokens(fake_slack):
    connect(SlackSettings(), stored("xoxe.xoxp-1-rotating"), fake_slack)
    connect(SlackSettings(token_kind="bot"), stored("xoxe.xoxb-1-rotating"), fake_slack)


@pytest.mark.parametrize("token", [None, "", "   "])
def test_connect_without_a_token_names_the_command_and_the_scopes(fake_slack, token):
    with pytest.raises(HeinzelError, match=f"no anonymous access. Store a user token with `{FIX}`.*channels:history"):
        connect(SlackSettings(), stored(token), fake_slack)


@pytest.mark.parametrize(
    "token",
    ["xoxp-first\r\nX-Injected: second", "xoxp-töken", "xoxp-a b", "xoxp-\x00"],
    ids=["line-break", "non-ascii", "space", "nul"],
)
def test_connect_refuses_a_malformed_token_without_showing_it(fake_slack, token):
    with pytest.raises(HeinzelError, match="not one line of printable ASCII") as caught:
        connect(SlackSettings(), stored(token), fake_slack)
    for part in ("first", "Injected", "second", "t\u00f6ken"):
        assert part not in str(caught.value)
    assert fake_slack.requests == []


def test_connect_refuses_a_token_of_the_other_kind(fake_slack):
    with pytest.raises(HeinzelError, match=r"not a user token \(xoxp-...\)") as caught:
        connect(SlackSettings(), stored("xoxb-secret-bot"), fake_slack)
    assert "secret" not in str(caught.value)
