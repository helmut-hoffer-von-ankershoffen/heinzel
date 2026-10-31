"""Plugin previews and Discover read the published static catalog and raw files before any GitHub API request."""

import email.message
import io
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from heinzel import HeinzelError
from heinzel.modules.system import _github
from heinzel.modules.system.plugin import _feed
from heinzel.modules.system.plugin._feed import feed_showcase, load_feed
from heinzel.modules.system.plugin._service import PluginService
from heinzel.modules.system.plugin._showcase import raw_showcase
from heinzel.modules.system.plugin._sources import parse

pytestmark = pytest.mark.unit
REPOSITORY = "fictional-lab/plugins"
SHA = "a" * 40
OTHER = "b" * 40
RAW = f"https://raw.githubusercontent.com/{REPOSITORY}/{SHA}/plugins/heinzel-harbor/"
SPEC = f"heinzel-harbor @ git+https://github.com/{REPOSITORY}#subdirectory=plugins/heinzel-harbor"
PINNED = f"heinzel-harbor @ git+https://github.com/{REPOSITORY}@{SHA}#subdirectory=plugins/heinzel-harbor"
OLDER = f"heinzel-harbor @ git+https://github.com/{REPOSITORY}@{OTHER}#subdirectory=plugins/heinzel-harbor"


def entry(**changes):
    value = {
        "name": "heinzel-harbor",
        "title": "Harbor",
        "summary": "A harbor.",
        "directory": "plugins/heinzel-harbor",
        "source": {"repository": REPOSITORY, "revision": SHA, "visibility": "public"},
        "screenshots": [
            {"url": RAW + "docs/ui.png", "surface": "ui", "caption": "The harbor"},
            {"url": RAW + "docs/cli.webp", "surface": "cli", "caption": "The command"},
        ],
        "soundscapes": [{"name": "tide", "title": "Harbor tide", "video": "abcdefghijk"}],
    }
    value.update(changes)
    return value


def test_a_published_entry_answers_without_any_request():
    gallery = feed_showcase(parse(SPEC), [entry()])
    assert gallery
    assert gallery.revision == SHA
    assert [shot.surface for shot in gallery.screenshots] == ["ui", "cli"]
    assert gallery.soundscapes[0].url == "https://www.youtube.com/watch?v=abcdefghijk"
    assert feed_showcase(parse(PINNED), [entry()])


@pytest.mark.parametrize(
    "plugins",
    [
        [entry(source={"repository": REPOSITORY, "revision": SHA, "visibility": "private"})],
        [entry(source={"repository": REPOSITORY, "revision": "main", "visibility": "public"})],
        [entry(directory="plugins/other")],
        [entry(name="heinzel-other")],
        [],
    ],
)
def test_only_a_public_immutable_entry_for_this_exact_plugin_is_used(plugins):
    assert feed_showcase(parse(SPEC), plugins) is None


def test_an_installed_other_revision_is_never_answered_from_the_catalog():
    assert feed_showcase(parse(OLDER), [entry()]) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/ui.png",
        f"https://raw.githubusercontent.com/{REPOSITORY}/{OTHER}/plugins/heinzel-harbor/docs/ui.png",
        RAW + "../escape.png",
        RAW + "docs/ui.svg",
    ],
)
def test_an_image_outside_its_plugin_revision_is_rejected(url):
    bad = entry(screenshots=[{"url": url, "surface": "ui", "caption": "x"}])
    with pytest.raises(HeinzelError, match="does not belong"):
        feed_showcase(parse(SPEC), [bad])


def document(*plugins):
    return {"format": 1, "plugins": list(plugins)}


