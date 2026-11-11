"""Absent runtime records avoid building a validator; present records retain strict validation."""

from types import SimpleNamespace

import pytest

from heinzel.kernel import private_write
from heinzel.modules.ui import _service

pytestmark = pytest.mark.unit


def test_missing_runtime_does_not_construct_a_json_validator(tmp_path, monkeypatch):
    calls = []
    adapter = _service.TypeAdapter

    def tracked(*args, **kwargs):
        calls.append(args)
        return adapter(*args, **kwargs)

    monkeypatch.setattr(_service, "TypeAdapter", tracked)
    service = SimpleNamespace(runtime=tmp_path / "runtime.json")
    with pytest.raises(FileNotFoundError):
        _service.WebService._control(service, {}, 123, "status")
    assert calls == []


@pytest.mark.parametrize("content", ["not-json", "[]", "{}"])
def test_present_invalid_runtime_still_fails_before_contacting_control(tmp_path, monkeypatch, content):
    path = tmp_path / "runtime.json"
    private_write(path, content)

    def refused(*args):
        pytest.fail("Invalid runtime reached its control socket")

    monkeypatch.setattr(_service.PrivateControl, "request", refused)
    with pytest.raises((ValueError, _service.HeinzelError)):
        _service.WebService._control(SimpleNamespace(runtime=path), {}, 123, "status")
