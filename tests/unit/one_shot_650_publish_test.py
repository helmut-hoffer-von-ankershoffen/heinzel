"""Fail-closed checks for the dormant 650 evidence verifier."""

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit
source = Path(__file__).resolve().parents[2] / "scripts/one_shot_650_publish.py"
spec = importlib.util.spec_from_file_location("publisher", source)
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


def test_source_rejects_moved_base_without_reading_or_writing_other_state():
    with (
        patch.object(publisher, "one", return_value={"object": {"sha": "moved"}}) as read,
        pytest.raises(ValueError, match="main drift"),
    ):
        publisher.source()
    assert read.call_count == 1


def test_evidence_rejects_wrong_run_attempt_before_reading_jobs(monkeypatch):
    monkeypatch.setenv("REVIEWED_HELPER_SHA", "reviewed")
    run = {"event": "push", "head_branch": publisher.BRANCH, "head_sha": "reviewed", "run_attempt": 2}
    with (
        patch.object(publisher, "one", return_value=run),
        patch.object(publisher, "api") as api,
        pytest.raises(ValueError, match="workflow source drift"),
    ):
        publisher.evidence(42)
    api.assert_not_called()


def test_publication_stays_held_after_successful_verification(monkeypatch):
    for key, value in {
        "GITHUB_REPOSITORY": publisher.REPO,
        "GITHUB_REF": f"refs/heads/{publisher.BRANCH}",
        "GITHUB_ACTOR": "helmut-hoffer-von-ankershoffen",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_SHA": "reviewed",
        "REVIEWED_HELPER_SHA": "reviewed",
        "GITHUB_RUN_ID": "42",
    }.items():
        monkeypatch.setenv(key, value)
    with (
        patch.object(publisher, "source") as source_check,
        patch.object(publisher, "evidence") as evidence_check,
        pytest.raises(ValueError, match="publication held"),
    ):
        publisher.main()
    assert source_check.call_count == 2
    evidence_check.assert_called_once_with(42)


def test_status_publisher_sets_pending_then_reads_back_each_success():
    import json
    from types import SimpleNamespace

    states = {}
    calls = []
    url = f"https://github.com/{publisher.REPO}/actions/runs/42"
    checks = [
        {"context": context, **({"integration_id": publisher.GITHUB_ACTIONS_APP} if context != "CI OK" else {})}
        for context in publisher.CONTEXTS
    ]
    rules = [[{"type": "required_status_checks", "parameters": {"required_status_checks": checks}}]]

    def run(command, **_kwargs):
        state = next(arg.removeprefix("state=") for arg in command if arg.startswith("state="))
        context = next(arg.removeprefix("context=") for arg in command if arg.startswith("context="))
        calls.append((context, state))
        states[context] = {
            "context": context,
            "state": state,
            "target_url": url,
            "creator": {"login": "github-actions[bot]"},
        }
        return SimpleNamespace(stdout=json.dumps(states[context]).encode())

    with (
        patch.object(publisher, "api", return_value=rules),
        patch.object(publisher, "one", side_effect=lambda _path: {"statuses": list(states.values())}),
        patch.object(publisher.subprocess, "run", side_effect=run),
        patch.object(publisher, "source"),
        patch.object(publisher, "evidence"),
    ):
        publisher.publish_statuses(42)
    assert calls == [
        ("CI OK", "pending"),
        ("merge-gate", "pending"),
        ("coverage-floor", "pending"),
        ("merge-gate", "success"),
        ("coverage-floor", "success"),
        ("CI OK", "success"),
    ]