def test_the_catalog_is_cached_and_revalidated_with_its_etag(tmp_path: Path, monkeypatch):
    calls = []

    def fetch(url, etag):
        calls.append(etag)
        return (304, etag, b"") if etag else (200, '"v1"', json.dumps(document(entry())).encode())

    monkeypatch.setattr(_feed, "_fetch", fetch)
    assert load_feed("https://x.test/c.json", tmp_path, now=1000)[0]["name"] == "heinzel-harbor"
    assert load_feed("https://x.test/c.json", tmp_path, now=1100)  # fresh: no request
    assert calls == [None]
    assert load_feed("https://x.test/c.json", tmp_path, now=2000)  # stale: asks "changed?" and gets a 304
    assert calls == [None, '"v1"']


def test_an_unreachable_catalog_serves_the_stale_copy_or_fails(tmp_path: Path, monkeypatch):
    def offline(url, etag):
        msg = "offline"
        raise OSError(msg)

    monkeypatch.setattr(_feed, "_fetch", offline)
    with pytest.raises(OSError, match="offline"):
        load_feed("https://x.test/c.json", tmp_path, now=1)
    monkeypatch.setattr(_feed, "_fetch", lambda url, etag: (200, None, json.dumps(document(entry())).encode()))
    load_feed("https://x.test/c.json", tmp_path, now=1)
    monkeypatch.setattr(_feed, "_fetch", offline)
    assert load_feed("https://x.test/c.json", tmp_path, now=10_000)


