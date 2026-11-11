"""The protected judge binds coverage evidence to an immutable original upload."""

import base64
import gzip
import hashlib
import io
import json
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


def _advisory_check(repo, run, head, suite_id):
    check_id = 803
    url = f"https://github.com/{repo}/runs/{check_id}"
    job = {
        "id": check_id,
        "name": "Claude review",
        "run_id": run,
        "run_attempt": 1,
        "head_sha": head,
        "html_url": url,
        "check_run_url": f"https://api.github.com/repos/{repo}/check-runs/{check_id}",
        "runner_id": None,
        "runner_name": None,
        "steps": [],
        "labels": [],
        "status": "completed",
        "conclusion": "neutral",
    }
    check = job | {"details_url": url, "app": {"id": 15368}, "check_suite": {"id": suite_id}}
    return job, check


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({}, ""),
        ({"job": {"runner_id": 99, "runner_name": "GitHub Actions"}}, "workload job inventory"),
        ({"job": {"name": "Claude review lookalike"}}, "workload job inventory"),
        ({"job": {"conclusion": "failure"}}, "workload job inventory"),
        ({"job": {"status": "in_progress"}}, "job identity or attempt"),
        ({"job": {"run_attempt": 2}}, "job identity or attempt"),
        ({"job": {"run_id": 999}}, "job identity or attempt"),
        ({"job": {"head_sha": "f" * 40}}, "job identity or attempt"),
        ({"job": {"html_url": "https://github.com/other/repo/runs/803"}}, "workload job inventory"),
        (
            {"job": {"check_run_url": "https://api.github.com/repos/other/repo/check-runs/803"}},
            "workload job inventory",
        ),
        ({"check": {"id": 999}}, "workload job inventory"),
        ({"check": {"app": {"id": 999}}}, "workload job inventory"),
        ({"check": {"check_suite": {"id": 999}}}, "workload job inventory"),
        ({"check": {"details_url": "https://github.com/other/repo/runs/803"}}, "workload job inventory"),
        ({"check": {"conclusion": "success"}}, "workload job inventory"),
    ],
)
def test_protected_reader_only_excludes_authentic_neutral_advisory(
    focused_gate, first_attempt, monkeypatch, changes, error
):
    gate, run, manifest, lane, rows, logs, _git_rows = first_attempt
    suite_id = rows[f"actions/runs/{run}"]["check_suite_id"]
    job, check = _advisory_check(gate.repo, run, gate.head, suite_id)
    page = rows[f"actions/runs/{run}/attempts/1/jobs?per_page=100&page=1"]
    page["jobs"].extend([*_appended_checks(gate.repo, run, lane, "none"), job | changes.get("job", {})])
    page["total_count"] = len(page["jobs"])
    rows[f"check-runs/{job['id']}"] = check | changes.get("check", {})
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
@pytest.mark.parametrize("transport", ["legacy", "slot"])
def test_protected_reader_verifies_complete_attempt(  # noqa: PLR0913, PLR0917 - independent evidence and transport cases
    focused_gate, first_attempt, monkeypatch, defect, error, transport
):
    gate, run, manifest, lane, rows, logs, _git_rows = first_attempt
    from scripts.focused_plan import encode_slot

    slots = (encode_slot(json.loads(manifest)),) if transport == "slot" else ()
    manifest = "{}" if slots else manifest
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
            focused_gate.verify(gate, run, manifest, logs.__getitem__, slots=slots)
    else:
        assert focused_gate.verify(gate, run, manifest, logs.__getitem__, slots=slots) == (
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


@pytest.mark.parametrize("defect", ["none", "unrelated-base", "wrong-parent", "wrong-tree", "bad-digest", "moved-main"])
@pytest.mark.parametrize("transport", ["legacy", "slot"])
def test_focused_reader_authenticates_original_artifact_before_accepting_ancestor_base(  # noqa: C901 - independent drift cases
    focused_gate, first_attempt, monkeypatch, defect, transport
):
    from scripts import focused_plan

    gate, run_id, manifest, _lane, rows, logs, git_rows = first_attempt
    from scripts.focused_plan import encode_slot

    slots = (encode_slot(json.loads(manifest)),) if transport == "slot" else ()
    manifest = "{}" if slots else manifest
    old = rows["git/ref/heads/main"]["object"]["sha"]
    current, current_merge = "f" * 40, "1" * 40
    original_merge = "c" * 40
    ref = f"refs/pull/{gate.pr}/merge"
    rows["git/ref/heads/main"]["object"]["sha"] = current
    git_rows[("ls-remote", "--exit-code", "origin", ref)] = f"{current_merge} {ref}".encode()
    git_rows[
        ("diff-tree", "--no-commit-id", "-r", "--raw", "--no-abbrev", "--no-renames", "-z", current, current_merge)
    ] = b"current"
    git_rows[("merge-base", "--is-ancestor", old, current)] = b""
    git_rows[("fetch", "--no-tags", "origin", old)] = b""
    git_rows[("fetch", "--no-tags", "origin", original_merge)] = b""
    if defect == "unrelated-base":
        key = ("merge-base", "--is-ancestor", old, current)
        git_rows[key] = CalledProcessError(1, key)
    elif defect == "wrong-parent":
        git_rows[("rev-list", "--parents", "-n", "1", original_merge)] = (
            f"{original_merge} {current} {gate.head}".encode()
        )
    elif defect == "wrong-tree":
        monkeypatch.setattr(focused_plan, "object_id", lambda _ref: "2" * 40)
    elif defect == "bad-digest":
        rows[f"actions/runs/{run_id}/artifacts?per_page=100"]["artifacts"][0]["digest"] = "sha256:" + "0" * 64
    state = {"fetched": current_merge, "main_reads": 0}

    def git(*args):
        if args[:3] == ("fetch", "--no-tags", "origin"):
            state["fetched"] = current_merge if args[3] == ref else args[3]
        if args == ("rev-parse", "FETCH_HEAD"):
            return state["fetched"].encode()
        value = git_rows[args]
        if isinstance(value, CalledProcessError):
            raise value
        return value

    def github(_gate, path):
        if path == "git/ref/heads/main":
            state["main_reads"] += 1
            if defect == "moved-main" and state["main_reads"] > 1:
                return {"object": {"sha": "3" * 40}}
        return rows[path]

    monkeypatch.setattr(focused_gate, "git", git)
    monkeypatch.setattr(focused_plan, "git", git)
    monkeypatch.setattr(type(gate), "github", github)
    if defect == "none":
        assert (
            focused_gate.verify(gate, run_id, manifest, logs.__getitem__, slots=slots)
            == "Python coverage: not applicable to the reviewed scope"
        )
    else:
        with pytest.raises(focused_gate.CandidateError, match=r".+"):
            focused_gate.verify(gate, run_id, manifest, logs.__getitem__, slots=slots)


KEY = "pull_request:" + "a" * 40
PAIR = {KEY: {"schema": 1}, "push-tree:" + "b" * 40: {"schema": 1}}
EMPTY = ("", "", "", "")


@pytest.fixture
def registry(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    from scripts import focused_plan

    return focused_plan


def _envelope(raw, **changes):
    envelope = {
        "schema": 1,
        "codec": "gzip+base64",
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "data": base64.b64encode(gzip.compress(raw, mtime=0)).decode(),
    }
    return json.dumps(envelope | changes)


@pytest.mark.parametrize("position", range(4))
def test_slots_preserve_legacy_and_original_manifest_digest(registry, position):
    legacy = '{ "push:' + "c" * 40 + '": {"schema":1} }'
    original = legacy
    slots = list(EMPTY)
    slots[position] = registry.encode_slot(PAIR)
    combined = registry.plan_registry(legacy, tuple(slots))
    assert combined == json.loads(legacy) | PAIR
    assert legacy == original
    assert registry.registry_bytes(combined[KEY]) == registry.registry_bytes(PAIR[KEY])
    assert registry.selected_registry(legacy, tuple(slots), KEY) == registry.registry_bytes({KEY: PAIR[KEY]}).decode()
    assert registry.registry_slots(dict(zip(registry.SLOT_VARIABLES, slots, strict=True))) == tuple(slots)


@pytest.mark.parametrize(
    "changes",
    [
        {"schema": True},
        {"schema": 2},
        {"codec": "zlib"},
        {"bytes": True},
        {"bytes": -1},
        {"bytes": 1048577},
        {"bytes": 1},
        {"sha256": "0" * 64},
        {"data": "!!"},
        {"extra": 1},
        {"data": "é"},
    ],
)
def test_envelope_rejects_invalid_contract(registry, changes):
    with pytest.raises(registry.CandidateError):
        registry.decode_slot(_envelope(b"{}", **changes))


@pytest.mark.parametrize("suffix", [b"junk", gzip.compress(b"{}", mtime=0)])
def test_one_complete_gzip_member_is_required(registry, suffix):
    data = base64.b64encode(gzip.compress(b"{}", mtime=0) + suffix).decode()
    with pytest.raises(registry.CandidateError, match="stream"):
        registry.decode_slot(_envelope(b"{}", data=data))


@pytest.mark.parametrize(
    "raw",
    [b'{"same":1,"same":2}', b'{"x":NaN}', b'{"x":Infinity}', b'"not-an-object"', b"\xff", b"{ }", b'{"bad":{}}'],
)
def test_decoded_registry_is_strict_canonical_json(registry, raw):
    with pytest.raises(registry.CandidateError):
        registry.decode_slot(_envelope(raw))


def test_duplicate_envelope_and_nested_manifest_keys_are_rejected(registry):
    encoded = _envelope(b"{}")
    with pytest.raises(registry.CandidateError):
        registry.decode_slot(encoded[:-1] + ', "schema":1}')
    with pytest.raises(registry.CandidateError):
        registry.decode_slot(_envelope(("{" + json.dumps(KEY) + ':{"schema":1,"schema":1}}').encode()))


def test_bomb_truncation_and_transport_limits_fail_closed(registry):
    bomb = b" " * (registry.MAX_SLOT_DECODED + 1)
    with pytest.raises(registry.CandidateError, match="stream"):
        registry.decode_slot(_envelope(bomb, bytes=2))
    truncated = base64.b64encode(gzip.compress(b"{}", mtime=0)[:-1]).decode()
    with pytest.raises(registry.CandidateError, match="stream"):
        registry.decode_slot(_envelope(b"{}", data=truncated))
    with pytest.raises(registry.CandidateError, match="encoded limit"):
        registry.decode_slot(" " * (registry.MAX_SLOT + 1))
    with pytest.raises(registry.CandidateError, match="supported size"):
        registry.plan_registry(" " * (registry.MAX_PLAN + 1), EMPTY)
    with pytest.raises(registry.CandidateError, match="too many"):
        registry.plan_registry("{}", (*EMPTY, ""))
    with pytest.raises(registry.CandidateError, match="original plan limit"):
        registry.decode_slot(_envelope(registry.registry_bytes({KEY: {"padding": "x" * registry.MAX_PLAN}})))
    entries = {f"push-tree:{i:040x}": {} for i in range(registry.MAX_SLOT_ENTRIES + 1)}
    with pytest.raises(registry.CandidateError, match="too many entries"):
        registry.decode_slot(_envelope(registry.registry_bytes(entries)))


def test_conflicts_fail_even_when_unused_and_boolean_is_not_integer(registry):
    first = registry.encode_slot(PAIR)
    same = registry.plan_registry(json.dumps(PAIR), (first,))
    assert same == PAIR
    conflict = registry.encode_slot({KEY: {"schema": True}})
    with pytest.raises(registry.CandidateError, match="conflicting"):
        registry.selected_registry(json.dumps(PAIR), (conflict,), "push:" + "d" * 40)
    with pytest.raises(registry.CandidateError, match="conflicting"):
        registry.plan_registry("{}", (first, conflict))


def test_pair_append_preserves_all_history_and_is_idempotent(registry):
    historical = {"push-tree:" + "c" * 40: {"schema": 1}}
    slots = (registry.encode_slot(historical), "", "", "")
    result = registry.append_slot("{}", slots, PAIR)
    assert result is not None
    index, encoded = result
    assert index == 0
    assert registry.decode_slot(encoded) == historical | PAIR
    updated = (encoded, *slots[1:])
    assert registry.append_slot("{}", updated, PAIR) is None
    assert registry.decode_slot(slots[0]) == historical
    with pytest.raises(registry.CandidateError, match="replace"):
        registry.append_slot("{}", updated, {KEY: {"schema": 2}})
    with pytest.raises(registry.CandidateError, match="partially"):
        registry.append_slot(json.dumps({KEY: PAIR[KEY]}), EMPTY, PAIR)


def test_full_slots_cannot_evict_or_split_an_admission_pair(registry):
    full = {f"push-tree:{i:040x}": {} for i in range(registry.MAX_SLOT_ENTRIES - 1)}
    encoded = registry.encode_slot(full)
    slots = (encoded, "", "", "")
    result = registry.append_slot("{}", slots, PAIR)
    assert result is not None
    assert result[0] == 1
    assert registry.decode_slot(result[1]) == PAIR
    with pytest.raises(registry.CandidateError, match="all focused registry slots are full"):
        registry.append_slot("{}", (encoded,) * 4, PAIR)


def test_actual_ci_and_protected_gate_inject_all_four_optional_slots(registry):
    root = Path(__file__).resolve().parents[2]
    for file, readers in (("ci.yml", 2), ("merge-gate.yml", 1)):
        text = (root / ".github/workflows" / file).read_text()
        assert text.count("FOCUSED_CI_PLANS: ${{ vars.FOCUSED_CI_PLANS }}") == readers
        for name in registry.SLOT_VARIABLES:
            assert text.count(f"{name}: ${{{{ vars.{name} }}}}") == readers


@pytest.fixture
def source_manifest(registry, monkeypatch):
    base, head, merge, tree, blob = (digit * 40 for digit in "abcde")
    path = "src/heinzel/new.py"
    diff = f":000000 100644 {'0' * 40} {blob} A\0{path}\0".encode()
    payload = {
        "pull_request": {
            "number": 5,
            "base": {"sha": base, "ref": "main", "repo": {"full_name": registry.REPOSITORY}},
            "head": {"sha": head},
        }
    }
    source = registry.WorkloadSource(
        "pull_request", registry.REPOSITORY, "refs/pull/5/merge", payload, base, head, diff
    )
    manifest = {
        "schema": 1,
        "event": "pull_request",
        "ref": registry.PREOPEN_PR_REF,
        "base": base,
        "head": head,
        "tree": tree,
        "diff_sha256": hashlib.sha256(diff).hexdigest(),
        "changed": {path: "A"},
        "mode": "pr-scoped",
        "config_blob": blob,
        "execution": dict.fromkeys(registry.PROTECTED_FILES, blob),
        "production": {path: blob},
        "selection": {"python": ["tests/unit/new_test.py"]},
        "case_sha256": {"python": "f" * 64},
    }
    monkeypatch.setattr(registry, "git_blob", lambda *_args: blob)
    monkeypatch.setattr(registry, "object_id", lambda _ref: tree)
    monkeypatch.setattr(registry, "git", lambda *_args: f"{merge} {base} {head}".encode())
    return source, manifest, merge


@pytest.mark.parametrize(
    "defect", ["none", "production", "execution", "case_sha256", "diff_sha256", "tree", "config_blob", "base"]
)
def test_slot_transport_cannot_bypass_original_source_or_coverage_contract(registry, source_manifest, defect):
    source, manifest, commit = source_manifest
    key = f"pull_request:{source.head}"
    original = registry.resolve(source, commit, json.dumps({key: manifest}))
    if defect in {"production", "execution", "case_sha256"}:
        manifest[defect] = {}
    elif defect != "none":
        manifest[defect] = "0" * (64 if defect == "diff_sha256" else 40)
    slots = (registry.encode_slot({key: manifest}),)
    if defect == "none":
        assert registry.resolve(source, commit, "{}", slots=slots) == original
    else:
        with pytest.raises(registry.CandidateError):
            registry.resolve(source, commit, "{}", slots=slots)


def test_release_candidate_remains_independent_of_broken_slots(registry, source_manifest):
    source, _manifest, commit = source_manifest
    pr = source.payload["pull_request"]
    pr.update(title="chore(release): v0.9.37", state="open", draft=False)
    pr["head"].update(ref="release/v0.9.37", repo={"full_name": registry.REPOSITORY})
    assert isinstance(registry.resolve(source, commit, "not-json", slots=("broken",)), registry.CandidatePlan)


def test_main_tree_lookup_preserves_digest_and_exact_head_precedence(registry, source_manifest):
    from dataclasses import replace

    source, manifest, _commit = source_manifest
    source = replace(source, event="push", ref="refs/heads/main", payload={})
    manifest.update(event="push", ref=source.ref)
    del manifest["head"]
    tree_key = f"push-tree:{manifest['tree']}"
    encoded = registry.encode_slot({tree_key: manifest})
    scope = registry.resolve(source, source.head, "{}", slots=(encoded,))
    assert scope.manifest_digest == hashlib.sha256(registry.registry_bytes(manifest)).hexdigest()
    bad = manifest | {"head": source.head, "base": "0" * 40}
    with pytest.raises(registry.CandidateError):
        registry.resolve(source, source.head, json.dumps({f"push:{source.head}": bad}), slots=(encoded,))


@pytest.mark.parametrize("defect", ["none", "changed-case", "changed-config"])
def test_protected_main_reader_uses_slots_without_weakening_evidence(focused_gate, first_attempt, monkeypatch, defect):
    from scripts import focused_coverage, focused_plan

    gate, run_id, manifest, lane, rows, logs, git_rows = first_attempt
    plan = next(iter(json.loads(manifest).values()))
    base, head, tree = plan["base"], plan["head"], plan["tree"]
    plan.update(event="push", ref="refs/heads/main")
    del plan["head"]
    run = rows[f"actions/runs/{run_id}"]
    run.update(event="push", head_branch="main", head_repository={"full_name": gate.repo})
    rows["git/ref/heads/main"] = {"object": {"sha": head}}
    rows[f"actions/workflows/{focused_gate.CI_WORKFLOW_ID}/runs?head_sha={head}&event=push&per_page=1"] = {
        "workflow_runs": [run]
    }
    jobs = rows[f"actions/runs/{run_id}/attempts/1/jobs?per_page=100&page=1"]
    jobs["jobs"] = [job for job in jobs["jobs"] if job["name"] not in focused_gate.PR_JOBS]
    jobs["total_count"] = len(jobs["jobs"])
    diff = git_rows[("diff-tree", "--no-commit-id", "-r", "--raw", "--no-abbrev", "--no-renames", "-z", base, "c" * 40)]
    git_rows.update(
        {
            ("fetch", "--no-tags", "origin", "refs/heads/main"): b"",
            ("rev-parse", "FETCH_HEAD"): head.encode(),
            ("merge-base", base, head): base.encode(),
            ("diff-tree", "--no-commit-id", "-r", "--raw", "--no-abbrev", "--no-renames", "-z", base, head): diff,
        }
    )
    source = focused_plan.WorkloadSource("push", gate.repo, "refs/heads/main", {}, base, head, diff)
    registry = {f"push-tree:{tree}": plan}
    scope = focused_plan.resolve(source, head, json.dumps(registry))
    junit = b'<testsuite><testcase classname="test_unit" name="test_case"/></testsuite>'
    report = focused_coverage.canonical(focused_coverage.evidence(scope, "python", run_id, 1, {"junit.xml": junit}))
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("evidence.json", report)
        archive.writestr("junit.xml", junit)
    content = output.getvalue()
    artifact = rows[f"actions/runs/{run_id}/artifacts?per_page=100"]["artifacts"][0]
    artifact.update(size_in_bytes=len(content), digest=f"sha256:{hashlib.sha256(content).hexdigest()}")
    logs[f"actions/artifacts/{artifact['id']}/zip"] = content
    logs[f"actions/jobs/{lane['id']}/steps/6/logs"] = (
        f"Artifact {artifact['name']} has been successfully uploaded! "
        f"Final size is {len(content)} bytes. Artifact ID is {artifact['id']}\n"
    ).encode()
    if defect == "changed-case":
        plan["case_sha256"]["python"] = "0" * 64
    elif defect == "changed-config":
        plan["config_blob"] = "0" * 40
    monkeypatch.setattr(type(gate), "github", lambda _gate, path: rows[path])
    slots = (focused_plan.encode_slot(registry),)
    if defect == "none":
        result = focused_gate.verify_main(gate, run_id, base, "{}", logs.__getitem__, slots=slots)
        assert result["manifest_sha256"] == scope.manifest_digest
        assert result["tree"] == tree
    else:
        with pytest.raises(focused_gate.CandidateError):
            focused_gate.verify_main(gate, run_id, base, "{}", logs.__getitem__, slots=slots)
