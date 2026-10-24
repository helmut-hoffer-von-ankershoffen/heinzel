"""The client's transport against fake responses: no request ever leaves. Offline."""

import io
import json
import urllib.error
import urllib.request

import pytest
from heinzel_openmeteo import FORECAST_HOST, GEOCODING_HOST, Client
from heinzel_openmeteo._client import MAX_BYTES, Forecast, _NoRedirect
from heinzel_openmeteo._codes import describe

from heinzel import HeinzelError

pytestmark = pytest.mark.unit


class Opener:
    """What `urllib.request.build_opener` returns in these tests: answers, or raises."""

    def __init__(self, body: bytes = b"{}", error: Exception | None = None) -> None:
        self.body, self.error = body, error
        self.handlers: tuple = ()
        self.requests: list[urllib.request.Request] = []

    def open(self, request: urllib.request.Request, timeout: float) -> io.BytesIO:
        self.requests.append(request)
        assert timeout > 0
        if self.error:
            raise self.error
        return io.BytesIO(self.body)


def using(monkeypatch: pytest.MonkeyPatch, opener: Opener) -> Opener:

    def build(*handlers):
        opener.handlers = handlers
        return opener

    monkeypatch.setattr(urllib.request, "build_opener", build)
    return opener


def test_it_asks_over_https_with_the_query_encoded(monkeypatch):
    opener = using(monkeypatch, Opener(b'{"ok": true}'))
    assert Client().get(GEOCODING_HOST, "/v1/search", {"name": "São Paulo, BR"}) == {"ok": True}
    [request] = opener.requests
    assert _NoRedirect in opener.handlers  # the real opener refuses redirects
    assert request.full_url == f"https://{GEOCODING_HOST}/v1/search?name=S%C3%A3o+Paulo%2C+BR"


def test_a_refusal_names_the_host_and_status_but_not_the_place(monkeypatch):
    error = urllib.error.HTTPError("https://x", 429, "Too Many", None, io.BytesIO(b""))  # type: ignore[arg-type]
    using(monkeypatch, Opener(error=error))
    with pytest.raises(HeinzelError, match=r"answered 429") as caught:
        Client().get(FORECAST_HOST, "/v1/forecast", {"latitude": "1.2345", "longitude": "6.7890"})
    assert "1.2345" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize("error", [urllib.error.URLError("offline"), TimeoutError(), ConnectionResetError()])
def test_an_unreachable_service_is_an_error_that_says_so(monkeypatch, error):
    using(monkeypatch, Opener(error=error))
    with pytest.raises(HeinzelError, match="can't be reached"):
        Client().get(FORECAST_HOST, "/v1/forecast", {})


def test_an_answer_that_is_not_json_or_too_large_is_refused(monkeypatch):
    using(monkeypatch, Opener(b"<html>"))
    with pytest.raises(HeinzelError, match="isn't JSON"):
        Client().get(FORECAST_HOST, "/v1/forecast", {})
    using(monkeypatch, Opener(b"x" * (MAX_BYTES + 1)))
    with pytest.raises(HeinzelError, match="more than"):
        Client().get(FORECAST_HOST, "/v1/forecast", {})


def test_redirects_are_never_followed():
    handler = _NoRedirect()
    request = urllib.request.Request("https://api.open-meteo.com/v1/forecast")
    assert handler.redirect_request(request, None, 302, "Found", {}, "http://elsewhere.invalid/") is None


def test_an_answer_in_an_unknown_shape_names_fields_not_values(monkeypatch):
    using(monkeypatch, Opener(json.dumps({"timezone": "Europe/Lisbon", "current": {"time": "secret-place"}}).encode()))
    with pytest.raises(HeinzelError, match=r"shape this plugin doesn't know \(at ") as caught:
        Client().forecast(1.0, 2.0, 3)
    assert "secret-place" not in str(caught.value)


def test_a_forecast_with_extra_fields_is_read(client):
    assert Forecast.model_validate(client.forecast_answer).daily.time[0] == "2026-10-25"


@pytest.mark.parametrize(
    ("code", "words"), [(0, "clear sky"), (63, "rain"), (95, "thunderstorm"), (4, "unknown"), (None, "unknown")]
)
def test_codes_are_words(code, words):
    assert describe(code) == words
