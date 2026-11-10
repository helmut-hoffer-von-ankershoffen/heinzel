"""Offline checks for the exact-head merge helper."""

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit
SPEC = importlib.util.spec_from_file_location("one_shot_692", Path(__file__).parents[2] / "scripts/one_shot_692.py")
assert SPEC
assert SPEC.loader
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


@pytest.fixture
def valid(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", helper.REPO)
    monkeypatch.setenv("GITHUB_REF", "refs/heads/bootstrap/one-shot-692")
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setenv("REVIEWED_HELPER_SHA", "a" * 40)
    pull = {
        "state": "open",
        "draft": False,
        "head": {"sha": helper.HEAD},
        "base": {"sha": helper.BASE, "ref": "main"},
        "mergeable": True,
        "mergeable_state": "clean",
    }
    checks = [
        {
            "name": "CI OK",
            "status": "completed",
            "conclusion": "success",
            "app": {"id": 15368},
            "pull_requests": [{"number": 692}],
            "id": 129674241526,
            "details_url": f"https://github.com/{helper.REPO}/actions/runs/{helper.CI_RUN}/job/129674241526",
        }
    ]
    statuses = [
        {
            "context": name,
            "state": "success",
            "target_url": f"https://github.com/{helper.REPO}/actions/runs/{helper.RECOVERY_RUN}",
        }
        for name in helper.GATES
    ]
    runs = {
        helper.CI_RUN: {
            "id": helper.CI_RUN,
            "head_sha": helper.HEAD,
            "event": "pull_request",
            "path": ".github/workflows/ci.yml",
            "run_attempt": 2,
            "status": "completed",
            "conclusion": "success",
        },
        helper.RECOVERY_RUN: {
            "id": helper.RECOVERY_RUN,
            "head_sha": helper.RECOVERY_SHA,
            "event": "push",
            "path": ".github/workflows/checks-status-recovery.yml",
            "run_attempt": 1,
            "status": "completed",
            "conclusion": "success",
        },
    }

    def api(path):
        if path == "pulls/692":
            return [pull]
        if path == "commits/main":
            return [{"sha": helper.BASE}]
        if path.startswith("actions/runs/"):
            return [runs[int(path.rsplit("/", 1)[-1])]]
        if "/status?" in path:
            return [{"statuses": statuses}]
        return [{"check_runs": checks}]

    monkeypatch.setattr(helper, "api", api)
    return pull, checks, statuses, runs


def test_accepts_only_expected_pull_and_checks(valid):
    helper.validate()


@pytest.mark.parametrize("mutation", ["draft", "head", "base", "pending", "failed", "app", "association", "missing"])
def test_refuses_stale_or_untrusted_evidence(valid, mutation):
    pull, checks, _, _ = valid
    if mutation == "draft":
        pull["draft"] = True
    elif mutation in {"head", "base"}:
        pull[mutation]["sha"] = "0" * 40
    elif mutation == "missing":
        checks.clear()
    elif mutation == "association":
        checks[0]["pull_requests"] = []
    elif mutation == "app":
        checks[0]["app"]["id"] = 1
    elif mutation == "pending":
        checks[0]["status"] = "in_progress"
    else:
        checks[0]["conclusion"] = "failure"
    with pytest.raises(ValueError, match=r"pull request|check"):
        helper.validate()


def test_mismatched_reviewed_workflow_sha_refuses_before_api(valid, monkeypatch):
    monkeypatch.setenv("REVIEWED_HELPER_SHA", "b" * 40)
    with (
        patch.object(helper, "api", side_effect=AssertionError("unexpected API call")),
        pytest.raises(ValueError, match="helper SHA"),
    ):
        helper.validate()


@pytest.mark.parametrize("mutation", ["status_failed", "status_producer", "run_source", "run_attempt"])
def test_refuses_bad_status_or_run_provenance(valid, mutation):
    _, _, statuses, runs = valid
    if mutation == "status_failed":
        statuses[0]["state"] = "failure"
    elif mutation == "status_producer":
        statuses[0]["target_url"] = "https://example.invalid/"
    elif mutation == "run_source":
        runs[helper.RECOVERY_RUN]["head_sha"] = "0" * 40
    else:
        runs[helper.CI_RUN]["run_attempt"] = 1
    with pytest.raises(ValueError, match=r"evidence run|recovery status"):
        helper.validate()
