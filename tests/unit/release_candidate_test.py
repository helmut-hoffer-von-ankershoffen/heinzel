"""Full release candidates require real exact-source first-attempt CI, never inferred aliases or shortened matrices."""

import base64
import copy
import hashlib
import importlib.util
import io
import json
import os
import shlex
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("release_candidate", ROOT / "scripts/release_candidate.py")
assert SPEC
assert SPEC.loader
candidate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = candidate
SPEC.loader.exec_module(candidate)
HEAD, BASE, MERGE, TREE = (character * 40 for character in "abcd")
RUN, PR = 123, 45
PINS = {candidate.WORKFLOW: "e" * 64, candidate.RECORDER: "f" * 64}


def pr():
    return {
        "number": PR,
        "state": "open",
        "draft": False,
        "title": "chore(release): v0.9.18",
        "head": {"ref": "release/v0.9.18", "sha": HEAD, "repo": {"full_name": candidate.REPOSITORY}},
        "base": {"ref": "main", "sha": BASE, "repo": {"full_name": candidate.REPOSITORY}},
    }


def evidence(event="pull_request"):
    source = MERGE if event == "pull_request" else HEAD
    run = {
        "id": RUN,
        "run_attempt": 1,
        "repository": {"full_name": candidate.REPOSITORY},
        "head_repository": {"full_name": candidate.REPOSITORY},
        "path": candidate.WORKFLOW,
        "status": "completed",
        "conclusion": "success",
        "event": event,
        "head_sha": HEAD,
        "head_branch": "release/v0.9.18",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:15:00Z",
    }
    proof = {
        "schema": 1,
        "repository": candidate.REPOSITORY,
        "event": event,
        "run_id": RUN,
        "run_attempt": 1,
        "head": HEAD,
        "base": BASE,
        "source_commit": source,
        "tree": TREE,
        "version": "0.9.18",
        "pr": PR if event == "pull_request" else None,
        "sources": PINS,
    }
    jobs = [
        {
            "id": index,
            "name": name,
            "run_id": RUN,
            "run_attempt": 1,
            "head_sha": HEAD,
            "status": "completed",
            "conclusion": conclusion,
            "runner_id": index if conclusion == "success" else 0,
            "steps": [{"conclusion": "success"}],
        }
        for index, (name, conclusion) in enumerate(sorted(candidate.expected_jobs(event).items()), 1)
    ]
    return {
        "run": run,
        "proof": proof,
        "jobs": jobs,
        "pr": pr(),
        "source": {
            "sha": source,
            "commit": {"tree": {"sha": TREE}},
            "parents": [{"sha": BASE}, {"sha": HEAD}] if event == "pull_request" else [{"sha": BASE}],
        },
        "head": {"sha": HEAD, "commit": {"tree": {"sha": TREE}}},
    }


def validate(value):
    return candidate.validate(value, candidate.ExpectedCandidate(RUN, PR, HEAD, TREE), PINS)


@pytest.mark.parametrize("event", ["pull_request", "workflow_dispatch"])
def test_complete_genuine_full_candidate_preserves_event_source_and_wall_clock(event):
    value = evidence(event)
    original = copy.deepcopy(value)
    result = validate(value)
    assert result["run_id"] == RUN
    assert result["head"] == HEAD
    assert result["tree"] == TREE
    assert result["workflow_wall_seconds"] == 900
    assert result["event"] == event
    assert value == original
    assert len(value["jobs"]) == 68
    assert len([j for j in value["jobs"] if j["name"].startswith("Test (")]) == 16
    assert len([j for j in value["jobs"] if j["name"].startswith("End-to-end ")]) == 8


@pytest.mark.parametrize("event", ["pull_request", "workflow_dispatch"])
def test_canonical_owner_release_metadata_selects_full_workload(event):
    assert candidate.release_version(event, candidate.REPOSITORY, "release/v0.9.18", {"pull_request": pr()}) == "0.9.18"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "chore(release): v0.9.19"),
        ("title", "chore(release): v0.9.18\nextra"),
        ("title", "fix: release"),
        ("head.ref", "release/v0.9.018"),
        ("head.ref", "release/v0.9.18.dev1"),
        ("head.ref", "feature/release"),
        ("head.repo", "other/fork"),
        ("base.repo", "other/fork"),
        ("base.ref", "other"),
    ],
)
def test_unvalidated_or_forked_release_selectors_grant_no_full_candidate_contract(field, value):
    release = pr()
    if field.endswith("repo"):
        release[field.split(".")[0]]["repo"]["full_name"] = value
    elif "." in field:
        first, second = field.split(".")
        release[first][second] = value
    else:
        release[field] = value
    assert candidate.release_version("pull_request", candidate.REPOSITORY, "", {"pull_request": release}) is None


