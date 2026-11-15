"""PyPI's JSON API as hz reads it: an HTTP error closes its response instead of leaking it."""

import io
import urllib.error

import pytest

from heinzel.modules.system import _pypi

pytestmark = pytest.mark.unit


def test_an_http_error_is_closed_and_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    body = io.BytesIO(b"not found")
    error = urllib.error.HTTPError("https://pypi.org/pypi/heinzel/9.9.9/json", 404, "Not Found", {}, body)  # type: ignore[arg-type]

    def refuse(request: object, timeout: float) -> object:
        raise error

    monkeypatch.setattr(_pypi.urllib.request, "urlopen", refuse)
    with pytest.raises(urllib.error.HTTPError):
        _pypi.fetch("https://pypi.org/pypi/heinzel/9.9.9/json", timeout=1)
    assert body.closed


def test_only_pypi_json_urls_are_fetched() -> None:
    with pytest.raises(ValueError, match="not a PyPI JSON URL"):
        _pypi.fetch("https://example.com/pypi/heinzel/json", timeout=1)
