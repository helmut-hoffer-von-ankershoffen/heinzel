import http.client
import io
import urllib.request

import pytest
from pydantic import SecretStr

from heinzel import __version__
from heinzel.modules.system import _github

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("token", [None, SecretStr("t0ken")])
def test_get_json_sends_only_the_version_and_an_optional_token(monkeypatch, token):
    requests = []

    def fake_open(request, timeout):
        requests.append((request.full_url, dict(request.header_items()), timeout))
        return b'{"tag_name": "v1"}'

    monkeypatch.setattr(_github, "_open", fake_open)
    assert _github.get_json("https://api.github.com/x", token, 5) == {"tag_name": "v1"}
    expected = {"Accept": "application/vnd.github+json", "User-agent": f"heinzel/{__version__}"}
    if token:
        expected["Authorization"] = "Bearer t0ken"
    assert requests == [("https://api.github.com/x", expected, 5)]


@pytest.mark.parametrize(
    "url", ["https://example.com/api.github.com/", "https://api.github.com.evil.example/x", "http://api.github.com/x"]
)
def test_get_json_sends_nothing_outside_the_github_api(url):
    with pytest.raises(ValueError, match="not a GitHub API URL"):
        _github.get_json(url, SecretStr("t0ken"), 5)


def test_get_json_refuses_oversized_answers(monkeypatch):
    monkeypatch.setattr(_github, "_open", lambda request, timeout: b" " * (_github.MAX_BYTES + 1))
    with pytest.raises(ValueError, match="larger than"):
        _github.get_json("https://api.github.com/x", None, 5)


def redirect(newurl):
    request = urllib.request.Request("https://api.github.com/x", headers={"Authorization": "Bearer t0ken"})
    return _github._GitHubApiRedirects().redirect_request(request, io.BytesIO(), 301, "Moved", {}, newurl)


def test_redirects_within_the_github_api_keep_the_token():
    assert redirect("https://api.github.com/y").get_header("Authorization") == "Bearer t0ken"


@pytest.mark.parametrize("newurl", ["https://example.com/", "http://api.github.com/y", "https://api.github.com.evil/"])
def test_redirects_elsewhere_are_refused_so_the_token_stays_with_github(newurl):
    assert redirect(newurl) is None


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_open_reads_through_the_github_only_opener(monkeypatch):
    opened = []

    class Opener:
        def open(self, request, timeout):
            opened.append((request.full_url, timeout))
            return Response(b"{}")

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: Opener())
    assert _github._open(urllib.request.Request("https://api.github.com/x"), 5) == b"{}"
    assert opened == [("https://api.github.com/x", 5)]


def test_open_turns_a_broken_answer_into_an_oserror(monkeypatch):
    class Opener:
        def open(self, request, timeout):
            partial = b"{"
            raise http.client.IncompleteRead(partial)

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: Opener())
    with pytest.raises(OSError, match="IncompleteRead"):
        _github._open(urllib.request.Request("https://api.github.com/x"), 5)
