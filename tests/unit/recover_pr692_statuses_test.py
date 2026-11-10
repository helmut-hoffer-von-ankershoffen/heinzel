"""Qualify PR #692 recovery against offline incident projections and the pinned judge."""

import hashlib
import io
import zipfile
from copy import deepcopy
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
RECOVERY_URL = "https://github.com/helmut-hoffer-von-ankershoffen/heinzel/actions/runs/100"


@pytest.fixture
def recovery(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    from scripts import recover_pr692_statuses

    return recover_pr692_statuses


@pytest.fixture
def responses(recovery):
    r = recovery
    repo = {"full_name": r.REPO}
    association = {
        "number": r.PR,
        "head": {"sha": r.HEAD, "ref": r.HEAD_REF, "repo": repo},
        "base": {"sha": r.BASE, "ref": "main", "repo": repo},
    }
    run = {
        "id": r.RUN,
        "run_attempt": 2,
        "event": "pull_request",
        "path": r.CI,
        "head_sha": r.HEAD,
        "head_branch": r.HEAD_REF,
        "display_title": f"CI {r.MERGE}",
        "check_suite_id": r.SUITE,
        "status": "completed",
        "conclusion": "success",
        "repository": repo,
        "head_repository": repo,
        "pull_requests": [association],
    }

    def jobs(name, job_id, attempt):
        return {
            "total_count": 1,
            "jobs": [
                {
                    "name": name,
                    "id": job_id,
                    "run_id": r.RUN,
                    "run_attempt": attempt,
                    "head_sha": r.HEAD,
                    "status": "completed",
                    "conclusion": "success",
                }
            ],
        }

    return {
        "pulls/692": {
            **deepcopy(association),
            "state": "open",
            "draft": False,
            "mergeable": True,
            "mergeable_state": "blocked",
            "merge_commit_sha": r.MERGE,
        },
        "git/ref/heads/main": {"ref": "refs/heads/main", "object": {"sha": r.BASE, "type": "commit"}},
        f"git/ref/heads/{r.HEAD_REF}": {
            "ref": f"refs/heads/{r.HEAD_REF}",
            "object": {"sha": r.HEAD, "type": "commit"},
        },
        f"git/commits/{r.HEAD}": {"sha": r.HEAD, "tree": {"sha": r.TREE}},
        f"git/commits/{r.MERGE}": {
            "sha": r.MERGE,
            "tree": {"sha": r.TREE},
            "parents": [{"sha": r.BASE}, {"sha": r.HEAD}],
        },
        "rules/branches/main": [
            {
                "type": "required_status_checks",
                "ruleset_id": r.RULESET,
                "ruleset_source": r.REPO,
                "parameters": {
                    "required_status_checks": [
                        {"context": "CI OK"},
                        {"context": "merge-gate", "integration_id": 15368},
                        {"context": "coverage-floor", "integration_id": 15368},
                    ],
                    "strict_required_status_checks_policy": False,
                },
            }
        ],
        f"actions/runs/{r.RUN}": run,
        f"actions/runs/{r.RUN}/attempts/2": deepcopy(run),
        f"actions/runs/{r.RUN}/attempts/2/jobs?per_page=100": jobs("CI OK", r.CI_OK_JOB, 2),
        f"actions/runs/{r.RUN}/attempts/1/jobs?per_page=100": jobs("Coverage", r.COVERAGE_JOB, 1),
        f"actions/artifacts/{r.ARTIFACT}": {
            "id": r.ARTIFACT,
            "name": "coverage-total",
            "expired": False,
            "size_in_bytes": r.ARTIFACT_SIZE,
            "digest": f"sha256:{r.ARTIFACT_DIGEST}",
            "workflow_run": {"id": r.RUN, "head_sha": r.HEAD, "head_branch": r.HEAD_REF},
        },
    }


def states(writes):
    return [(entry["context"], entry["state"]) for entry in writes]


def test_posts_pending_and_actual_outcomes_only_for_the_two_gates(recovery, responses, caplog):
    writes = []
    caplog.set_level("INFO")

    def coverage(job):
        assert job["id"] == 129668190311
        assert job["run_attempt"] == 1
        return ["coverage below floor"]

    assert not recovery.recover(responses.__getitem__, writes.append, list, coverage, RECOVERY_URL)
    assert states(writes) == [
        ("merge-gate", "pending"),
        ("coverage-floor", "pending"),
        ("merge-gate", "success"),
        ("coverage-floor", "failure"),
    ]
    assert {entry["target_url"] for entry in writes} == {RECOVERY_URL}
    assert caplog.records[0].message == "ci.pr692_recovery.start"
    assert caplog.records[-1].outcome == "failed_gates"


def test_unavailable_gate_posts_error_and_keeps_other_genuine_outcome(recovery, responses):
    writes = []

    def unavailable():
        message = "Sonar unavailable"
        raise OSError(message)

    assert not recovery.recover(responses.__getitem__, writes.append, unavailable, lambda _: [], RECOVERY_URL)
    assert states(writes)[-2:] == [("merge-gate", "error"), ("coverage-floor", "success")]


@pytest.mark.parametrize("drift", ["base", "tree", "suite", "attempt", "rules", "artifact", "inventory", "coverage"])
def test_rejects_invalid_evidence_before_any_post(recovery, responses, drift):
    r = recovery
    original_jobs = responses[f"actions/runs/{r.RUN}/attempts/1/jobs?per_page=100"]
    changes = {
        "base": (responses["git/ref/heads/main"]["object"], "sha", "f" * 40),
        "tree": (responses[f"git/commits/{r.MERGE}"]["tree"], "sha", "f" * 40),
        "suite": (responses[f"actions/runs/{r.RUN}/attempts/2"], "check_suite_id", 115622128070),
        "attempt": (responses[f"actions/runs/{r.RUN}"], "run_attempt", 3),
        "rules": (responses["rules/branches/main"][0]["parameters"]["required_status_checks"][1], "integration_id", 12),
        "artifact": (responses[f"actions/artifacts/{r.ARTIFACT}"], "expired", True),
        "inventory": (original_jobs, "total_count", 2),
        "coverage": (original_jobs["jobs"][0], "id", 129673417162),
    }
    record, key, value = changes[drift]
    record[key] = value
    writes = []
    with pytest.raises(r.RecoveryError):
        r.recover(responses.__getitem__, writes.append, list, lambda _: [], RECOVERY_URL)
    assert writes == []


def test_duplicate_coverage_job_is_ambiguous_even_when_page_count_matches(recovery, responses):
    path = f"actions/runs/{recovery.RUN}/attempts/1/jobs?per_page=100"
    responses[path]["jobs"].append(deepcopy(responses[path]["jobs"][0]))
    responses[path]["total_count"] = 2
    with pytest.raises(recovery.RecoveryError, match="ambiguous Coverage"):
        recovery.recover(responses.__getitem__, pytest.fail, list, lambda _: [], RECOVERY_URL)


def test_movement_after_final_success_invalidates_both_statuses(recovery, responses):
    writes = []

    def post(entry):
        writes.append(entry)
        if entry["context"] == "coverage-floor" and entry["state"] == "success":
            responses["git/ref/heads/main"]["object"]["sha"] = "f" * 40

    with pytest.raises(recovery.RecoveryError, match="ref moved"):
        recovery.recover(responses.__getitem__, post, list, lambda _: [], RECOVERY_URL)
    assert states(writes)[-2:] == [("merge-gate", "error"), ("coverage-floor", "error")]


def test_transport_failure_after_first_success_invalidates_both_statuses(recovery, responses):
    writes = []

    def post(entry):
        writes.append(entry)
        if entry["context"] == "coverage-floor" and entry["state"] == "success":
            message = "response lost"
            raise OSError(message)

    with pytest.raises(OSError, match="response lost"):
        recovery.recover(responses.__getitem__, post, list, lambda _: [], RECOVERY_URL)
    assert states(writes)[-2:] == [("merge-gate", "error"), ("coverage-floor", "error")]


@pytest.mark.parametrize("percent", ["79.99", "80.00"])
def test_coverage_uses_pinned_judgment_and_exact_archive(recovery, monkeypatch, percent):
    gate = recovery._load_gate(ROOT)
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("coverage-total.txt", percent)
    payload = content.getvalue()
    monkeypatch.setattr(recovery, "ARTIFACT_SIZE", len(payload))
    monkeypatch.setattr(recovery, "ARTIFACT_DIGEST", hashlib.sha256(payload).hexdigest())

    def fetch(url, _headers):
        assert url == recovery.ARTIFACT_URL
        return payload

    def read_queue(url, headers=None):
        assert url == recovery.PUBLIC_QUEUE_URL
        assert headers is None
        return {"check_runs": [{"status": "completed"}]}

    monkeypatch.setattr(gate, "fetch", fetch)
    monkeypatch.setattr(gate, "fetch_json", read_queue)
    floor = gate.red_below((ROOT / recovery.CI).read_text())
    problems = recovery._coverage(gate, "fixture-token", floor, {"status": "completed", "conclusion": "success"})
    assert bool(problems) is (float(percent) < floor)

    monkeypatch.setattr(recovery, "ARTIFACT_DIGEST", "0" * 64)
    with pytest.raises(recovery.RecoveryError, match="archive size or digest"):
        recovery._coverage(gate, "fixture-token", floor, {})


def test_host_refuses_unapproved_push_and_reruns(recovery, monkeypatch):
    commit = "f" * 40
    for key, value in {
        "RECOVERY_SHA": commit,
        "GITHUB_SHA": commit,
        "GITHUB_WORKFLOW_SHA": commit,
        "GITHUB_ACTIONS": "true",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "GITHUB_REPOSITORY": recovery.REPO,
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": recovery.REF,
        "GITHUB_WORKFLOW_REF": f"{recovery.REPO}/{recovery.WORKFLOW}@{recovery.REF}",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_ACTOR": recovery.REPO.partition("/")[0],
    }.items():
        monkeypatch.setenv(key, value)
    recovery._assert_host()
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    with pytest.raises(recovery.RecoveryError, match="one-shot"):
        recovery._assert_host()
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("RECOVERY_SHA", "")
    with pytest.raises(recovery.RecoveryError, match="one-shot"):
        recovery._assert_host()
