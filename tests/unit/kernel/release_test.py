import json
from datetime import UTC, datetime, timedelta

import pytest

from heinzel.kernel import ReleaseCache, ReleaseSource

pytestmark = pytest.mark.unit


@pytest.fixture
def cache(tmp_path):
    return ReleaseCache(tmp_path / "cache")


def write(cache, content):
    cache.cache_dir.mkdir(parents=True, exist_ok=True)
    cache.path.write_text(content, encoding="utf-8")


def test_remembers_the_latest_release_and_when_it_was_seen(cache):
    seen = datetime(2026, 10, 24, 8, 0, tzinfo=UTC)
    cache.remember("0.2.0", seen)
    assert (cache.latest, cache.checked_at) == ("0.2.0", seen)
    assert json.loads(cache.path.read_text(encoding="utf-8")) == {"latest": "0.2.0", "checked_at": seen.isoformat()}


def test_remember_stamps_the_time_by_default(cache):
    cache.remember("0.2.0")
    assert cache.checked_at is not None


@pytest.mark.parametrize(
    ("current", "newer"), [("0.1.0", "0.2.0"), ("0.2.0", None), ("0.3.0.dev1", None), ("not a version", None)]
)
def test_newer_than_names_only_a_newer_release(cache, current, newer):
    cache.remember("0.2.0")
    assert cache.newer_than(current) == newer


@pytest.mark.parametrize(
    "content",
    [None, "{corrupt", "[]", json.dumps({"latest": 2}), json.dumps({"latest": "?", "checked_at": "yesterday"})],
    ids=["missing", "corrupt", "not-a-table", "not-a-string", "unparsable"],
)
def test_a_missing_or_broken_cache_knows_nothing(cache, content):
    if content is not None:
        write(cache, content)
    assert (cache.latest, cache.checked_at, cache.newer_than("0.1.0")) == (None, None, None)


def test_an_unwritable_cache_directory_only_loses_the_hint(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("", encoding="utf-8")
    cache = ReleaseCache(blocked / "cache")
    cache.remember("0.2.0")
    assert cache.latest is None


NOW = datetime(2026, 10, 24, 8, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("checked", "fresh"),
    [
        (None, False),
        (NOW - timedelta(minutes=59), True),
        (NOW - timedelta(hours=1), False),
        (NOW + timedelta(1), False),
    ],
    ids=["never", "within-the-hour", "an-hour-ago", "in-the-future"],
)
def test_is_fresh_within_max_age_only(cache, checked, fresh):
    if checked is not None:
        cache.stamp(checked_at=checked)
    assert cache.is_fresh(timedelta(hours=1), now=NOW) is fresh


def test_stamp_keeps_the_latest_release_and_records_a_failure(cache):
    cache.remember("0.2.0", NOW - timedelta(days=1))
    cache.stamp(error="offline", checked_at=NOW)
    assert (cache.latest, cache.checked_at, cache.error) == ("0.2.0", NOW, "offline")


def test_remember_clears_a_failure(cache):
    cache.stamp(error="offline")
    cache.remember("0.3.0")
    assert (cache.latest, cache.error) == ("0.3.0", None)


class Source(ReleaseSource):
    def __init__(self, answer):
        self.answer = answer

    def latest_version(self, timeout):
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_refresh_remembers_what_the_source_says(cache):
    cache.refresh(Source("v0.3.0"), timeout=1)
    assert (cache.latest, cache.error) == ("0.3.0", None)


@pytest.mark.parametrize(
    ("answer", "error"),
    [(OSError("offline"), "OSError: offline"), ("nightly", "InvalidVersion: Invalid version: 'nightly'")],
)
def test_refresh_never_raises_and_stamps_the_failure(cache, answer, error):
    cache.remember("0.2.0", NOW)
    cache.refresh(Source(answer), timeout=1)
    assert (cache.latest, cache.error) == ("0.2.0", error)
    assert cache.checked_at != NOW


def test_the_base_source_knows_nothing():
    with pytest.raises(NotImplementedError):
        ReleaseSource().latest_version(1)


def test_a_write_replaces_the_file_whole(cache):
    cache.remember("0.2.0")
    assert [path.name for path in cache.cache_dir.iterdir()] == ["release.json"]
