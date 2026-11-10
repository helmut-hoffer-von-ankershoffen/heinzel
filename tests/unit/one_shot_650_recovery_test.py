"""Identity and evidence tests for the one-shot 650 recovery."""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit
root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "scripts"))
spec = importlib.util.spec_from_file_location("recovery", root / "scripts/one_shot_650_recovery.py")
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


def test_recovery_uses_original_evidence_and_a_distinct_status_run(monkeypatch):
    env = {
        "GITHUB_REPOSITORY": recovery.guard.REPO,
        "GITHUB_REF": recovery.RECOVERY_REF,
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_ACTOR": "helmut-hoffer-von-ankershoffen",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_SHA": "reviewed",
        "GITHUB_WORKFLOW_SHA": "reviewed",
        "REVIEWED_RECOVERY_SHA": "reviewed",
        "GITHUB_RUN_ID": "123",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with (
        patch.object(recovery.guard, "source_review"),
        patch.object(recovery.guard, "source") as source,
        patch.object(recovery, "original_evidence") as evidence,
        patch.object(recovery.guard, "publish_statuses") as publish,
    ):
        recovery.main()
    assert source.call_count == 2
    evidence.assert_called_once_with()
    publish.assert_called_once_with(recovery.EVIDENCE_RUN, 123, recovery.EVIDENCE_SOURCE)


def test_original_evidence_refuses_new_attempt_without_reading_jobs():
    run = {
        "id": recovery.EVIDENCE_RUN,
        "status": "completed",
        "conclusion": "failure",
        "event": "push",
        "run_attempt": 2,
    }
    with (
        patch.object(recovery.guard, "one", return_value=run),
        patch.object(recovery.guard, "api") as api,
        pytest.raises(ValueError, match="original evidence run identity drift"),
    ):
        recovery.original_evidence()
    api.assert_not_called()
