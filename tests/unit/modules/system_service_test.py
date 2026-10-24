import json
import urllib.error

import pytest
from pydantic import BaseModel

from heinzel import Module, Status, __version__
from heinzel.kernel import Installation, Progress, ReleaseCache
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


@pytest.fixture
def github(monkeypatch):
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
    for variable in ("GH_TOKEN", "GITHUB_TOKEN", "HZ_SYSTEM_PLUGIN__GITHUB_TOKEN"):
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