def test_an_unknown_catalog_format_is_refused(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(_feed, "_fetch", lambda url, etag: (200, None, b'{"format": 2, "plugins": []}'))
    with pytest.raises(ValueError, match="unknown format"):
        load_feed("https://x.test/c.json", tmp_path)


PROJECT = """[project]
name = "heinzel-harbor"
[tool.heinzel.catalog]
title = "Harbor"
[[tool.heinzel.catalog.screenshots]]
path = "docs/ui.png"
surface = "ui"
caption = "The harbor"
"""


def raw_fixture(project=PROJECT, size=(500, "image/png")):
    def text(url, limit):
        if url == RAW + "pyproject.toml":
            return project
        raise LookupError(url)

    return text, lambda url: size


def test_a_pinned_revision_is_previewed_from_raw_files_alone():
    text, size = raw_fixture()
    gallery, discover = raw_showcase(parse(PINNED), text, size)
    assert gallery.screenshots[0].url == RAW + "docs/ui.png"
    assert discover is True  # undeclared soundscapes would need the file tree


def test_a_default_branch_is_not_a_pinned_revision():
    assert raw_showcase(parse(SPEC), *raw_fixture()) is None


def test_a_missing_pyproject_is_reported_as_no_public_preview():
    gallery, _ = raw_showcase(
        parse(PINNED), lambda url, limit: (_ for _ in ()).throw(LookupError(url)), lambda u: (1, "")
    )
    assert gallery.screenshots == []
    assert "private or not readable" in gallery.notice


@pytest.mark.parametrize("size", [(3 * 1024 * 1024, "image/png"), (-1, "image/png"), (10, "text/html")])
def test_raw_images_must_be_bounded_rasters(size):
    with pytest.raises(HeinzelError, match="bounded regular raster"):
        raw_showcase(parse(PINNED), *raw_fixture(size=size))


def service(feed=(), **changes):
    value = object.__new__(PluginService)
    value.feed = lambda: list(feed)
    value.raw_text, value.raw_size = raw_fixture()
    value.github_api = lambda path: (_ for _ in ()).throw(AssertionError(path))
    value._published_repositories = set()
    for key, item in changes.items():
        setattr(value, key, item)
    return value


def test_the_catalog_beats_raw_and_the_api():
    assert service([entry()]).showcase(SPEC).revision == SHA


def test_an_installed_older_revision_uses_its_own_raw_files_not_the_catalog():
    seen = []

    def text(url, limit):
        seen.append(url)
        return PROJECT

    offline = service(
        [entry()],
        raw_text=text,
        raw_size=lambda u: (5, "image/png"),
        github_api=lambda p: (_ for _ in ()).throw(OSError(p)),
    )
    gallery = offline.showcase(OLDER)
    assert gallery.revision == OTHER
    assert OTHER in gallery.screenshots[0].url
    assert OTHER in seen[0]


def test_hinted_soundscapes_are_read_at_the_installed_revision_without_the_api():
    sound = 'kind = "stream"\ntitle = "Tide"\n[stream]\nprovider = "youtube"\nvideo = "abcdefghijk"\n'

    def text(url, limit):
        if url.endswith("pyproject.toml"):
            return PROJECT
        if url.endswith("/src/h/soundscapes/tide.toml") and OTHER in url:
            return sound
        raise LookupError(url)  # a hinted file this revision no longer has is skipped

    hinted = entry(
        soundscapes=[
            {"title": "Tide", "video": "abcdefghijk", "path": "plugins/heinzel-harbor/src/h/soundscapes/tide.toml"},
            {"title": "Gone", "video": "abcdefghijl", "path": "plugins/heinzel-harbor/src/h/soundscapes/gone.toml"},
        ]
    )
    gallery = service([hinted], raw_text=text, raw_size=lambda u: (5, "image/png")).showcase(OLDER)
    assert [link.title for link in gallery.soundscapes] == ["Tide"]
    assert "newer one may be missing" in gallery.notice


def test_undeclared_soundscape_discovery_degrades_when_github_is_rate_limited():
    def limited(path):
        raise _github.RateLimitedError(None, authenticated=False)

    gallery = service(github_api=limited).showcase(PINNED)
    assert gallery.screenshots
    assert "Soundscape links could not be listed" in gallery.notice


def test_a_rate_limited_branch_lookup_says_so_plainly():
    def limited(path):
        raise _github.RateLimitedError(2_000_000_000, authenticated=False)

    with pytest.raises(HeinzelError, match=r"request limit is used up until \d\d:\d\d.*github_token"):
        service(github_api=limited).showcase(SPEC)


def test_discover_lists_published_public_plugins_without_a_request():
    found = service(
        [entry(), entry(name="heinzel-x", source={"repository": REPOSITORY, "revision": SHA, "visibility": "private"})]
    )
    plugins = found._published_plugins(set())
    assert [plugin.name for plugin in plugins] == ["heinzel-harbor"]
    assert plugins[0].install_spec == SPEC
    assert found._published_repositories == {REPOSITORY}


class Reply:
    def __init__(self, body, headers=None):
        self.body, self.headers = body, headers or {}

    def read(self, limit):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, headers):
    message = email.message.Message()
    for key, value in headers.items():
        message[key] = value
    return urllib.error.HTTPError("https://api.github.com/x", code, "x", message, io.BytesIO(b""))


def test_github_requests_are_conditional_and_a_304_costs_nothing(monkeypatch):
    _github._CONDITIONAL.clear()
    sent = []

    class Opener:
        def open(self, request, timeout):
            sent.append(request.get_header("If-none-match"))
            if sent[-1]:
                raise http_error(304, {})
            return Reply(b'{"a": 1}', {"ETag": '"e1"'})

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: Opener())
    for _ in range(2):
        assert _github._open(urllib.request.Request("https://api.github.com/x"), 5) == b'{"a": 1}'
    assert sent == [None, '"e1"']


@pytest.mark.parametrize(
    ("code", "headers"),
    [(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1999999999"}), (429, {"Retry-After": "30"})],
)
def test_a_used_up_limit_is_its_own_error_with_a_reset_time(monkeypatch, code, headers):
    _github._CONDITIONAL.clear()

    class Opener:
        def open(self, request, timeout):
            raise http_error(code, headers)

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: Opener())
    with pytest.raises(_github.RateLimitedError) as caught:
        _github._open(urllib.request.Request("https://api.github.com/x"), 5)
    assert caught.value.reset
    assert "request limit is used up until" in caught.value.message()
