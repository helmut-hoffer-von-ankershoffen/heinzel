import http.client
import io
import urllib.error
import urllib.request
from email.message import Message

import pytest
from heinzel_atlassian import Transport
from heinzel_atlassian._transport import MAX_BYTES, _SameOriginRedirects, origin

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
    response = Transport().get("https://acme.atlassian.net/x", {"Accept": "a"}, 3.0)
    assert (response.status, response.body) == (200, b"[]")
    assert response.headers == {"link": "<x>", "x-ratelimit-remaining": "59"}
    request, timeout = fake.requests[0]
    assert request.full_url == "https://acme.atlassian.net/x"
    assert request.get_header("Accept") == "a"
    assert timeout == 3.0
    assert isinstance(fake.handlers[0], _SameOriginRedirects)


def test_an_error_status_is_an_answer(opener):
    error = urllib.error.HTTPError(
        "https://acme.atlassian.net/x", 404, "Not Found", message(Retry_After="1"), io.BytesIO(b"{}")
    )
    opener(error)
    response = Transport().get("https://acme.atlassian.net/x", {}, 1.0)
    assert (response.status, response.headers, response.body) == (404, {"retry-after": "1"}, b"{}")


def test_the_body_is_read_up_to_one_byte_past_the_limit(opener):
    opener(Answer(b"x" * (MAX_BYTES + 10)))
    assert len(Transport().get("https://acme.atlassian.net/x", {}, 1.0).body) == MAX_BYTES + 1


def test_a_broken_connection_is_an_os_error(opener):
    opener(http.client.IncompleteRead(b""))
    with pytest.raises(OSError, match="IncompleteRead"):
        Transport().get("https://acme.atlassian.net/x", {}, 1.0)


def test_unreachable_stays_an_os_error(opener):
    opener(urllib.error.URLError("no route"))
    with pytest.raises(OSError, match="no route"):
        Transport().get("https://acme.atlassian.net/x", {}, 1.0)


def test_redirects_stay_within_the_origin():
    handler = _SameOriginRedirects("https://acme.atlassian.net")
    request = urllib.request.Request("https://acme.atlassian.net/wiki/old", headers={"Authorization": "Basic dA=="})
    moved = handler.redirect_request(request, None, 301, "Moved", message(), "https://acme.atlassian.net/wiki/new")
    assert moved is not None
    assert moved.full_url == "https://acme.atlassian.net/wiki/new"
    for elsewhere in (
        "https://evil.example/x",
        "https://other.atlassian.net/x",
        "http://acme.atlassian.net/x",
        "https://acme.atlassian.net.evil.example/x",
    ):
        assert handler.redirect_request(request, None, 302, "Found", message(), elsewhere) is None


def test_origin():
    assert origin("https://acme.atlassian.net:8443/wiki/x?y=1") == "https://acme.atlassian.net:8443"
