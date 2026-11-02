"""Newer stable plugin releases from PyPI, for automatic updates (ADR 0116)."""

import pytest

from heinzel.kernel import CorePin, Installation
from heinzel.modules.system._plugin_releases import PyPIPluginReleases

pytestmark = pytest.mark.unit


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
        return {"info": {"version": latest[url.split("/")[-2]]}}

    releases = PyPIPluginReleases(Receipt(pins, versions), fetch)
    assert releases.newer(5.0) == {"heinzel-newer": "1.1.0"}
    assert all(url.startswith("https://pypi.org/pypi/heinzel-") for url in asked)
    assert len(asked) == 4  # the git plugin is never asked about


def test_no_index_plugins_asks_nothing():
    def fetch(url, timeout):
        raise AssertionError(url)

    assert PyPIPluginReleases(Receipt([], {}), fetch).newer(5.0) == {}
