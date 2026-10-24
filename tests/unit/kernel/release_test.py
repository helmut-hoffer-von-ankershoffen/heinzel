import http.client
import io
import json
import urllib.error
import urllib.request

import pytest
from pydantic import SecretStr

from heinzel import Settings
from heinzel.kernel import _release
from heinzel.kernel._release import (
    CACHE_FILENAME,
    CI_VARIABLES,
    DEFAULT_REPOSITORY,
    check_for_update,
    github_get_json,
    installed_version,
    repository_of,
    update_hint,
)

pytestmark = pytest.mark.unit

SOURCE = f"https://api.github.com/repos/{DEFAULT_REPOSITORY}/releases/latest"
RELEASE = {"tag_name": "v0.2.0", "html_url": "https://github.com/org/heinzel/releases/tag/v0.2.0"}


@pytest.fixture
def settings(tmp_path):
    return Settings(cache_dir=tmp_path / "cache")


def answering(answer):
    """A fake fetch that records its calls and answers with `answer`, or raises it."""
    calls = []

    def fetch(url, token, timeout):
        calls.append((url, token, timeout))
        if isinstance(answer, Exception):
            raise answer
        return answer

    fetch.calls = calls
    return fetch


@pytest.mark.parametrize(
    ("requirement", "expected"),
    [
        (None, DEFAULT_REPOSITORY),
        ("heinzel>=0.1", DEFAULT_REPOSITORY),
        ("heinzel @ git+https://github.com/someone/heinzel@v0.1.0", "someone/heinzel"),
        ("heinzel @ git+https://github.com/someone/heinzel.git", "someone/heinzel"),
        ("heinzel @ git+https://github.com/someone/hz-fork#subdirectory=core", "someone/hz-fork"),
        ("heinzel @ git+https://gitlab.com/someone/heinzel", DEFAULT_REPOSITORY),
        ("heinzel @ git+https://github.com/someone", DEFAULT_REPOSITORY),
        ("heinzel @ git+https://github.com/someone/..", DEFAULT_REPOSITORY),
        ("heinzel @ git+https://github.com/some%2Fone/heinzel", DEFAULT_REPOSITORY),
    ],
)
def test_repository_of_reads_the_github_repository_from_the_requirement(requirement, expected):
    assert repository_of(requirement) == expected


def test_check_asks_the_source_and_caches_the_answer(settings):
    fetch = answering(RELEASE)
    check = check_for_update(settings, None, fetch=fetch, current="0.1.0")
    assert check.model_dump() == {
        "current": "0.1.0",
        "latest": "0.2.0",
        "update_available": True,
        "url": RELEASE["html_url"],
        "source": SOURCE,
        "warnings": [],
    }
    assert fetch.calls == [(SOURCE, None, 5)]
    cached = json.loads((settings.cache_dir / CACHE_FILENAME).read_text(encoding="utf-8"))
    assert cached["latest"] == "0.2.0"


def test_check_asks_the_repository_heinzel_was_installed_from_with_the_token(tmp_path):
    settings = Settings(cache_dir=tmp_path, github_token=SecretStr("t0ken"))
    fetch = answering(RELEASE)
    check_for_update(settings, "heinzel @ git+https://github.com/someone/heinzel@v0.1.0", fetch=fetch)
    [(url, token, _)] = fetch.calls
    assert url == "https://api.github.com/repos/someone/heinzel/releases/latest"
    assert token.get_secret_value() == "t0ken"


@pytest.mark.parametrize(("current", "available"), [("0.2.0", False), ("0.3.0.dev1", False), ("0.1.9", True)])
def test_check_offers_only_newer_releases(settings, current, available):
    assert check_for_update(settings, None, fetch=answering(RELEASE), current=current).update_available is available


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (urllib.error.URLError("no route to host"), "no route to host"),
        (urllib.error.HTTPError(SOURCE, 403, "rate limit exceeded", {}, None), "HTTP Error 403: rate limit exceeded"),
        (TimeoutError("timed out"), "timed out"),
        ({"message": "Not Found"}, "'tag_name'"),
        ({"tag_name": "nightly", "html_url": "x"}, "Invalid version: 'nightly'"),
        (["not", "a", "release"], "list indices must be integers or slices, not str"),
    ],
)
def test_check_warns_instead_of_failing_and_keeps_the_cache(settings, answer, reason):
    check = check_for_update(settings, None, fetch=answering(answer), current="0.1.0")
    assert (check.latest, check.update_available, check.url) == (None, False, None)
    assert check.warnings == [f"Could not ask {SOURCE} for the latest release ({reason})."]
    assert not (settings.cache_dir / CACHE_FILENAME).exists()


def test_check_shows_only_release_links_to_github(settings):
    answer = {**RELEASE, "html_url": "https://example.com/phish"}
    check = check_for_update(settings, None, fetch=answering(answer), current="0.1.0")
    assert (check.latest, check.url) == ("0.2.0", None)