@pytest.mark.parametrize("event", ["push", "schedule", "pull_request_target"])
def test_non_candidate_events_never_authorize_candidate_proofs(event):
    assert candidate.release_version(event, candidate.REPOSITORY, "release/v0.9.18", {"pull_request": pr()}) is None


@pytest.mark.parametrize(
    ("area", "field", "value"),
    [
        ("run", "run_attempt", 2),
        ("run", "id", RUN + 1),
        ("run", "status", "in_progress"),
        ("run", "conclusion", "failure"),
        ("run", "conclusion", "cancelled"),
        ("run", "head_sha", "0" * 40),
        ("run", "path", ".github/workflows/fake.yml"),
        ("run", "repository", {"full_name": "other/fork"}),
        ("run", "head_repository", {"full_name": "other/fork"}),
        ("run", "event", "push"),
        ("run", "event", "schedule"),
        ("run", "head_branch", "feature/source"),
        ("proof", "head", "0" * 40),
        ("proof", "base", "0" * 40),
        ("proof", "tree", "0" * 40),
        ("proof", "source_commit", "0" * 40),
        ("proof", "sources", {}),
        ("proof", "run_id", RUN + 1),
        ("proof", "run_attempt", 2),
        ("proof", "event", "workflow_dispatch"),
        ("proof", "pr", PR + 1),
        ("pr", "state", "closed"),
        ("pr", "draft", True),
        ("pr", "number", PR + 1),
        ("source", "parents", [{"sha": BASE}]),
        ("head", "commit", {"tree": {"sha": "0" * 40}}),
    ],
)
def test_wrong_identity_shortcuts_never_become_candidate_evidence(area, field, value):
    result = evidence()
    result[area][field] = value
    with pytest.raises(candidate.CandidateError, match=r".+"):
        validate(result)


@pytest.mark.parametrize(
    "defect", ["missing", "extra", "duplicate", "short-matrix", "cancelled", "rerun", "skipped", "never-started"]
)
def test_incomplete_or_changed_job_inventory_is_refused(defect):
    result = evidence()
    jobs = result["jobs"]
    if defect == "missing":
        jobs.pop()
    elif defect == "extra":
        jobs.append(jobs[0] | {"id": 1000, "name": "Extra workload"})
    elif defect == "duplicate":
        jobs[-1] = copy.deepcopy(jobs[0])
    elif defect == "short-matrix":
        result["jobs"] = [j for j in jobs if "Python 3.13" not in j["name"]]
    else:
        job = next(j for j in jobs if j["name"] == "Test (macos-latest, Python 3.14, unit)")
        job[{"rerun": "run_attempt", "never-started": "runner_id"}.get(defect, "conclusion")] = {
            "cancelled": "cancelled",
            "rerun": 2,
            "skipped": "skipped",
            "never-started": 0,
        }[defect]
    with pytest.raises(candidate.CandidateError, match=r".+"):
        validate(result)


def test_all_job_pages_are_exhausted_and_totals_must_agree():
    pages = []

    def get(path):
        pages.append(path)
        return {"total_count": 101, "jobs": [{"id": n} for n in (range(1, 101) if len(pages) == 1 else [101])]}

    assert len(candidate.job_inventory(get, RUN)) == 101
    assert len(pages) == 2
    with pytest.raises(candidate.CandidateError, match="truncated"):
        candidate.job_inventory(lambda _: {"total_count": 101, "jobs": []}, RUN)


