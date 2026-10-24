import http.client
import io
import urllib.error
import urllib.request
from email.message import Message

import pytest
from heinzel_slack import Transport
from heinzel_slack._transport import MAX_BYTES, _SameOriginRedirects, origin

from heinzel import HeinzelError

pytestmark = pytest.mark.unit


def message(**headers: str) -> Message:
    result = Message()
    for name, value in headers.items():
        result[name.replace("_", "-")] = value
    return result


class Answer(io.BytesIO):
    def __init__(self, body: bytes, status: int = 200, **headers: str) -> None:
        super().__init__(body)
        self.status = status
        self.headers = message(**headers)


class Opener:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.requests: list[tuple[urllib.request.Request, float]] = []

    def open(self, request: urllib.request.Request, timeout: float):
        self.requests.append((request, timeout))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


@pytest.fixture
def opener(monkeypatch):
    def install(outcome: object) -> Opener:
        fake = Opener(outcome)
        handlers: list[object] = []
        monkeypatch.setattr(urllib.request, "build_opener", lambda *h: handlers.extend(h) or fake)
        fake.handlers = handlers
        return fake

    return install


def test_a_get_returns_the_answer_with_lower_case_headers(opener):
    fake = opener(Answer(b"{}", Retry_After="1"))
    response = Transport().send("https://slack.com/api/auth.test", {"Accept": "a"}, None, 3.0)
    assert (response.status, response.body, response.headers) == (200, b"{}", {"retry-after": "1"})
    request, timeout = fake.requests[0]
    assert request.full_url == "https://slack.com/api/auth.test"
    assert request.get_method() == "GET"
    assert request.get_header("Accept") == "a"
    assert timeout == 3.0
    assert isinstance(fake.handlers[0], _SameOriginRedirects)


def test_a_body_is_posted(opener):
    fake = opener(Answer(b"{}"))
    Transport().send("https://slack.com/api/chat.postMessage", {}, b'{"text": "hi"}', 1.0)
    request, _ = fake.requests[0]
    assert request.get_method() == "POST"
    assert request.data == b'{"text": "hi"}'


def test_an_error_status_is_an_answer(opener):
    opener(
        urllib.error.HTTPError("https://slack.com/api/x", 429, "Too Many", message(Retry_After="3"), io.BytesIO(b""))
    )
    response = Transport().send("https://slack.com/api/x", {}, None, 1.0)
    assert (response.status, response.headers) == (429, {"retry-after": "3"})


def test_the_body_is_read_up_to_one_byte_past_the_limit(opener):
    opener(Answer(b"x" * (MAX_BYTES + 10)))
    assert len(Transport().send("https://slack.com/api/x", {}, None, 1.0).body) == MAX_BYTES + 1


def test_a_broken_connection_is_an_os_error(opener):
    opener(http.client.IncompleteRead(b""))
    with pytest.raises(OSError, match="IncompleteRead"):
        Transport().send("https://slack.com/api/x", {}, None, 1.0)


@pytest.mark.parametrize(
    "error",
    [ValueError("Invalid header value b'Bearer tok\\r\\n'"), UnicodeEncodeError("latin-1", "t€", 1, 2, "no")],
    ids=["value-error", "unicode-error"],
)
def test_a_request_http_client_refuses_is_an_error_without_the_header(opener, error):
    opener(error)
    with pytest.raises(HeinzelError, match="not valid HTTP") as caught:
        Transport().send("https://slack.com/api/x", {"Authorization": "Bearer tok"}, None, 1.0)
    assert "Bearer" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


def test_real_http_client_refuses_a_line_break_in_a_header_before_connecting(monkeypatch):
    monkeypatch.setattr("socket.create_connection", lambda *a, **k: pytest.fail("must not connect"))
    with pytest.raises(HeinzelError, match="ValueError") as caught:
        Transport().send("https://slack.com/api/x", {"Authorization": "Bearer tok\r\n"}, None, 1.0)
    assert "Bearer" not in str(caught.value)


def test_redirects_stay_within_the_origin():
    handler = _SameOriginRedirects("https://slack.com")
    request = urllib.request.Request("https://slack.com/api/x", headers={"Authorization": "Bearer t"})
    moved = handler.redirect_request(request, None, 301, "Moved", message(), "https://slack.com/api/y")
    assert moved is not None
    assert moved.full_url == "https://slack.com/api/y"
    for elsewhere in ("https://evil.example/x", "http://slack.com/x", "https://slack.com.evil.example/x"):
        assert handler.redirect_request(request, None, 302, "Found", message(), elsewhere) is None


def test_sleep_waits(monkeypatch):
    waited: list[float] = []
    monkeypatch.setattr("time.sleep", waited.append)
    Transport().sleep(2)
    assert waited == [2]


def test_origin():
    assert origin("https://slack.com:443/api/x?y=1") == "https://slack.com:443"
