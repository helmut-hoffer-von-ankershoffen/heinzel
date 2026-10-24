import json
from datetime import UTC, datetime

import pytest

from heinzel.kernel import ReleaseCache

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
