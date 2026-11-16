"""Adversarial local fixtures for the distinct full-PR capacity proof."""

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def fg(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    from scripts import focused_gate

    return focused_gate


@pytest.mark.parametrize(
    "defect",
    [None, *json.loads((Path(__file__).resolve().parents[1] / "fixtures/pr-capacity/adversarial.json").read_text())],
)
def test_capacity_proof_refuses_unproven_recovery(fg, monkeypatch, defect):  # noqa: C901, PLR0912, PLR0915 - adversarial matrix
    head, base, merge, tree = (c * 40 for c in "abcd")
    run = {
        "id": 123,
        "run_attempt": 3 if defect == "attempt-three" else 2,
        "event": "pull_request",
        "check_suite_id": 99,
    }
    snapshot = ({"number": 1}, merge, base)
    source = SimpleNamespace(run=run, source=SimpleNamespace(head=head), commit=merge)
    name = "make setup (macos-latest)"
    inventory = {name: "success", "CI OK": "success", "Changed paths": "success", "Coverage": "success"}

    def step(name, conclusion="success"):
        return {"name": name, "status": "completed", "conclusion": conclusion}

    def job(number, name, conclusion="success"):
        return {
            "id": number,
            "name": name,
            "run_id": 123,
            "run_attempt": 1,
            "head_sha": head,
            "status": "completed",
            "conclusion": conclusion,
            "runner_id": 8,
            "runner_name": "runner",
            "started_at": "start",
            "completed_at": "end",
            "steps": [step("work")],
        }

    first = [job(i, n) for i, n in enumerate(inventory, 1)]
    first[0].update(
        conclusion="cancelled",
        runner_id=0,
        runner_name="",
        steps=[],
        labels=["macos-latest"],
        check_run_url=f"{fg.GITHUB}/repos/{fg.REPOSITORY}/check-runs/1",
    )
    first[1].update(conclusion="failure", steps=[step("Fail if any required job failed", "failure")])
    first.append(job(5, "Release candidate source", "skipped") | {"steps": []})
    second = deepcopy(first)
    for row in second:
        row["id"] += 100
        row["run_attempt"] = 2
    second[0].update(
        conclusion="success", runner_id=9, runner_name="other", steps=[step(x) for x in fg._RECOVERY_STEPS[name]]
    )
    second[1].update(conclusion="success", steps=[step(x) for x in fg._RECOVERY_STEPS["CI OK"]])

    def annotation(level, message):
        return {
            "annotation_level": level,
            "message": message,
            "path": ".github",
            "blob_href": f"https://github.com/{fg.REPOSITORY}/blob/{head}/.github",
        }

    annotations = [annotation("failure", fg.CAPACITY_NOT_ACQUIRED), annotation("notice", fg.CAPACITY_NOTICE)]

    def check(row):
        return {
            "id": row["id"],
            "name": row["name"],
            "head_sha": head,
            "status": "completed",
            "conclusion": row["conclusion"],
            "app": {"id": fg.ACTIONS_APP_ID},
            "check_suite": {"id": 99},
        }

    checks = {f"check-runs/{row['id']}": check(row) for row in first + second}
    if defect == "original-workload-failure":
        first[2]["conclusion"] = "failure"
    if defect == "assigned-runner":
        first[0]["runner_id"] = 1
    if defect == "executed-step":
        first[0]["steps"] = [step("work")]
    if defect == "missing-annotation":
        annotations.pop()
    if defect == "unknown-annotation":
        annotations.append(annotation("notice", "unknown"))
    if defect == "wrong-check-suite":
        checks["check-runs/1"]["check_suite"]["id"] = 98
    if defect == "wrong-head":
        checks["check-runs/1"]["head_sha"] = "f" * 40
    if defect == "duplicate-job":
        second.append(deepcopy(second[0]))
    if defect == "missing-job":
        second.pop()
    if defect == "coverage-failure":
        first[3]["conclusion"] = "failure"
    if defect == "recovered-step-skipped":
        second[0]["steps"][0]["conclusion"] = "skipped"
    if defect == "changed-original-carrier":
        second[2]["runner_id"] = 13
    artifacts = {"total_count": 1, "artifacts": [{"name": fg.ARTIFACT, "workflow_run": {"id": 123}}]}
    if defect == "coverage-artifact-mismatch":
        artifacts["artifacts"][0]["workflow_run"]["id"] = 124
    rows = checks | {
        "check-runs/1/annotations?per_page=100&page=1": annotations,
        "actions/runs/123/artifacts?name=coverage-total": artifacts,
        f"actions/workflows/{fg.CI_WORKFLOW_ID}/runs?head_sha={head}&event=pull_request&per_page=2": {
            "total_count": 2 if defect == "extra-run" else 1,
            "workflow_runs": [{"id": 123}],
        },
    }
    gate = SimpleNamespace(head=head, github=rows.__getitem__)
    snapshots = iter([snapshot, (snapshot[0], snapshot[1], "f" * 40)]) if defect == "wrong-base" else None
    monkeypatch.setattr(fg, "pull_identity", lambda *_: next(snapshots) if snapshots else snapshot)
    monkeypatch.setattr(fg, "release_version", lambda *_: "0.9.99" if defect == "candidate" else None)
    monkeypatch.setattr(fg, "_authenticated_run", lambda *_args, **_kwargs: run)
    monkeypatch.setattr(fg, "_pr_source", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(fg, "_full_pr_source", lambda *_: None)
    monkeypatch.setattr(fg, "jobs_for", lambda *_: first)
    monkeypatch.setattr(fg, "_recovery_inventory", lambda *_: second)
    monkeypatch.setattr(fg, "quarantine_lane", lambda jobs, *_: jobs)
    monkeypatch.setattr(fg, "expected_jobs", lambda *_: inventory.copy())
    monkeypatch.setattr(fg, "full_checkout", lambda _: "e" * 40 if defect == "changed-checkout" else merge)
    monkeypatch.setattr(fg, "_verified_archive", lambda *_: b"100")
    monkeypatch.setattr(fg, "whole_total", lambda _: 100)
    monkeypatch.setattr(fg, "red_below", lambda _: 90)
    monkeypatch.setattr(fg, "git", lambda *_: b"invalid" if defect == "wrong-tree" else tree.encode())
    if defect:
        with pytest.raises(fg.CandidateError):
            fg.verify_full_pr_capacity(gate, 123, lambda _: b"log")
    else:
        assert fg.verify_full_pr_capacity(gate, 123, lambda _: b"log") == {
            "type": "full-pr-capacity/v1",
            "head": head,
            "base": base,
            "tree": tree,
            "run": 123,
            "attempt": 2,
            "original_attempt": 1,
        }
