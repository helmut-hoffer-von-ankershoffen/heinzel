import http.client
import io
import urllib.error
import urllib.request
from email.message import Message

import pytest
from heinzel_github import Transport
from heinzel_github._transport import MAX_BYTES, _SameOriginRedirects, origin

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


def test_an_answer_with_lower_case_headers(opener):
    fake = opener(Answer(b"[]", Link="<x>", X_RateLimit_Remaining="59"))
    response = Transport().get("https://api.github.com/x", {"Accept": "a"}, 3.0)
    assert (response.status, response.body) == (200, b"[]")
    assert response.headers == {"link": "<x>", "x-ratelimit-remaining": "59"}
    request, timeout = fake.requests[0]
    assert request.full_url == "https://api.github.com/x"
    assert request.get_header("Accept") == "a"
    assert timeout == 3.0
    assert isinstance(fake.handlers[0], _SameOriginRedirects)


def test_an_error_status_is_an_answer(opener):
    error = urllib.error.HTTPError(
        "https://api.github.com/x", 404, "Not Found", message(Retry_After="1"), io.BytesIO(b"{}")
    )
    opener(error)
    response = Transport().get("https://api.github.com/x", {}, 1.0)
    assert (response.status, response.headers, response.body) == (404, {"retry-after": "1"}, b"{}")


def test_the_body_is_read_up_to_one_byte_past_the_limit(opener):
    opener(Answer(b"x" * (MAX_BYTES + 10)))
    assert len(Transport().get("https://api.github.com/x", {}, 1.0).body) == MAX_BYTES + 1


def test_a_broken_connection_is_an_os_error(opener):
    opener(http.client.IncompleteRead(b""))
    with pytest.raises(OSError, match="IncompleteRead"):
        Transport().get("https://api.github.com/x", {}, 1.0)


def test_unreachable_stays_an_os_error(opener):
    opener(urllib.error.URLError("no route"))
    with pytest.raises(OSError, match="no route"):
        Transport().get("https://api.github.com/x", {}, 1.0)


@pytest.mark.parametrize(
    "error",
    [
        ValueError("Invalid header value b'Bearer tok\\r\\n'"),
        UnicodeEncodeError("latin-1", "Bearer t\u00f6k\u20ac", 9, 10, "ordinal not in range(256)"),
    ],
    ids=["value-error", "unicode-error"],
)
def test_a_request_http_client_refuses_is_an_error_without_the_header(opener, error):
    opener(error)
    with pytest.raises(HeinzelError, match="not valid HTTP") as caught:
        Transport().get("https://api.github.com/x", {"Authorization": "Bearer tok"}, 1.0)
    assert "tok" not in str(caught.value).replace("token", "")
    assert caught.value.__suppress_context__
    assert caught.value.__cause__ is None


def test_real_http_client_refuses_a_line_break_in_a_header_before_connecting(monkeypatch):
    def no_network(*args, **kwargs):
        pytest.fail("the request must be refused before any connection")

    monkeypatch.setattr("socket.create_connection", no_network)
    with pytest.raises(HeinzelError, match="ValueError") as caught:
        Transport().get("https://api.github.com/x", {"Authorization": "Bearer tok\r\n"}, 1.0)
    assert "tok" not in str(caught.value).replace("token", "")


def test_redirects_stay_within_the_origin():
    handler = _SameOriginRedirects("https://api.github.com")
    request = urllib.request.Request("https://api.github.com/repos/acme/old", headers={"Authorization": "Bearer t"})
    moved = handler.redirect_request(request, None, 301, "Moved", message(), "https://api.github.com/repositories/1")
    assert moved is not None
    assert moved.full_url == "https://api.github.com/repositories/1"
    for elsewhere in ("https://evil.example/x", "http://api.github.com/x", "https://api.github.com.evil.example/x"):
        assert handler.redirect_request(request, None, 302, "Found", message(), elsewhere) is None


def test_origin():
    assert origin("https://ghe.example.com:8443/api/v3/x?y=1") == "https://ghe.example.com:8443"
