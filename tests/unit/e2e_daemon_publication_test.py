"""Fixture signals are published only after their JSON is complete."""

import json
from pathlib import Path

import pytest

from tests.e2e.scheduled_connected_support import _publish_json

pytestmark = pytest.mark.unit


def test_daemon_completion_is_complete_before_it_becomes_visible(tmp_path, monkeypatch):
    target = tmp_path / "daemon-finished.json"
    replace = Path.replace
    seen = []

    def inspect(staged, destination):
        assert destination == target
        assert not target.exists()
        seen.append(json.loads(staged.read_text()))
        return replace(staged, destination)

    monkeypatch.setattr(Path, "replace", inspect)
    payload = {"ok": True, "deduplicated": True, "failure": None}
    _publish_json(tmp_path, target.name, payload)
    assert seen == [payload]
    assert json.loads(target.read_text()) == payload
    assert sorted(path.name for path in tmp_path.iterdir()) == [target.name]
