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


@pytest.fixture
def evidence(monkeypatch):
    recovery_id = mod.RECOVERY_RUN_ID
    original = {
        "id": mod.RUN,
        "repository": {"id": mod.REPOSITORY_ID},
        "actor": {"login": "helmut-hoffer-von-ankershoffen"},
        "head_sha": mod.RUN_SHA,
        "head_branch": "bootstrap/one-shot-650",
        "event": "push",
        "path": ".github/workflows/focused-650.yml",
        "run_attempt": 1,
        "status": "completed",
        "conclusion": "failure",
    }
    recovery = {
        "id": recovery_id,
        "head_sha": mod.RECOVERY_SHA,
        "head_branch": "bootstrap/one-shot-650-publisher-recovery",
        "event": "push",
        "path": ".github/workflows/one-shot-650-publisher-recovery.yml",
        "run_attempt": 1,
        "status": "completed",
        "conclusion": "success",
        "repository": {"id": mod.REPOSITORY_ID},
        "actor": {"login": "helmut-hoffer-von-ankershoffen"},
    }
    original_jobs = [
        {
            "name": name,
            "status": "completed",
            "conclusion": "failure" if name == mod.PUBLISHER else "success",
            "run_id": mod.RUN,
            "run_attempt": 1,
            "head_sha": mod.RUN_SHA,
        }
        for name in mod.JOBS
    ]
    recovery_jobs = [
        {
            "name": "recover",
            "status": "completed",
            "conclusion": "success",
            "run_id": recovery_id,
            "run_attempt": 1,
            "head_sha": mod.RECOVERY_SHA,
        }
    ]
    rules = [
        {
            "type": "required_status_checks",
            "ruleset_id": mod.RULESET_ID,
            "parameters": {
                "strict_required_status_checks_policy": False,
                "required_status_checks": [
                    {"context": name, "integration_id": app} for name, app in mod.CONTEXTS.items()
                ],
            },
        }
    ]
    statuses = [
        {
            "context": name,
            "state": "success",
            "target_url": f"https://github.com/{mod.REPO}/actions/runs/{recovery_id}",
            "creator": {"id": mod.ACTIONS_BOT_ID},
        }
        for name in mod.CONTEXTS
    ]

    def api(path):
        if path == f"actions/runs/{mod.RUN}":
            return [original]
        if path == f"actions/runs/{recovery_id}":
            return [recovery]
        if path == f"actions/runs/{mod.RUN}/attempts/1/jobs?per_page=100":
            return [{"total_count": len(original_jobs), "jobs": original_jobs}]
        if path == f"actions/runs/{recovery_id}/attempts/1/jobs?per_page=100":
            return [{"total_count": len(recovery_jobs), "jobs": recovery_jobs}]
        if path == "rules/branches/main?per_page=100":
            return [rules]
        if path == f"commits/{mod.HEAD}/statuses?per_page=100":
            return [statuses]
        pytest.fail(f"unexpected API path {path}")

    monkeypatch.setattr(mod, "api", api)
    return recovery_id, original, recovery, original_jobs, recovery_jobs, statuses


def test_accepts_exact_original_and_recovery_evidence(evidence):
    mod.validate_evidence()


@pytest.mark.parametrize(
    "mutation",
    [
        "original_validation_failed",
        "original_publisher_success",
        "recovery_sha",
        "recovery_failed",
        "recovery_job",
        "status_source",
        "status_failure",
    ],
)
def test_refuses_changed_evidence(evidence, mutation):
    _, _, recovery, original_jobs, recovery_jobs, statuses = evidence
    if mutation == "original_validation_failed":
        next(job for job in original_jobs if job["name"] != mod.PUBLISHER)["conclusion"] = "failure"
    elif mutation == "original_publisher_success":
        next(job for job in original_jobs if job["name"] == mod.PUBLISHER)["conclusion"] = "success"
    elif mutation == "recovery_sha":
        recovery["head_sha"] = "0" * 40
    elif mutation == "recovery_failed":
        recovery["conclusion"] = "failure"
    elif mutation == "recovery_job":
        recovery_jobs[0]["conclusion"] = "failure"
    elif mutation == "status_source":
        statuses[0]["target_url"] = f"https://github.com/{mod.REPO}/actions/runs/{mod.RUN}"
    else:
        statuses[0]["state"] = "failure"
    with pytest.raises(ValueError, match=r"focused jobs|recovery run|recovery job|untrusted status"):
        mod.validate_evidence()


def test_validate_reads_test_merge_ref_without_pr_merge_sha(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", mod.REPO)
    monkeypatch.setenv("GITHUB_REF", "refs/heads/bootstrap/one-shot-650-merge")
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setenv("REVIEWED_HELPER_SHA", "a" * 40)
    objects = {
        "pulls/650": {
            "state": "open",
            "draft": False,
            "head": {"sha": mod.HEAD},
            "base": {"sha": mod.BASE, "ref": "main"},
            "mergeable": True,
            "mergeable_state": "clean",
        },
        "commits/main": {"sha": mod.BASE},
        f"git/commits/{mod.HEAD}": {"tree": {"sha": mod.TREE}},
        "git/ref/pull/650/merge": {"object": {"sha": "merge-oid"}},
        "git/commits/merge-oid": {"tree": {"sha": mod.TREE}, "parents": [{"sha": mod.BASE}, {"sha": mod.HEAD}]},
    }
    monkeypatch.setattr(mod, "one", lambda path: objects[path])
    validated = []
    monkeypatch.setattr(mod, "validate_evidence", lambda: validated.append(mod.RECOVERY_RUN_ID))
    mod.validate()
    assert validated == [42787744206]
    objects["git/commits/merge-oid"]["parents"].reverse()
    with pytest.raises(ValueError, match="test merge changed"):
        mod.validate()
