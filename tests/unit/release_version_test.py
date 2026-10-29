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


@pytest.mark.parametrize("invalid", ["v0.9.0", "00.9.0", "0.9.0rc1", "0.9", "../0.9.0"])
def test_noncanonical_release_identity_is_refused(invalid):
    with pytest.raises(ValueError, match="canonical"):
        release_version.select_version(invalid, "0.9.0")
