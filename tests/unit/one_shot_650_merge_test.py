"""Offline evidence and safety checks for the one-shot merge."""

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
spec = importlib.util.spec_from_file_location("merge650", Path(__file__).parents[2] / "scripts/one_shot_650_merge.py")
assert spec
assert spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_requires_separately_reviewed_helper_before_api(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", mod.REPO)
    monkeypatch.setenv("GITHUB_REF", "refs/heads/bootstrap/one-shot-650-merge")
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setenv("REVIEWED_HELPER_SHA", "b" * 40)
    monkeypatch.setattr(mod, "one", lambda _: pytest.fail("unexpected API call"))
    with pytest.raises(ValueError, match="helper SHA"):
        mod.validate()


def test_merge_uses_head_bound_normal_squash(monkeypatch):
    captured = {}

    def run(command, **kwargs):
        captured["command"] = command

        class Result:
            stdout = '{"merged": true}'

        return Result()

    monkeypatch.setattr(mod.subprocess, "run", run)
    mod.one_merge()
    assert f"sha={mod.HEAD}" in captured["command"]
    assert "merge_method=squash" in captured["command"]
    assert not any("admin" in part for part in captured["command"])
