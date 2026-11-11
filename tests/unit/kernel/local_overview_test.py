"""Only private, bounded, inert historical records reach the interactive overview."""

import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from heinzel.kernel import CachedOverviewLet, _local_overview
from heinzel.kernel._local_overview import MAX_RECORD_BYTES, cached_observation

pytestmark = pytest.mark.unit
NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def observation_clocks(monkeypatch):
    """Semantic record tests do not measure host scheduling or cold validator initialization."""
    monkeypatch.setattr(_local_overview, "time", SimpleNamespace(monotonic=lambda: 0.0))
    monkeypatch.setattr(
        _local_overview, "datetime", SimpleNamespace(now=lambda zone: NOW, fromisoformat=datetime.fromisoformat)
    )


@pytest.fixture
def record(tmp_path):
    tmp_path.chmod(0o700)
    directory = tmp_path / "overview"
    directory.mkdir(mode=0o700)
    path = directory / "example.sharing.json"
    path.write_text(json.dumps({"state": "running", "observed_at": NOW.isoformat()}))
    path.chmod(0o600)
    return tmp_path, path


def read(record):
    root, path = record
    return cached_observation(root, path.name, 0.1)


def test_fresh_record_is_explicitly_historical(record):
    result = read(record)
    assert result.state == "unknown"
    assert result.detail.startswith("last observed running at ")


@pytest.mark.parametrize("age", [timedelta(minutes=6), timedelta(seconds=-10)])
def test_stale_and_future_evidence_never_claims_running(record, age):
    root, path = record
    path.write_text(json.dumps({"state": "running", "observed_at": (NOW - age).isoformat()}))
    assert read((root, path)).detail == "last observation stale or unavailable"


@pytest.mark.parametrize("content", ["[]", "broken", '{"state": []}', "x" * (MAX_RECORD_BYTES + 1)])
def test_invalid_record_is_unavailable(record, content):
    record[1].write_text(content)
    assert read(record).detail == "last observation unavailable"


@pytest.mark.parametrize(
    "payload",
    [
        {"state": "https://example.test/token", "observed_at": NOW.isoformat()},
        {"state": "running", "observed_at": NOW.isoformat(), "url": "secret"},
        {"state": "running", "observed_at": "2026-11-03T12:00:00"},
    ],
)
def test_arbitrary_or_unscoped_fields_are_not_displayed(record, payload):
    record[1].write_text(json.dumps(payload))
    assert read(record).detail == "last observation unavailable"


@pytest.mark.parametrize("kind", ["public-file", "public-directory", "symlink", "hardlink", "fifo"])
def test_unsafe_files_do_not_block_or_disclose(record, kind):
    root, path = record
    if kind == "public-file":
        path.chmod(0o644)
    elif kind == "public-directory":
        path.parent.chmod(0o755)
    elif kind == "hardlink":
        os.link(path, path.with_suffix(".linked"))
    else:
        path.unlink()
        if kind == "symlink":
            path.symlink_to(root / "missing")
        else:
            os.mkfifo(path, 0o600)
    assert read(record).detail == "last observation unavailable"


def test_expired_budget_does_not_open_files(record, monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("expired observations must not open records")

    monkeypatch.setattr(os, "open", fail)
    assert cached_observation(record[0], record[1].name, -1).state == "unknown"


def test_declaration_rejects_path_traversal():
    with pytest.raises(ValueError, match="Overview names"):
        CachedOverviewLet(name="../escape")


@pytest.mark.parametrize("filename", ["../escape.json", "/escape.json", "example..sharing.json", "example.sharing"])
def test_cache_name_cannot_escape_its_private_folder(record, filename):
    assert cached_observation(record[0], filename, 0.1).detail == "last observation unavailable"


def test_budget_expiring_during_read_never_displays_history(record, monkeypatch):
    readings = iter([0.0, 0.1])
    monkeypatch.setattr(_local_overview, "time", SimpleNamespace(monotonic=lambda: next(readings)))
    result = read(record)
    assert result.state == "unknown"
    assert result.detail == "last observation stale or unavailable"