def _alter_artifact_inventory(artifacts, defect):
    if defect == "wrong-artifact-run":
        artifacts["artifacts"][0]["workflow_run"]["id"] = RUN + 1
    elif defect == "wrong-artifact-head":
        artifacts["artifacts"][0]["workflow_run"]["head_sha"] = "0" * 40
    elif defect == "expired":
        artifacts["artifacts"][0]["expired"] = True
    elif defect == "duplicate-artifact":
        artifacts["artifacts"].append(copy.deepcopy(artifacts["artifacts"][0]))
        artifacts["total_count"] = 2
    elif defect == "truncated-artifacts":
        artifacts["total_count"] = 2


@pytest.mark.parametrize(
    "defect",
    [
        "none",
        "wrong-artifact-run",
        "wrong-artifact-head",
        "expired",
        "duplicate-artifact",
        "truncated-artifacts",
        "unreviewed-source",
        "run-changed",
        "extra-archive-file",
    ],
)
def test_verification_uses_unique_same_run_artifact_reviewed_sources_and_terminal_refetch(defect):
    data = evidence()
    blobs = {path: f"source {path}".encode() for path in PINS}
    pins = {path: hashlib.sha256(blob).hexdigest() for path, blob in blobs.items()}
    data["proof"]["sources"] = pins
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr(candidate.PROOF_FILE, json.dumps(data["proof"]))
        if defect == "extra-archive-file":
            zipped.writestr("unexpected.json", "{}")
    calls = []

    def get(path):
        calls.append(path)
        mapping = {
            f"actions/runs/{RUN}": data["run"],
            f"actions/runs/{RUN}/attempts/1/jobs?per_page=100&page=1": {"total_count": 68, "jobs": data["jobs"]},
            f"actions/runs/{RUN}/artifacts?per_page=100": {
                "total_count": 1,
                "artifacts": [
                    {
                        "id": 10,
                        "name": candidate.ARTIFACT,
                        "expired": False,
                        "workflow_run": {"id": RUN, "head_sha": HEAD},
                    }
                ],
            },
            f"pulls/{PR}": data["pr"],
            f"commits/{HEAD}": data["head"],
            f"commits/{MERGE}": data["source"],
            **{
                f"contents/{path}?ref={MERGE}": {
                    "type": "file",
                    "encoding": "base64",
                    "content": base64.b64encode(blob).decode(),
                }
                for path, blob in blobs.items()
            },
        }
        if defect == "run-changed" and path == f"actions/runs/{RUN}" and calls.count(path) > 1:
            return data["run"] | {"run_attempt": 2}
        artifacts = mapping[f"actions/runs/{RUN}/artifacts?per_page=100"]
        _alter_artifact_inventory(artifacts, defect)
        if defect == "unreviewed-source" and path.startswith("contents/"):
            return mapping[path] | {"content": base64.b64encode(b"different source").decode()}
        return mapping[path]

    if defect != "none":
        with pytest.raises(candidate.CandidateError, match=r".+"):
            candidate.verify(get, lambda _: archive.getvalue(), candidate.ExpectedCandidate(RUN, PR, HEAD, TREE), pins)
        return
    result = candidate.verify(get, lambda _: archive.getvalue(), candidate.ExpectedCandidate(RUN, PR, HEAD, TREE), pins)
    assert result["job_count"] == 68
    assert calls.count(f"actions/runs/{RUN}") == 2


@pytest.mark.parametrize("defect", ["none", "duplicate", "rerun", "wrong-head", "missing", "truncated"])
def test_discovery_never_samples_a_later_green_or_retries_failed_heads(defect):
    run = evidence()["run"]
    rows = [run]
    if defect == "duplicate":
        rows.append(run | {"id": RUN + 1})
    elif defect == "rerun":
        rows[0] = run | {"run_attempt": 2}
    elif defect == "wrong-head":
        rows[0] = run | {"head_sha": "0" * 40}
    elif defect in ("missing", "truncated"):
        rows = []
    payload = {"total_count": 1 if defect == "truncated" else len(rows), "workflow_runs": rows}
    if defect != "none":
        with pytest.raises(candidate.CandidateError, match=r".+"):
            candidate.discover(lambda _: payload, HEAD)
    else:
        assert candidate.discover(lambda _: payload, HEAD) == RUN


