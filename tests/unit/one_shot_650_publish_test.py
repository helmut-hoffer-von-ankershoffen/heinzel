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
