"""Newer stable plugin releases from PyPI, for automatic updates (ADR 0116)."""

from datetime import UTC, datetime

import pytest

from heinzel.kernel import CorePin, Installation, PluginRelease
from heinzel.modules.system._plugin_releases import PyPIPluginReleases

pytestmark = pytest.mark.unit
UPLOAD = {"upload_time_iso_8601": "2026-10-01T08:00:00.000000Z", "yanked": False}


class Receipt(Installation):
    def __init__(self, pins, versions):
        self.pins, self.versions = pins, versions

    def plugin_pins(self):
        return self.pins

    def installed_versions(self, names):
        return {name: self.versions[name] for name in names if name in self.versions}


def test_only_index_plugins_with_a_newer_stable_release_allowed_by_their_specifier_count():
    pins = [
        CorePin("index", "heinzel-newer"),
        CorePin("index", "heinzel-current"),
        CorePin("index", "heinzel-capped<2"),
        CorePin("index", "heinzel-pre"),
        CorePin("ref", "heinzel-git @ git+https://example.test/x@main", ref="main"),
    ]
    versions = {"heinzel-newer": "1.0.0", "heinzel-current": "1.2.0", "heinzel-capped": "1.0.0", "heinzel-pre": "1.0"}
    latest = {"heinzel-newer": "1.1.0", "heinzel-current": "1.2.0", "heinzel-capped": "2.0.0", "heinzel-pre": "2.0b1"}
    asked = []

    def fetch(url, timeout):
        asked.append(url)
        return {"releases": {"1.0.0": [UPLOAD], latest[url.split("/")[-2]]: [UPLOAD]}}

    releases = PyPIPluginReleases(Receipt(pins, versions), fetch)
    assert releases.newer(5.0) == {"heinzel-newer": "1.1.0"}
    assert all(url.startswith("https://pypi.org/pypi/heinzel-") for url in asked)
    assert len(asked) == 4  # the git plugin is never asked about


def test_no_index_plugins_asks_nothing():
    def fetch(url, timeout):
        raise AssertionError(url)

    assert PyPIPluginReleases(Receipt([], {}), fetch).newer(5.0) == {}


def test_a_yanked_release_is_never_offered_and_the_upload_time_comes_with_the_release():
    def fetch(url, timeout):
        return {
            "releases": {
                "1.0.0": [UPLOAD],
                "1.1.0": [
                    {"upload_time_iso_8601": "2026-11-01T10:00:00Z", "yanked": False},
                    {"upload_time_iso_8601": "2026-11-01T09:00:00Z", "yanked": False},
                ],
                "1.2.0": [{"upload_time_iso_8601": "2026-11-02T08:00:00Z", "yanked": True}],
                "1.3.0b1": [UPLOAD],
                "1.4.0": [],
            },
        }

    releases = PyPIPluginReleases(Receipt([CorePin("index", "heinzel-x")], {"heinzel-x": "1.0.0"}), fetch)
    assert releases.newer_releases(5.0) == {
        "heinzel-x": PluginRelease("1.1.0", datetime(2026, 11, 1, 9, 0, tzinfo=UTC)),  # the first upload
    }
