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
