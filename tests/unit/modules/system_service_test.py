import io
import json
import urllib.error

import pytest
from pydantic import BaseModel

from heinzel import Module, Status, __version__
from heinzel.kernel import Installation, Progress, ReleaseCache, ReleaseSource
from heinzel.modules.system import GitHubReleases, SystemService, _github, github_releases
from heinzel.modules.system._service import DEFAULT_REPOSITORY, repository_of
from heinzel.modules.system.doctor import release_check

pytestmark = pytest.mark.unit


class Section(BaseModel):
    answer: int


def test_info_collects_sections_and_isolates_broken_ones(make_kernel):
    good, model, broken = Module("good", "g"), Module("model", "m"), Module("broken", "b")
    good.info(lambda: {"ok": True})
    model.info(lambda: Section(answer=42))

    @broken.info
    def explode() -> dict[str, str]:
        msg = "boom"
        raise RuntimeError(msg)

    info = SystemService(make_kernel(good=good, model=model, broken=broken)).info()

    assert info.version == __version__
    assert info.sections == {"broken": {"error": "RuntimeError: boom"}, "good": {"ok": True}, "model": {"answer": 42}}


def test_modules_lists_lets_by_kind(make_kernel):
    module = Module("x", "The x module.")

    @module.operation
    def run() -> None:
        """Run."""

    [info] = SystemService(make_kernel(x=module)).modules()
    assert (info.qualname, info.summary, info.builtin, info.lets) == (
        "x",
        "The x module.",
        False,
        {"OperationLet": ["run"]},
    )


SOURCE = f"https://api.github.com/repos/{DEFAULT_REPOSITORY}/releases/latest"
RELEASE = {"tag_name": "v99.0.0", "html_url": "https://github.com/org/heinzel/releases/tag/v99.0.0"}


class FakeGitHub:
    """Answers every request with `answer` (raising it if it is an exception) and records url, headers and timeout."""

    def __init__(self) -> None:
        self.answer: object = RELEASE
        self.requests: list[tuple[str, dict[str, str], float]] = []

    def open(self, request, timeout):
        self.requests.append((request.full_url, dict(request.header_items()), timeout))
        if isinstance(self.answer, Exception):
            raise self.answer
        return json.dumps(self.answer).encode()


class FakeRedirect:
    """Answers the HEAD of `github.com/<repository>/releases/latest` with a status and a Location, or raises."""

    def __init__(self) -> None:
        self.answer: tuple[int, str] | Exception = urllib.error.URLError("redirect not faked")
        self.requests: list[tuple[str, float]] = []

    def head(self, url, timeout):
        self.requests.append((url, timeout))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture
def redirect(monkeypatch):
    fake = FakeRedirect()
    monkeypatch.setattr(_github, "_head", fake.head)
    return fake


@pytest.fixture
def github(monkeypatch, redirect):
    """The REST API is the fake; the redirect is unavailable unless a test gives it an answer."""
    fake = FakeGitHub()
    monkeypatch.setattr(_github, "_open", fake.open)
    return fake


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


def test_release_asks_the_source_and_caches_the_answer(make_kernel, github):
    kernel = make_kernel()
    check = SystemService(kernel).release(None)
    assert check.model_dump() == {
        "current": __version__,
        "latest": "99.0.0",
        "update_available": True,
        "url": RELEASE["html_url"],
        "source": SOURCE,
        "warnings": [],
    }
    assert ReleaseCache(kernel.settings.cache_dir).latest == "99.0.0"
    assert SystemService(kernel).info().latest_release == "99.0.0"


def test_release_asks_the_repository_heinzel_was_installed_from(make_kernel, github):
    SystemService(make_kernel()).release("heinzel @ git+https://github.com/someone/heinzel@v0.1.0")
    [(url, _, timeout)] = github.requests
    assert (url, timeout) == ("https://api.github.com/repos/someone/heinzel/releases/latest", 5)


def test_release_never_sends_a_token(make_kernel, github, monkeypatch):
    for variable in ("GH_TOKEN", "GITHUB_TOKEN", "HZ_SYSTEM_PLUGIN__GITHUB_TOKEN", "HZ_SYSTEM_CHANGELOG__GITHUB_TOKEN"):
        monkeypatch.setenv(variable, "t0ken")
    SystemService(make_kernel(builtins=True)).release(None)
    [(_, headers, _)] = github.requests
    assert headers == {"Accept": "application/vnd.github+json", "User-agent": f"heinzel/{__version__}"}


