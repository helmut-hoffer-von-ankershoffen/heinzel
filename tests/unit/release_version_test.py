"""A candidate SDK version can become its final release without an extra bump or a downgrade."""

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

spec = importlib.util.spec_from_file_location(
    "release_version", Path(__file__).resolve().parents[2] / "scripts/release_version.py"
)
assert spec
assert spec.loader
release_version = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_version)


@pytest.mark.parametrize(
    ("current", "suggested", "expected"),
    [
        ("0.9.0", "0.8.2", "0.9.0"),
        ("0.8.1", "0.8.2", "0.8.2"),
        ("0.8.1", "0.9.0", "0.9.0"),
        ("0.9.0", "0.10.0", "0.10.0"),
        ("0.10.0", "0.9.9", "0.10.0"),
    ],
)
def test_reserved_sdk_versions_and_ordinary_releases(current, suggested, expected):
    assert release_version.select_version(current, suggested) == expected


@pytest.mark.parametrize(
    ("current", "suggested", "released", "expected"),
    [
        ("0.9.1", "0.10.0", "0.9.0", "0.9.1"),
        ("0.9.1", "0.9.1", "0.9.0", "0.9.1"),
        ("0.9.0", "0.10.0", "0.9.0", "0.10.0"),
        ("0.9.0", "0.9.1", "0.9.0", "0.9.1"),
    ],
)
def test_explicit_candidate_is_finalized_while_released_versions_advance(current, suggested, released, expected):
    assert release_version.select_version(current, suggested, released) == expected


@pytest.mark.parametrize("invalid", ["v0.9.0", "00.9.0", "0.9.0rc1", "0.9", "../0.9.0"])
def test_noncanonical_release_identity_is_refused(invalid):
    with pytest.raises(ValueError, match="canonical"):
        release_version.select_version(invalid, "0.9.0")


def test_noncanonical_published_identity_is_refused():
    with pytest.raises(ValueError, match="canonical"):
        release_version.select_version("0.9.1", "0.10.0", "v0.9.0")


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ("v0.7.30\nv0.9.0\nv0.8.1", "0.9.0"),
        ("v0.9.99\nv0.10.1\nv0.10.0", "0.10.1"),
        ("v0.9.0\nv0.10.0rc1\nv01.0.0\nheinzel-clock/v2.0.0", "0.9.0"),
        ("v0.9.0rc1\nheinzel-clock/v2.0.0", None),
        ("", None),
    ],
)
def test_highest_canonical_core_release_is_selected_from_reachable_tags(tags, expected):
    assert release_version.latest_release(tags) == expected
