"""`hz system update` asks the target release's Python requirement before it installs (`UpdateService`)."""

import sys

import pytest

from heinzel import HeinzelError, Progress
from heinzel.kernel import Installation, ReleaseSource
from heinzel.modules.system._service import GitHubReleases
from heinzel.modules.system._update import UpdateService

pytestmark = pytest.mark.unit

MAJOR, MINOR = sys.version_info[:2]


class Releases(ReleaseSource):
    def __init__(self, requires: str | None = None, error: Exception | None = None) -> None:
        self.requires, self.error, self.asked = requires, error, []

    def requires_python(self, version, timeout):
        self.asked.append(version)
        if self.error:
            raise self.error
        return self.requires


def service(releases):
    return UpdateService(Installation(Progress(), uv="uv"), releases)


def test_a_release_for_this_python_keeps_the_tool_interpreter():
    releases = Releases(f">={MAJOR}.{MINOR}")
    assert service(releases).needed_python("9.9.9") is None
    assert releases.asked == ["9.9.9"]


def test_a_release_for_a_newer_python_names_it():
    assert service(Releases(f">={MAJOR}.{MINOR + 1}")).needed_python("9.9.9") == f"{MAJOR}.{MINOR + 1}"


def test_an_unknown_requirement_keeps_the_tool_interpreter():
    assert service(Releases(error=OSError("offline"))).needed_python("9.9.9") is None
    assert service(Releases(None)).needed_python("9.9.9") is None
    assert service(None).needed_python("9.9.9") is None
    assert service(Releases(">=4")).needed_python(None) is None


def test_a_requirement_no_python_meets_is_refused():
    with pytest.raises(HeinzelError, match="keeps working"):
        service(Releases(">=4")).needed_python("9.9.9")


def test_the_base_release_source_cannot_tell():
    assert ReleaseSource().requires_python("1.0.0", 1.0) is None


def test_github_releases_read_requires_python_from_pypi(monkeypatch):
    from heinzel.modules.system import _service

    urls = []

    def fetch(url, timeout):
        urls.append(url)
        return {"info": {"requires_python": ">=3.15"}}

    monkeypatch.setattr(_service, "fetch_json", fetch)
    assert GitHubReleases("owner/name").requires_python("0.9.44", 1.0) == ">=3.15"
    assert urls == ["https://pypi.org/pypi/heinzel/0.9.44/json"]
    monkeypatch.setattr(_service, "fetch_json", lambda url, timeout: {"info": {"requires_python": ""}})
    assert GitHubReleases("owner/name").requires_python("0.9.44", 1.0) is None
    monkeypatch.setattr(_service, "fetch_json", lambda url, timeout: [])
    assert GitHubReleases("owner/name").requires_python("0.9.44", 1.0) is None


def test_the_doctor_says_plainly_how_a_newer_python_is_handled():
    from heinzel.modules.system.doctor import python_detail

    version = sys.version.split()[0]
    assert python_detail(tool=False) == version
    detail = python_detail(tool=True)
    assert detail.startswith(f"{version} (the uv tool's interpreter")
    assert "uv-managed" in detail
    assert "keeps working" in detail


class Highest(Releases):
    def __init__(self, highest: str | Exception) -> None:
        super().__init__()
        self.highest = highest

    def highest_version(self, timeout, *, prereleases=False):
        if isinstance(self.highest, Exception):
            raise self.highest
        return self.highest


def pin(kind, git=None):
    from types import SimpleNamespace

    return SimpleNamespace(kind=kind, git=git)


def test_the_release_an_install_moves_to_is_the_one_asked():
    updates = service(Highest("0.9.44"))
    assert updates._target_release(pin("release", "g"), "0.9.43", "0.9.44", "v0.9.44") == "0.9.44"
    assert updates._target_release(pin("release", "g"), "0.9.43", None, "v0.9.45") == "0.9.45"
    assert updates._target_release(pin("ref", "g"), "0.9.43", None, "git+https://example.test/x@main") is None
    assert updates._target_release(pin("index"), "0.9.43", None, None) == "0.9.44"  # uv resolves the newest
    assert updates._target_release(pin("default"), "0.9.44", None, None) is None  # nothing newer to move to
    assert updates._target_release(pin("default", "g"), "0.9.43", None, None) is None  # a branch: unknown
    assert service(Highest(OSError("offline")))._target_release(pin("index"), "0.9.43", None, None) is None