def test_release_is_up_to_date_when_the_latest_is_installed(make_kernel, github):
    github.answer = {**RELEASE, "tag_name": f"v{__version__}"}
    assert SystemService(make_kernel()).release(None).update_available is False


def test_release_shows_only_release_links_to_github(make_kernel, github):
    github.answer = {**RELEASE, "html_url": "https://example.com/phish"}
    check = SystemService(make_kernel()).release(None)
    assert (check.latest, check.url) == ("99.0.0", None)


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
def test_release_warns_instead_of_failing_and_keeps_the_cache(make_kernel, github, answer, reason):
    github.answer = answer
    kernel = make_kernel()
    check = SystemService(kernel).release(None)
    assert (check.latest, check.update_available, check.url) == (None, False, None)
    assert check.warnings == [f"Could not ask {SOURCE} for the latest release ({reason})."]
    assert ReleaseCache(kernel.settings.cache_dir).latest is None


def test_a_failed_release_check_stamps_the_cache_so_the_hourly_check_waits(make_kernel, github):
    github.answer = urllib.error.URLError("no route to host")
    kernel = make_kernel()
    SystemService(kernel).release(None)
    cache = ReleaseCache(kernel.settings.cache_dir)
    assert (cache.error, cache.checked_at is not None) == ("no route to host", True)


def test_the_release_source_asks_the_latest_release_without_a_token(github):
    assert GitHubReleases("someone/heinzel").latest_version(2) == "99.0.0"
    [(url, headers, timeout)] = github.requests
    assert (url, timeout) == ("https://api.github.com/repos/someone/heinzel/releases/latest", 2)
    assert "Authorization" not in headers


TAG_PAGE = "https://github.com/someone/heinzel/releases/tag/v99.1.0"
REAL_HEAD = _github._head  # the suite's autouse fixture replaces it with an offline one


def test_the_latest_release_comes_from_the_redirect_and_spends_no_rest_quota(github, redirect):
    redirect.answer = (302, TAG_PAGE)
    assert GitHubReleases("someone/heinzel").latest(2) == ("99.1.0", TAG_PAGE)
    assert redirect.requests == [("https://github.com/someone/heinzel/releases/latest", 2)]
    assert github.requests == []


def test_a_rate_limited_rest_api_does_not_blind_the_release_check(make_kernel, github, redirect):
    github.answer = urllib.error.URLError("HTTP Error 403: rate limit exceeded")
    redirect.answer = (302, f"https://github.com/{DEFAULT_REPOSITORY}/releases/tag/v99.1.0")
    kernel = make_kernel()
    check = SystemService(kernel).release(None)
    assert (check.latest, check.update_available, check.warnings) == ("99.1.0", True, [])
    assert ReleaseCache(kernel.settings.cache_dir).latest == "99.1.0"
    assert github.requests == []


@pytest.mark.parametrize(
    "answer",
    [
        urllib.error.URLError("no route to host"),
        urllib.error.HTTPError(TAG_PAGE, 404, "Not Found", {}, io.BytesIO()),
        TimeoutError("timed out"),
        (200, ""),  # no redirect at all
        (302, ""),
        (302, "https://example.com/someone/heinzel/releases/tag/v99.1.0"),  # another host
        (302, "https://github.com/someone/other/releases/tag/v99.1.0"),  # another repository
        (302, "https://github.com/someone/heinzel/releases"),  # no tag
        (302, "https://github.com/someone/heinzel/releases/tag/v99.1.0/extra"),
        (302, "https://github.com/someone/heinzel/releases/tag/v99.1.0?x=1"),
        (302, "https://github.com/someone/heinzel/releases/tag/nightly"),  # no version
    ],
)
def test_an_unusable_redirect_falls_back_to_the_rest_api(github, redirect, answer):
    redirect.answer = answer
    assert GitHubReleases("someone/heinzel").latest(2) == ("99.0.0", RELEASE["html_url"])
    assert [url for url, _, _ in github.requests] == ["https://api.github.com/repos/someone/heinzel/releases/latest"]


