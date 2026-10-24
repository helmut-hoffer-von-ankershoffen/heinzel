import types
from types import SimpleNamespace

import pytest

from heinzel.kernel import _discovery

pytestmark = pytest.mark.unit


class FakeEntryPoint:
    def __init__(self, name, target=None, error=None):
        self.name = name
        self.dist = SimpleNamespace(name=f"heinzel-{name}", version="1.0.0")
        self._target = target
        self._error = error

    def load(self):
        if self._error:
            raise self._error
        return self._target


def test_broken_plugins_are_reported_not_raised(monkeypatch):
    package = types.ModuleType("good_plugin")
    entry_points = [
        FakeEntryPoint("bad", error=ImportError("missing")),
        FakeEntryPoint("good", target=package),
        FakeEntryPoint("object", target=object()),
    ]
    monkeypatch.setattr(_discovery, "entry_points", lambda group: entry_points)

    _, errors = _discovery.discover()

    assert errors == {
        "bad": "ImportError: missing",
        "object": "TypeError: entry point must name a package, not object",
    }


def test_plugins_can_be_skipped(monkeypatch):
    monkeypatch.setattr(_discovery, "entry_points", lambda group: pytest.fail("must not scan entry points"))
    records, errors = _discovery.discover(plugins=False)
    assert {r.qualname for r in records} >= {"system", "system.plugin"}
    assert errors == {}


class FakeMetadata:
    def __init__(self, *urls):
        self.urls = list(urls)

    def get_all(self, name):
        assert name == "Project-URL"
        return self.urls or None


@pytest.mark.parametrize(
    ("urls", "expected"),
    [
        (["Homepage, https://example.org", "Source, https://github.com/o/src"], "https://github.com/o/src"),
        (["Homepage, https://example.org", "Repository, https://github.com/o/repo"], "https://github.com/o/repo"),
        (["Source Code, https://github.com/o/code", "Homepage, https://example.org"], "https://github.com/o/code"),
        (["Home-page, https://example.org"], "https://example.org"),
        (["Issues, https://github.com/o/x/issues", "Changelog, https://github.com/o/x/CHANGELOG.md"], None),
        (["Source, ftp://example.org/x", "Homepage, javascript:alert(1)"], None),
        (["Source, https://example.org/a b"], None),
        ([], None),
    ],
)
def test_the_repository_comes_from_project_urls(urls, expected):
    assert _discovery.repository_of(SimpleNamespace(metadata=FakeMetadata(*urls))) == expected


def test_broken_metadata_costs_only_the_link():
    assert _discovery.repository_of(SimpleNamespace(name="broken")) is None


def test_plugins_and_the_core_carry_their_repository(monkeypatch):
    package = types.ModuleType("linked_plugin")
    package.__path__ = []
    package.module = _discovery.Module("linked", "Linked.")
    entry_point = FakeEntryPoint("linked", target=package)
    entry_point.dist.metadata = FakeMetadata("Source, https://github.com/o/heinzel-linked")
    monkeypatch.setattr(_discovery, "entry_points", lambda group: [entry_point])

    records, errors = _discovery.discover()

    assert errors == {}
    origins = {record.qualname: record.origin for record in records}
    assert origins["linked"].url == "https://github.com/o/heinzel-linked"
    assert origins["system"].url == "https://github.com/helmut-hoffer-von-ankershoffen/heinzel"


def test_no_core_repository_without_core_metadata(monkeypatch):
    def missing(name):
        raise _discovery.PackageNotFoundError(name)

    monkeypatch.setattr(_discovery, "distribution", missing)
    assert _discovery.core_repository() is None
