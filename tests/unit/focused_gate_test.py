"""The protected judge binds coverage evidence to an immutable original upload."""

import hashlib
import io
import zipfile
from pathlib import Path
from subprocess import CalledProcessError

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def focused_gate(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    from scripts import focused_gate

    return focused_gate


def _archive(*names):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name in names:
            archive.writestr(name, "{}")
    return output.getvalue()


@pytest.mark.parametrize(
    "defect", ["none", "wrong-id", "wrong-hash", "expired", "skipped", "wrong-name", "extra-entry"]
)
def test_focused_artifact_is_bound_to_its_successful_upload_and_digest(focused_gate, defect):
    run, job_id, artifact_id = 123, 456, 789
    name = f"focused-pr-evidence-python-{run}-1"
    steps = [{"name": focused_gate.UPLOAD_NAME, "number": 6, "status": "completed", "conclusion": "success"}]
    job = {"id": job_id, "steps": steps}
    entries = ["evidence.json", "junit.xml", "coverage.json"]
    if defect == "extra-entry":
        entries.append("coverage-total.txt")
    content = _archive(*entries)
    artifact = {
        "id": artifact_id,
        "size_in_bytes": len(content),
        "digest": f"sha256:{hashlib.sha256(content).hexdigest()}",
        "expired": defect == "expired",
    }
    if defect == "skipped":
        steps[0]["conclusion"] = "skipped"
    if defect == "wrong-hash":
        artifact["digest"] = "sha256:" + "0" * 64
    observed_id = artifact_id + 1 if defect == "wrong-id" else artifact_id
    observed_name = f"{name}-other" if defect == "wrong-name" else name
    log = (
        f"2026-11-10T08:00:00.000Z Artifact {observed_name} has been successfully uploaded! "
        f"Final size is {len(content)} bytes. Artifact ID is {observed_id}\n"
    ).encode()

    def read(path):
        return {
            f"actions/jobs/{job_id}/steps/6/logs": log,
            f"actions/artifacts/{artifact_id}/zip": content,
        }[path]

    if defect == "none":
        assert set(focused_gate._artifact(read, job, artifact, name)) == set(entries)
    else:
        with pytest.raises(focused_gate.CandidateError, match=r".+"):
            focused_gate._artifact(read, job, artifact, name)


def test_candidate_composite_still_binds_original_attempt_without_allowing_a_focused_rerun(focused_gate, monkeypatch):
    from scripts import merge_gate
    from scripts.release_candidate import REPOSITORY, WORKFLOW

    head = "a" * 40
    live = {
        "id": 123,
        "run_attempt": 2,
        "head_sha": head,
        "workflow_id": focused_gate.CI_WORKFLOW_ID,
        "path": WORKFLOW,
        "repository": {"full_name": REPOSITORY},
        "status": "completed",
        "conclusion": "success",
        "event": "pull_request",
        "check_suite_id": 333,
    }
    original = live | {"run_attempt": 1, "conclusion": "failure"}

    def github(_gate, path):
        if path == "actions/runs/123":
            return live
        if path == "actions/runs/123/attempts/1":
            return original
        return {"workflow_runs": [live]}

    monkeypatch.setattr(merge_gate.Gate, "github", github)
    gate = merge_gate.Gate(REPOSITORY, head=head)
    with pytest.raises(focused_gate.CandidateError, match="successful original attempt"):
        focused_gate._authenticated_run(gate, 123)
    assert focused_gate._authenticated_run(gate, 123, candidate_composite=True) == live
    original["head_sha"] = "b" * 40
    with pytest.raises(focused_gate.CandidateError, match="canonical CI workflow"):
        focused_gate._authenticated_run(gate, 123, candidate_composite=True)


@pytest.fixture
def first_attempt(focused_gate, monkeypatch):
    """A complete successful PR attempt with independent Git and GitHub observations."""
    import json

    from scripts import focused_coverage, focused_plan, merge_gate
    from scripts.ci_workload import WorkloadSource
    from scripts.release_candidate import REPOSITORY, WORKFLOW

    base, head, merge, tree, blob = (digit * 40 for digit in "abcde")
    ref, number, run_id, suite_id = "refs/pull/5/merge", 5, 123, 234
    config = (Path(__file__).resolve().parents[2] / "pyproject.toml").read_bytes()
    test_file = "tests/unit/focused_gate_test.py"
    diff = f":100644 100644 {head} {blob} M\0{test_file}\0".encode()
    junit = b'<testsuite><testcase classname="test_unit" name="test_case"/></testsuite>'
    pr = {
        "number": number,
        "state": "open",
        "draft": False,
        "title": "test: fix focused fixture",
        "base": {"sha": base, "ref": "main", "repo": {"id": 345, "full_name": REPOSITORY}},
        "head": {"sha": head, "ref": "test/focused", "repo": {"id": 345, "full_name": REPOSITORY, "fork": False}},
    }
    run = {
        "id": run_id,
        "run_attempt": 1,
        "head_sha": head,
        "head_branch": pr["head"]["ref"],
        "head_repository": {"id": 345},
        "repository": {"full_name": REPOSITORY},
        "workflow_id": focused_gate.CI_WORKFLOW_ID,
        "path": WORKFLOW,
        "event": "pull_request",
        "status": "completed",
        "conclusion": "success",
        "check_suite_id": suite_id,
        "pull_requests": [{"number": number, "base": dict(pr["base"]), "head": dict(pr["head"])}],
    }
    git_rows = {
        ("ls-remote", "--exit-code", "origin", ref): f"{merge} {ref}".encode(),
        ("fetch", "--no-tags", "origin", ref): b"",
        ("rev-parse", "FETCH_HEAD"): merge.encode(),
        ("rev-list", "--parents", "-n", "1", merge): f"{merge} {base} {head}".encode(),
        ("diff-tree", "--no-commit-id", "-r", "--raw", "--no-abbrev", "--no-renames", "-z", base, merge): diff,
        ("show", f"{base}:pyproject.toml"): config,
        ("cat-file", "blob", blob): config,
    }

    def git(*args):
        row = git_rows[args]
        if isinstance(row, CalledProcessError):
            raise row
        return row

    for module in (focused_plan, focused_gate):
        monkeypatch.setattr(module, "git", git)
        monkeypatch.setattr(module, "git_blob", lambda *_args: blob)
    monkeypatch.setattr(focused_plan, "object_id", lambda _ref: tree)
    plan = {
        "schema": 1,
        "event": "pull_request",
        "ref": focused_plan.PREOPEN_PR_REF,
        "base": base,
        "head": head,
        "tree": tree,
        "changed": {test_file: "M"},
        "diff_sha256": hashlib.sha256(diff).hexdigest(),
        "config_blob": blob,
        "execution": dict.fromkeys(focused_plan.PROTECTED_FILES, blob),
        "production": {},
        "mode": "python-coverage-not-applicable",
        "selection": {"python": [test_file]},
        "case_sha256": {"python": focused_coverage.cases_digest(junit)},
    }
    manifest = json.dumps({f"pull_request:{head}": plan})
    source = WorkloadSource("pull_request", REPOSITORY, ref, {"pull_request": pr}, base, head, diff)
    scope = focused_plan.resolve(source, merge, manifest)
    report = focused_coverage.canonical(focused_coverage.evidence(scope, "python", run_id, 1, {"junit.xml": junit}))
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("evidence.json", report)
        archive.writestr("junit.xml", junit)
    content = output.getvalue()
    artifact = {
        "id": 456,
        "name": f"focused-pr-evidence-python-{run_id}-1",
        "expired": False,
        "size_in_bytes": len(content),
        "digest": f"sha256:{hashlib.sha256(content).hexdigest()}",
        "workflow_run": {"id": run_id},
    }
    jobs = [
        {
            "id": index,
            "name": name,
            "run_id": run_id,
            "run_attempt": 1,
            "head_sha": head,
            "status": "completed",
            "conclusion": "success",
            "runner_id": index,
            "steps": [{"name": focused_gate.UPLOAD_NAME, "number": 6, "status": "completed", "conclusion": "success"}],
        }
        for index, name in enumerate(
            sorted(focused_gate.STATIC_JOBS | focused_gate.PR_JOBS | {"Focused tests (python)"}), 1
        )
    ]
    lane = next(job for job in jobs if job["name"] == "Focused tests (python)")
    suite = {
        "id": suite_id,
        "head_sha": head,
        "status": "completed",
        "conclusion": "success",
        "app": {"id": focused_gate.ACTIONS_APP_ID},
    }
    checks = {
        job["id"]: {
            **job,
            "app": {"id": focused_gate.ACTIONS_APP_ID},
            "check_suite": {"id": suite_id},
        }
        for job in jobs
    }
    rows = {
        f"pulls/{number}": pr,
        "git/ref/heads/main": {"object": {"sha": base}},
        f"actions/runs/{run_id}": run,
        f"actions/runs/{run_id}/attempts/1": run,
        f"actions/workflows/{focused_gate.CI_WORKFLOW_ID}/runs?head_sha={head}&event=pull_request&per_page=1": {
            "workflow_runs": [run],
        },
        f"check-suites/{suite_id}": suite,
        f"actions/runs/{run_id}/attempts/1/jobs?per_page=100&page=1": {"total_count": len(jobs), "jobs": jobs},
        f"actions/runs/{run_id}/artifacts?per_page=100": {"total_count": 1, "artifacts": [artifact]},
        **{f"check-runs/{check_id}": check for check_id, check in checks.items()},
    }
    logs = {
        f"actions/jobs/{lane['id']}/steps/6/logs": (
            f"Artifact {artifact['name']} has been successfully uploaded! "
            f"Final size is {len(content)} bytes. Artifact ID is {artifact['id']}\n"
        ).encode(),
        f"actions/artifacts/{artifact['id']}/zip": content,
    }
    return merge_gate.Gate(REPOSITORY, pr=str(number), head=head), run_id, manifest, lane, rows, logs, git_rows


def _appended_checks(repo, run, lane, defect):
    rows = [
        {
            "id": check_id,
            "name": name,
            "run_id": run,
            "html_url": f"https://github.com/{repo}/runs/{check_id}",
            "runner_id": None,
            "runner_name": None,
            "steps": [],
            "labels": [],
            "status": "in_progress",
        }
        for check_id, name in ((801, "merge-gate"), (802, "coverage-floor"))
    ]
    if defect == "lookalike-real-job":
        rows[1] = lane | {"id": 802, "name": "coverage-floor"}
    elif defect == "duplicate-api-row":
        rows.append(rows[1])
    return rows


@pytest.mark.parametrize(
    ("defect", "error"),
    [
        ("none", ""),
        ("pending-api-checks", ""),
        ("lookalike-real-job", "workload job inventory"),
        ("duplicate-api-row", "duplicate candidate job IDs"),
        ("wrong-job-attempt", "job identity or attempt"),
        ("wrong-job-app", "authentic successful execution"),
        ("missing-lane", "workload job inventory"),
        ("missing-artifact", "artifact lane inventory"),
    ],
)
def test_protected_reader_verifies_complete_attempt(focused_gate, first_attempt, monkeypatch, defect, error):
    gate, run, manifest, lane, rows, logs, _git_rows = first_attempt
    jobs_path = f"actions/runs/{run}/attempts/1/jobs?per_page=100&page=1"
    artifacts_path = f"actions/runs/{run}/artifacts?per_page=100"
    if defect in {"pending-api-checks", "lookalike-real-job", "duplicate-api-row"}:
        suite_id = rows[f"actions/runs/{run}"]["check_suite_id"]
        rows[f"check-suites/{suite_id}"].update(status="in_progress", conclusion=None)
        rows[jobs_path]["jobs"].extend(_appended_checks(gate.repo, run, lane, defect))
        rows[jobs_path]["total_count"] = len(rows[jobs_path]["jobs"])
    elif defect == "wrong-job-attempt":
        lane["run_attempt"] = 2
    elif defect == "wrong-job-app":
        rows[f"check-runs/{lane['id']}"]["app"] = {"id": 1}
    elif defect == "missing-lane":
        rows[jobs_path]["jobs"].remove(lane)
        rows[jobs_path]["total_count"] -= 1
    elif defect == "missing-artifact":
        rows[artifacts_path] = {"total_count": 0, "artifacts": []}

    monkeypatch.setattr(type(gate), "github", lambda _gate, path: rows[path])
    if error:
        with pytest.raises(focused_gate.CandidateError, match=error):
            focused_gate.verify(gate, run, manifest, logs.__getitem__)
    else:
        assert focused_gate.verify(gate, run, manifest, logs.__getitem__) == (
            "Python coverage: not applicable to the reviewed scope"
        )


@pytest.mark.parametrize(
    ("defect", "error"),
    [
        ("cached-bases", ""),
        ("wrong-pr-base", "outside the tested main history"),
        ("wrong-run-base", "outside the tested main history"),
        ("wrong-run-target", "does not target protected main"),
        ("wrong-run-repo", "does not target protected main"),
        ("wrong-run-pr", "does not bind the exact PR and head"),
        ("wrong-run-head", "does not bind the exact PR and head"),
        ("old-merge-base", "test merge base differs from reviewed base"),
    ],
)
def test_protected_reader_binds_merge_and_run_to_current_main(focused_gate, first_attempt, monkeypatch, defect, error):
    gate, run, manifest, _lane, rows, logs, git_rows = first_attempt
    pr_path = f"pulls/{gate.pr}"
    base = rows["git/ref/heads/main"]["object"]["sha"]
    association = rows[f"actions/runs/{run}"]["pull_requests"][0]
    old_base, another_base = "f" * 40, "1" * 40
    ancestry = ("merge-base", "--is-ancestor", old_base, base)
    if defect == "cached-bases":
        rows[pr_path]["base"]["sha"] = old_base
        association["base"]["sha"] = another_base
        git_rows[ancestry] = b""
        git_rows[("merge-base", "--is-ancestor", another_base, base)] = b""
    elif defect == "wrong-pr-base":
        rows[pr_path]["base"]["sha"] = old_base
        git_rows[ancestry] = CalledProcessError(1, ancestry)
    elif defect == "wrong-run-base":
        association["base"]["sha"] = old_base
        git_rows[ancestry] = CalledProcessError(1, ancestry)
    elif defect == "wrong-run-target":
        association["base"]["ref"] = "other"
    elif defect == "wrong-run-repo":
        association["base"]["repo"] = {"id": 777}
    elif defect == "wrong-run-pr":
        association["number"] += 1
    elif defect == "wrong-run-head":
        association["head"]["sha"] = old_base
    elif defect == "old-merge-base":
        merge = git_rows[("rev-parse", "FETCH_HEAD")].decode()
        git_rows[("rev-list", "--parents", "-n", "1", merge)] = f"{merge} {old_base} {gate.head}".encode()

    monkeypatch.setattr(type(gate), "github", lambda _gate, path: rows[path])
    if error:
        with pytest.raises(focused_gate.CandidateError, match=error):
            focused_gate.verify(gate, run, manifest, logs.__getitem__)
    else:
        assert focused_gate.verify(gate, run, manifest, logs.__getitem__) == (
            "Python coverage: not applicable to the reviewed scope"
        )


@pytest.mark.parametrize("metadata", ["pr", "run"])
def test_release_reader_still_requires_exact_base(focused_gate, first_attempt, monkeypatch, metadata):
    gate, run_id, manifest, _lane, rows, logs, git_rows = first_attempt
    pr = rows[f"pulls/{gate.pr}"]
    run = rows[f"actions/runs/{run_id}"]
    pr["title"] = "chore(release): v0.9.34"
    pr["head"]["ref"] = run["head_branch"] = "release/v0.9.34"
    reported = pr if metadata == "pr" else run["pull_requests"][0]
    stale, current = "f" * 40, pr["base"]["sha"]
    reported["base"]["sha"] = stale
    git_rows[("merge-base", "--is-ancestor", stale, current)] = b""
    monkeypatch.setattr(type(gate), "github", lambda _gate, path: rows[path])
    with pytest.raises(focused_gate.CandidateError, match=r"release .*base"):
        focused_gate.verify(gate, run_id, manifest, logs.__getitem__)


@pytest.mark.parametrize(
    ("defect", "error"),
    [
        ("moved-main", "source or workflow attempt changed during verification"),
        ("moved-merge", "source or workflow attempt changed during verification"),
        ("superseded", "a newer workflow run superseded this evidence"),
        ("changed-head", "PR or protected base changed"),
    ],
)
def test_protected_reader_rechecks_live_source_and_attempt(focused_gate, first_attempt, monkeypatch, defect, error):
    gate, run, manifest, _lane, rows, logs, git_rows = first_attempt
    pr_path = f"pulls/{gate.pr}"
    main_path = "git/ref/heads/main"
    merge_ref = f"refs/pull/{gate.pr}/merge"
    another_sha = "f" * 40
    reads = {}

    def github(_gate, path):
        reads[path] = reads.get(path, 0) + 1
        row = rows[path]
        if reads[path] == 2:
            if path == pr_path and defect == "changed-head":
                return row | {"head": row["head"] | {"sha": another_sha}}
            if path == pr_path and defect == "moved-merge":
                git_rows[("ls-remote", "--exit-code", "origin", merge_ref)] = f"{another_sha} {merge_ref}".encode()
            if path == main_path and defect == "moved-main":
                return {"object": {"sha": another_sha}}
            if path.startswith("actions/workflows/") and defect == "superseded":
                return {"workflow_runs": [{"id": run + 1}]}
        return row

    monkeypatch.setattr(type(gate), "github", github)
    with pytest.raises(focused_gate.CandidateError, match=error):
        focused_gate.verify(gate, run, manifest, logs.__getitem__)