def test_the_redirect_is_asked_without_a_token_and_never_followed(monkeypatch):
    seen = []
    monkeypatch.setattr(_github, "_head", REAL_HEAD)

    class Opener:
        def open(self, request, timeout):
            seen.append((request.get_method(), request.full_url, dict(request.header_items()), timeout))
            raise urllib.error.HTTPError(request.full_url, 302, "Found", {"Location": TAG_PAGE}, io.BytesIO())

    monkeypatch.setattr(_github.urllib.request, "build_opener", lambda *handlers: seen.append(handlers) or Opener())
    for variable in ("GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.setenv(variable, "t0ken")
    assert _github.latest_release_url("someone/heinzel", 3) == TAG_PAGE
    handlers, [method, url, headers, timeout] = seen
    assert handlers == (_github._NoRedirects,)
    assert (method, url, timeout) == ("HEAD", "https://github.com/someone/heinzel/releases/latest", 3)
    assert headers == {"User-agent": f"heinzel/{__version__}"}


def test_the_redirect_handler_refuses_every_redirect():
    handler = _github._NoRedirects()
    assert handler.redirect_request(None, None, 302, "Found", {}, TAG_PAGE) is None


def releases(*tags, prerelease=(), draft=()):
    return [{"tag_name": tag, "prerelease": tag in prerelease, "draft": tag in draft} for tag in tags]


@pytest.mark.parametrize(
    ("answer", "prereleases", "expected"),
    [
        # Published by date, a fix for an older line last: SemVer decides, not GitHub's "latest".
        (releases("v0.3.1", "v0.4.0", "v0.2.0"), False, "0.4.0"),
        (releases("v0.5.0rc1", "v0.4.0"), False, "0.4.0"),
        (releases("v0.5.0", "v0.4.0", prerelease={"v0.5.0"}), False, "0.4.0"),  # flagged on GitHub
        (releases("v0.5.0rc1", "v0.4.0"), True, "0.5.0rc1"),
        (releases("v0.6.0", "v0.4.0", draft={"v0.6.0"}), True, "0.4.0"),
        ([*releases("v0.4.0"), {"tag_name": "nightly"}, "no release", {}], False, "0.4.0"),
    ],
)
def test_the_highest_release_is_the_highest_version_not_the_newest(github, answer, prereleases, expected):
    github.answer = answer
    assert GitHubReleases("someone/heinzel").highest_version(2, prereleases=prereleases) == expected
    [(url, headers, timeout)] = github.requests
    assert (url, timeout) == ("https://api.github.com/repos/someone/heinzel/releases?per_page=30&page=1", 2)
    assert "Authorization" not in headers


@pytest.mark.parametrize(
    ("answer", "error"),
    [(releases("v1.0.0rc1"), ValueError), ([], ValueError), ({"tag_name": "v1.0.0"}, TypeError)],
)
def test_no_highest_release_is_an_error(github, answer, error):
    github.answer = answer
    with pytest.raises(error):
        GitHubReleases("someone/heinzel").highest_version(2)


def test_the_kernel_release_source_has_no_answer_of_its_own():
    with pytest.raises(NotImplementedError):
        ReleaseSource().highest_version(2)


def test_the_release_source_reads_the_repository_from_the_installation(tmp_path):
    assert github_releases(Installation(Progress(), prefix=tmp_path, uv="uv")).repository == DEFAULT_REPOSITORY


@pytest.mark.parametrize(
    ("cache_state", "update_check", "expected"),
    [
        (("99.0.0", None), True, (f"hz 99.0.0 is available (installed {__version__})", "hz system update")),
        ((__version__, None), True, (f"hz {__version__} is up to date (latest release {__version__})", "")),
        ((None, "offline"), True, ("no release known yet; the last check failed: offline", "hz system release")),
        (
            (None, None),
            False,
            ("no release known yet; the hourly check is off (update_check = false)", "hz system release"),
        ),
    ],
)
def test_doctor_says_what_the_release_cache_knows(settings, cache_state, update_check, expected):
    cached, error = cache_state
    cache = ReleaseCache(settings.cache_dir)
    if cached:
        cache.remember(cached)
    if error:
        cache.stamp(error=error)
    check = release_check(settings.model_copy(update={"update_check": update_check}))
    assert (check.name, check.status, (check.detail, check.fix)) == ("release", Status.UP, expected)