@pytest.mark.parametrize("event", ["pull_request", "workflow_dispatch"])
def test_recorder_binds_real_git_tree_and_exact_project_version(tmp_path, monkeypatch, event):
    monkeypatch.chdir(tmp_path)
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)

    def git(*args):
        return subprocess.run(["git", *args], capture_output=True, text=True, check=True, timeout=3).stdout.strip()

    git("init", "--quiet")
    git("config", "user.name", "Example Maintainer")
    git("config", "user.email", "maintainer@example.invalid")
    for source in (candidate.WORKFLOW, candidate.RECORDER):
        path = tmp_path / source
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text((ROOT / source).read_text())
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.9.16"\n')
    git("add", ".")
    git("commit", "--quiet", "-m", "chore: initialize fixture")
    base = git("rev-parse", "HEAD")
    git("update-ref", "refs/remotes/origin/main", base)
    git("switch", "--quiet", "-c", "release/v0.9.18")
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.9.18"\n')
    git("commit", "--quiet", "-a", "-m", "chore(release): v0.9.18")
    head = git("rev-parse", "HEAD")
    tree = git("rev-parse", "HEAD^{tree}")
    payload = {"pull_request": pr()}
    payload["pull_request"]["head"]["sha"] = head
    payload["pull_request"]["base"]["sha"] = base
    if event == "pull_request":
        git("switch", "--quiet", "--detach", base)
        git("merge", "--quiet", "--no-ff", "--no-edit", "release/v0.9.18")
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(payload))
    for key, value in {
        "GITHUB_REPOSITORY": candidate.REPOSITORY,
        "GITHUB_EVENT_NAME": event,
        "GITHUB_REF_NAME": "release/v0.9.18",
        "GITHUB_EVENT_PATH": str(event_path),
        "GITHUB_SHA": git("rev-parse", "HEAD"),
        "GITHUB_RUN_ID": str(RUN),
        "GITHUB_RUN_ATTEMPT": "1",
    }.items():
        monkeypatch.setenv(key, value)
    # Execute the literal workflow commands with no dependency cache or uv resolution.
    workflow = (ROOT / candidate.WORKFLOW).read_text()
    cold = tmp_path / "cold"
    cold.mkdir()
    output = tmp_path / "outputs"
    env = {key: value for key, value in os.environ.items() if key.startswith("GITHUB_")}
    env.update(
        PATH=os.environ["PATH"],
        HOME=str(cold),
        UV_CACHE_DIR=str(cold / "uv"),
        UV_FROZEN="1",
        GITHUB_OUTPUT=str(output),
        GIT_CONFIG_GLOBAL=os.devnull,
    )
    for role in ("select", "record"):
        line = next(
            line.strip()[5:]
            for line in workflow.splitlines()
            if line.strip().startswith("run: ") and f"release_candidate.py {role} " in line
        )
        argv = shlex.split(line.replace('"$GITHUB_OUTPUT"', str(output)))
        assert argv[:3] == ["python3", "-I", "scripts/release_candidate.py"]
        subprocess.run(argv, cwd=tmp_path, env=env, check=True, capture_output=True, text=True, timeout=3)
    assert output.read_text() == "full_release=true\n"
    assert json.loads((tmp_path / "reports/release-candidate.json").read_text())["tree"] == tree
    assert not (cold / "uv").exists()
    result = candidate.record(tmp_path)
    assert result["head"] == head
    assert result["base"] == base
    assert result["tree"] == tree
    assert result["event"] == event
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.9.19"\n')
    with pytest.raises(candidate.CandidateError, match="project version differs"):
        candidate.record(tmp_path)


def test_source_artifact_duplicate_json_fields_are_refused():
    with pytest.raises(candidate.CandidateError, match="duplicate"):
        json.loads('{"head": "first", "head": "second"}', object_pairs_hook=candidate.unique_object)


@pytest.mark.parametrize("defect", ["changed-count", "duplicate-id", "excess-count"])
def test_job_pagination_never_infers_a_complete_inventory_from_partial_or_repeated_pages(defect):
    calls = []

    def get(path):
        calls.append(path)
        if len(calls) == 1:
            return {"total_count": 101, "jobs": [{"id": n} for n in range(1, 101)]}
        return {
            "total_count": 102 if defect == "changed-count" else 101,
            "jobs": [{"id": 100 if defect == "duplicate-id" else 101}] * (2 if defect == "excess-count" else 1),
        }

    with pytest.raises(candidate.CandidateError, match=r".+"):
        candidate.job_inventory(get, RUN)