def test_check_answers_even_if_the_cache_cannot_be_written(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("", encoding="utf-8")
    check = check_for_update(Settings(cache_dir=blocked / "cache"), None, fetch=answering(RELEASE), current="0.1.0")
    assert check.update_available


def test_check_uses_github_get_json_by_default(settings, monkeypatch):
    fetch = answering(RELEASE)
    monkeypatch.setattr(_release, "github_get_json", fetch)
    check_for_update(settings, None)
    assert len(fetch.calls) == 1


@pytest.mark.parametrize("token", [None, SecretStr("t0ken")])
def test_github_get_json_sends_no_identifier_but_the_version_and_an_optional_token(monkeypatch, token):
    requests = []

    def fake_open(request, timeout):
        requests.append((request.full_url, dict(request.header_items()), timeout))
        return b'{"tag_name": "v1"}'

    monkeypatch.setattr(_release, "_open", fake_open)
    assert github_get_json("https://api.github.com/x", token, 5) == {"tag_name": "v1"}
    [(url, headers, timeout)] = requests
    expected = {"Accept": "application/vnd.github+json", "User-agent": f"heinzel/{installed_version()}"}
    if token:
        expected["Authorization"] = "Bearer t0ken"
    assert (url, headers, timeout) == ("https://api.github.com/x", expected, 5)


@pytest.mark.parametrize(
    "url", ["https://example.com/api.github.com/", "https://api.github.com.evil.example/x", "http://api.github.com/x"]
)
def test_github_get_json_sends_nothing_outside_the_github_api(url):
    with pytest.raises(ValueError, match="not a GitHub API URL"):
        github_get_json(url, SecretStr("t0ken"), 5)


def test_github_get_json_refuses_oversized_answers(monkeypatch):
    monkeypatch.setattr(_release, "_open", lambda request, timeout: b" " * (_release.MAX_BYTES + 1))
    with pytest.raises(ValueError, match="larger than"):
        github_get_json("https://api.github.com/x", None, 5)


def redirect(newurl):
    request = urllib.request.Request("https://api.github.com/x", headers={"Authorization": "Bearer t0ken"})
    return _release._GitHubApiRedirects().redirect_request(request, io.BytesIO(), 301, "Moved", {}, newurl)


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
    assert _release._open(urllib.request.Request("https://api.github.com/x"), 5) == b"{}"
    assert opened == [("https://api.github.com/x", 5)]


def test_open_turns_a_broken_answer_into_an_oserror(monkeypatch):
    class Opener:
        def open(self, request, timeout):
            partial = b"{"
            raise http.client.IncompleteRead(partial)

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: Opener())
    with pytest.raises(OSError, match="IncompleteRead"):
        _release._open(urllib.request.Request("https://api.github.com/x"), 5)


def cache(settings, content):
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    (settings.cache_dir / CACHE_FILENAME).write_text(content, encoding="utf-8")


def hint(settings, *, tty=True, environ=None, current="0.1.0"):
    return update_hint(settings, current=current, isatty=lambda: tty, environ=environ or {})


def test_hint_names_the_newer_release_and_the_command(settings):
    cache(settings, json.dumps({"latest": "0.2.0"}))
    assert hint(settings) == "v0.2.0 is available: hz system update"


@pytest.mark.parametrize(
    "content",
    [None, "{corrupt", "[]", json.dumps({"latest": 2}), json.dumps({"latest": "0.1.0"}), json.dumps({"latest": "?"})],
    ids=["missing", "corrupt", "not-a-table", "not-a-string", "not-newer", "unparsable"],
)
def test_no_hint_without_a_newer_cached_release(settings, content):
    if content is not None:
        cache(settings, content)
    assert hint(settings) is None


def test_no_hint_when_the_setting_is_off(tmp_path):
    settings = Settings(cache_dir=tmp_path, update_check=False)
    cache(settings, json.dumps({"latest": "0.2.0"}))
    assert hint(settings) is None


def test_no_hint_when_stdout_is_not_a_terminal(settings):
    cache(settings, json.dumps({"latest": "0.2.0"}))
    assert hint(settings, tty=False) is None


@pytest.mark.parametrize("variable", CI_VARIABLES)
def test_no_hint_in_ci(settings, variable):
    cache(settings, json.dumps({"latest": "0.2.0"}))
    assert hint(settings, environ={variable: "true"}) is None


def test_an_empty_ci_variable_is_not_ci(settings):
    cache(settings, json.dumps({"latest": "0.2.0"}))
    assert hint(settings, environ={"CI": ""}) == "v0.2.0 is available: hz system update"


def test_hint_asks_whether_stdout_is_a_terminal_at_call_time(settings, monkeypatch):
    cache(settings, json.dumps({"latest": "0.2.0"}))

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr("sys.stdout", Terminal())
    assert update_hint(settings, current="0.1.0", environ={}) == "v0.2.0 is available: hz system update"
