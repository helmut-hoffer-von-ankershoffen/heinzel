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


def test_a_rerun_needs_an_unsuccessful_original_attempt_on_both_tiers(focused_gate, monkeypatch):
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
    assert focused_gate._authenticated_run(gate, 123) == live  # merge tier: a rerun completes the evidence
    assert focused_gate._authenticated_run(gate, 123, candidate_composite=True) == live
    original["conclusion"] = "timed_out"  # the release tier keeps its narrower recovery set
    assert focused_gate._authenticated_run(gate, 123) == live
    with pytest.raises(focused_gate.CandidateError, match="unsuccessful original attempt"):
        focused_gate._authenticated_run(gate, 123, candidate_composite=True)
    original["conclusion"] = "success"
    with pytest.raises(focused_gate.CandidateError, match="unsuccessful original attempt"):
        focused_gate._authenticated_run(gate, 123)
    original["conclusion"] = "failure"
    original["head_sha"] = "b" * 40
    with pytest.raises(focused_gate.CandidateError, match="canonical CI workflow"):
        focused_gate._authenticated_run(gate, 123, candidate_composite=True)


@pytest.fixture
def first_attempt(focused_gate, monkeypatch):
    """A complete successful PR attempt with independent Git and GitHub observations."""

    from scripts import focused_coverage, focused_plan, merge_gate
    from scripts.ci_workload import WorkloadSource
    from scripts.release_candidate import REPOSITORY, WORKFLOW

    base, head, merge, tree, blob = (digit * 40 for digit in "abcde")
    ref, number, run_id, suite_id = "refs/pull/5/merge", 5, 123, 234
    config = (Path(__file__).resolve().parents[2] / "pyproject.toml").read_bytes()
    test_file = "tests/unit/focused_gate_test.py"
    diff = f":100644 100644 {head} {blob} M\0{test_file}\0".encode()
    junit = b'<testsuite><testcase classname="tests.unit.focused_gate_test" name="test_case"/></testsuite>'
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
    # raising=False: the same fixture also drives these tests against older judges that import git_blob directly
    for module in (focused_plan, focused_gate):
        monkeypatch.setattr(module, "git_blob", lambda *_args: blob, raising=False)
    monkeypatch.setattr(focused_plan, "tracked_paths", lambda *_args: [], raising=False)
    monkeypatch.setattr(focused_plan, "object_id", lambda _ref: tree)
    # The judge's own selector over the tested tree; no manifest is needed.
    monkeypatch.setattr(focused_plan, "selector_lanes", lambda _commit, _changed: ({"python": [test_file]}, "focused"))
    manifest = "{}"
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
        f"actions/runs/{run_id}/jobs?filter=latest&per_page=100&page=1": {"total_count": len(jobs), "jobs": jobs},
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
    page = rows[f"actions/runs/{run}/jobs?filter=latest&per_page=100&page=1"]
    page["jobs"].extend([*_appended_checks(gate.repo, run, lane, "none"), job | changes.get("job", {})])
    page["total_count"] = len(page["jobs"])
    rows[f"check-runs/{job['id']}"] = check | changes.get("check", {})
    monkeypatch.setattr(type(gate), "github", lambda _gate, path: rows[path])
    if error:
        with pytest.raises(focused_gate.CandidateError, match=error):
            focused_gate.verify(gate, run, manifest, logs.__getitem__)
    else:
        assert focused_gate.verify(gate, run, manifest, logs.__getitem__) == (
            "Python coverage: not applicable to the selected scope"
        )


@pytest.mark.parametrize(
    ("defect", "error"),
    [
        ("none", ""),
        ("pending-api-checks", ""),
        ("lookalike-real-job", "workload job inventory"),
        ("duplicate-api-row", "duplicate job IDs"),
        ("wrong-job-attempt", "job identity or attempt"),
        ("wrong-job-app", "authentic successful execution"),
        ("missing-lane", "evidence"),
        ("missing-artifact", "evidence"),
        ("narrowed-by-pr", "ran no case|evidence differs"),
    ],
)
def test_protected_reader_verifies_complete_attempt(focused_gate, first_attempt, monkeypatch, defect, error):
    from scripts import focused_plan

    gate, run, manifest, lane, rows, logs, _git_rows = first_attempt
    jobs_path = f"actions/runs/{run}/jobs?filter=latest&per_page=100&page=1"
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
    elif defect == "narrowed-by-pr":
        # The judge's selector (main's) wants more than the run executed: the evidence cannot match.
        wider = {"python": ["tests/unit/focused_gate_test.py", "tests/unit/focused_plan_test.py"]}
        monkeypatch.setattr(focused_plan, "selector_lanes", lambda _commit, _changed: (wider, "focused"))

    monkeypatch.setattr(type(gate), "github", lambda _gate, path: rows[path])
    if error:
        with pytest.raises(focused_gate.CandidateError, match=error):
            focused_gate.verify(gate, run, manifest, logs.__getitem__)
    else:
        assert focused_gate.verify(gate, run, manifest, logs.__getitem__) == (
            "Python coverage: not applicable to the selected scope"
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
        ("old-merge-base", "test merge differs from the tested base"),
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
            "Python coverage: not applicable to the selected scope"
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
def test_focused_reader_authenticates_original_artifact_before_accepting_ancestor_base(  # noqa: C901 - independent drift cases
    focused_gate, first_attempt, monkeypatch, defect
):
    from scripts import focused_plan

    gate, run_id, manifest, _lane, rows, logs, git_rows = first_attempt
    slots = ()
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
        diff = (
            "diff-tree",
            "--no-commit-id",
            "-r",
            "--raw",
            "--no-abbrev",
            "--no-renames",
            "-z",
            current,
            original_merge,
        )
        git_rows[diff] = git_rows[(*diff[:-2], old, original_merge)]
        git_rows[("merge-base", "--is-ancestor", current, current)] = b""
        git_rows[("show", f"{current}:pyproject.toml")] = git_rows[("show", f"{old}:pyproject.toml")]
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
            == "Python coverage: not applicable to the selected scope"
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
    for file, readers in (("ci.yml", 3), ("merge-gate.yml", 1)):
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


@pytest.mark.parametrize("transport", ["legacy", "slot"])
def test_a_manifest_only_widens_the_selection(registry, source_manifest, monkeypatch, transport):
    source, manifest, commit = source_manifest
    selected = {"python": ["tests/unit/new_test.py"]}
    monkeypatch.setattr(registry, "selector_lanes", lambda _commit, _changed: (selected, "focused"))
    automatic = registry.resolve(source, commit, "{}")
    assert dict(automatic.selection) == {"python": ("tests/unit/new_test.py",)}
    manifest["selection"] = {"python": ["tests/unit/other_test.py"], "web": ["web/src/x.test.ts"]}
    key = f"pull_request:{source.head}"
    legacy, slots = (
        (json.dumps({key: manifest}), ()) if transport == "legacy" else ("{}", (registry.encode_slot({key: manifest}),))
    )
    widened = registry.resolve(source, commit, legacy, slots=slots)
    assert widened.selection == {
        "python": ("tests/unit/new_test.py", "tests/unit/other_test.py"),
        "web": ("web/src/x.test.ts",),
    }
    manifest["ref"] = "refs/heads/elsewhere"
    with pytest.raises(registry.CandidateError, match="widening plan does not apply"):
        registry.resolve(source, commit, json.dumps({key: manifest}))


def test_release_candidate_remains_independent_of_broken_slots(registry, source_manifest):
    source, _manifest, commit = source_manifest
    pr = source.payload["pull_request"]
    pr.update(title="chore(release): v0.9.37", state="open", draft=False)
    pr["head"].update(ref="release/v0.9.37", repo={"full_name": registry.REPOSITORY})
    assert isinstance(registry.resolve(source, commit, "not-json", slots=("broken",)), registry.CandidatePlan)


def test_main_tree_widening_applies_to_a_main_push(registry, source_manifest, monkeypatch):
    from dataclasses import replace

    source, manifest, _commit = source_manifest
    source = replace(source, event="push", ref="refs/heads/main", payload={})
    monkeypatch.setattr(
        registry, "selector_lanes", lambda _commit, _changed: ({"python": ["tests/unit/new_test.py"]}, "focused")
    )
    manifest.update(event="push", ref=source.ref, selection={"web": ["web/src/x.test.ts"]})
    encoded = registry.encode_slot({f"push-tree:{manifest['tree']}": manifest})
    scope = registry.resolve(source, source.head, "{}", slots=(encoded,))
    assert set(scope.selection) == {"python", "web"}


@pytest.mark.parametrize("defect", ["none", "base-after-last-green", "no-green-run", "changed-config", "rerun"])
def test_protected_main_reader_compares_against_the_last_green_main(focused_gate, first_attempt, monkeypatch, defect):  # noqa: PLR0915 - one fixture, five cases
    from scripts import focused_coverage, focused_plan

    gate, run_id, _manifest, lane, rows, logs, git_rows = first_attempt
    base, head, older = "a" * 40, gate.head, "9" * 40
    run = rows[f"actions/runs/{run_id}"]
    run.update(event="push", head_branch="main", head_repository={"full_name": gate.repo})
    rows["git/ref/heads/main"] = {"object": {"sha": head}}
    rows[f"actions/workflows/{focused_gate.CI_WORKFLOW_ID}/runs?head_sha={head}&event=push&per_page=1"] = {
        "workflow_runs": [run]
    }
    green = older if defect == "base-after-last-green" else base
    runs = [] if defect == "no-green-run" else [{"id": 77, "head_sha": green}]
    rows["actions/workflows/ci.yml/runs?branch=main&event=push&status=success&per_page=30"] = {"workflow_runs": runs}
    rows["actions/runs/77/jobs?filter=latest&per_page=100"] = {"jobs": [{"name": "CI OK", "conclusion": "success"}]}
    rows[f"commits/{green}/statuses?per_page=100"] = [{"context": "hz/main-proof", "state": "success"}]
    jobs = rows[f"actions/runs/{run_id}/jobs?filter=latest&per_page=100&page=1"]
    jobs["jobs"] = [job for job in jobs["jobs"] if job["name"] not in focused_gate.PR_JOBS]
    jobs["total_count"] = len(jobs["jobs"])
    diff = git_rows[("diff-tree", "--no-commit-id", "-r", "--raw", "--no-abbrev", "--no-renames", "-z", base, "c" * 40)]
    git_rows.update(
        {
            ("fetch", "--no-tags", "origin", "refs/heads/main"): b"",
            ("rev-parse", "FETCH_HEAD"): head.encode(),
            ("merge-base", base, head): base.encode(),
            ("merge-base", "--is-ancestor", green, head): b"",
            ("merge-base", base, green): (older if green == older else base).encode(),
            ("diff-tree", "--no-commit-id", "-r", "--raw", "--no-abbrev", "--no-renames", "-z", base, head): diff,
        }
    )
    if defect == "changed-config":
        relaxed = git_rows[("show", f"{base}:pyproject.toml")].replace(b"fail_under = 90", b"fail_under = 80")
        assert relaxed != git_rows[("show", f"{base}:pyproject.toml")]
        git_rows[("show", f"{base}:pyproject.toml")] = relaxed
    source = focused_plan.WorkloadSource("push", gate.repo, "refs/heads/main", {}, base, head, diff)
    scope = focused_plan.resolve(source, head, "{}")
    junit = b'<testsuite><testcase classname="tests.unit.focused_gate_test" name="test_case"/></testsuite>'
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
    monkeypatch.setattr(type(gate), "github", lambda _gate, path: rows[path])
    if defect == "rerun":  # main repaired by a rerun: the proof carries the flake ledger
        original = dict(run, run_attempt=1, conclusion="failure")
        run["run_attempt"] = 2
        rows[f"actions/runs/{run_id}/attempts/1"] = original
        failed = dict(lane, id=999, run_attempt=1, conclusion="failure")
        every = [*jobs["jobs"], failed]
        rows[f"actions/runs/{run_id}/jobs?filter=all&per_page=100&page=1"] = {"total_count": len(every), "jobs": every}
        result = focused_gate.verify_main(gate, run_id, base, "{}", logs.__getitem__)
        assert result["passed_on_rerun"] == [
            f"passed on rerun: Focused tests (python) (attempt 1 failure, run {run_id}, head {head})"
        ]
    elif defect == "none":
        result = focused_gate.verify_main(gate, run_id, base, "{}", logs.__getitem__)
        assert result["manifest_sha256"] == scope.manifest_digest
        assert (result["attempt"], result["passed_on_rerun"], result["full"]) == (1, [], False)
    else:
        with pytest.raises(focused_gate.CandidateError):
            focused_gate.verify_main(gate, run_id, base, "{}", logs.__getitem__)


def test_a_rerun_completes_merge_evidence_and_the_flake_stays_on_record(focused_gate, first_attempt, monkeypatch):
    gate, run_id, manifest, lane, rows, logs, _git_rows = first_attempt
    run = rows[f"actions/runs/{run_id}"]
    original = dict(run, run_attempt=1, conclusion="failure")
    run["run_attempt"] = 2
    rows[f"actions/runs/{run_id}/attempts/1"] = original
    failed = dict(lane, id=999, run_attempt=1, conclusion="failure")
    lane["run_attempt"] = 2
    artifact = rows[f"actions/runs/{run_id}/artifacts?per_page=100"]["artifacts"][0]
    # The lane passed on attempt 2: its evidence and artifact name carry that attempt.
    from scripts import focused_coverage, focused_plan

    junit = b'<testsuite><testcase classname="tests.unit.focused_gate_test" name="test_case"/></testsuite>'
    monkeypatch.setattr(type(gate), "github", lambda _gate, path: rows[path])
    source = focused_gate._pr_source(gate, run, focused_gate.pull_identity(gate, gate.head), candidate=False)
    rescope = focused_plan.resolve(source.source, source.commit, "{}", pr_base_limit=source.source.base)
    report = focused_coverage.canonical(focused_coverage.evidence(rescope, "python", run_id, 2, {"junit.xml": junit}))
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("evidence.json", report)
        archive.writestr("junit.xml", junit)
    content = output.getvalue()
    artifact.update(
        name=f"focused-pr-evidence-python-{run_id}-2",
        size_in_bytes=len(content),
        digest=f"sha256:{hashlib.sha256(content).hexdigest()}",
    )
    logs[f"actions/artifacts/{artifact['id']}/zip"] = content
    logs[f"actions/jobs/{lane['id']}/steps/6/logs"] = (
        f"Artifact {artifact['name']} has been successfully uploaded! "
        f"Final size is {len(content)} bytes. Artifact ID is {artifact['id']}\n"
    ).encode()
    every = rows[f"actions/runs/{run_id}/jobs?filter=latest&per_page=100&page=1"]["jobs"] + [failed]
    rows[f"actions/runs/{run_id}/jobs?filter=all&per_page=100&page=1"] = {"total_count": len(every), "jobs": every}
    assert focused_gate.verify(gate, run_id, manifest, logs.__getitem__) == (
        "Python coverage: not applicable to the selected scope"
    )
    assert focused_gate.rerun_ledger(gate.github, run) == [
        f"passed on rerun: Focused tests (python) (attempt 1 failure, run {run_id}, head {gate.head})"
    ]
    assert focused_gate.rerun_ledger(gate.github, original) == []


@pytest.mark.parametrize("reviewed", [False, True])
def test_a_pr_that_edits_the_workflow_needs_a_reviewed_execution_pin(
    focused_gate, first_attempt, monkeypatch, reviewed
):
    """R1: a PR's ci.yml could drop e2e, perf or Electron from its own run; the judge then demands a reviewed pin."""
    from scripts import focused_plan

    gate, run, _manifest, _lane, rows, logs, _git_rows = first_attempt
    merge, blob = "c" * 40, "e" * 40
    edited = "9" * 40

    def git_blob(commit, path):
        return edited if (commit, path) == (merge, ".github/workflows/ci.yml") else blob

    monkeypatch.setattr(focused_plan, "git_blob", git_blob)
    monkeypatch.setattr(type(gate), "github", lambda _gate, path: rows[path])
    tested = {path: git_blob(merge, path) for path in focused_plan.PROTECTED_FILES}
    plan = {"ref": focused_plan.PREOPEN_PR_REF, "selection": {"python": ["tests/unit/focused_gate_test.py"]}}
    manifest = json.dumps({f"pull_request:{gate.head}": plan | {"execution": tested}}) if reviewed else "{}"
    if reviewed:
        verdict = focused_gate.verify(gate, run, manifest, logs.__getitem__)
        assert verdict.endswith("reviewed workflow change: .github/workflows/ci.yml")
    else:
        with pytest.raises(
            focused_gate.CandidateError, match=r"workflow or its producers changed \(.github/workflows/ci.yml\)"
        ):
            focused_gate.verify(gate, run, manifest, logs.__getitem__)


def test_the_protected_list_covers_the_workflow_and_what_it_runs():
    from scripts import focused_plan

    for path in (".github/workflows/ci.yml", "Makefile", "scripts/focused_coverage.py", "scripts/focused_plan.py"):
        assert path in focused_plan.PROTECTED_FILES


@pytest.mark.parametrize(
    ("failed", "clause"),
    [([], "consumers advisory: passed"), (["acme", "zoo"], "consumers advisory: 2 failing: acme, zoo"), (None, "")],
)
def test_the_advisory_consumers_result_is_shown_in_the_verdict(focused_gate, monkeypatch, failed, clause):
    from scripts.ci_workload import FocusedPlan
    from scripts.focused_plan import Scope

    workload = FocusedPlan("pull_request", "refs/pull/1/merge", "a" * 40, "b" * 40, "c", ("Focused tests (consumers)",))
    scope = Scope(workload, "c" * 40, "d" * 40, {}, {}, "e", {}, {"consumers": ("plugins/main",)}, {}, "f")
    job = {
        "name": "Focused tests (consumers)",
        "run_attempt": 1,
        "conclusion": "success" if failed is not None else "failure",
    }
    source = focused_gate.GateSource({"id": 7}, None, "c" * 40)
    monkeypatch.setattr(focused_gate, "jobs_for", lambda _gate, _source: [job])
    name = "focused-pr-evidence-consumers-7-1"
    monkeypatch.setattr(focused_gate, "_artifact_inventory", lambda _gate, _run: {name: {}})
    report = json.dumps({"failed": failed or []}).encode()
    monkeypatch.setattr(focused_gate, "_artifact", lambda *_args: {"evidence.json": report})
    summary = focused_gate.advisory_summary(None, source, scope, lambda _path: b"")
    assert summary == (clause or "consumers advisory: did not finish")


@pytest.mark.parametrize(
    ("event", "changed", "ok"),
    [("push", "SECURITY.md", True), ("pull_request", "SECURITY.md", False), ("push", "Makefile", False)],
)
def test_a_prose_main_push_needs_no_app_jobs_in_the_full_set(focused_gate, monkeypatch, event, changed, ok):
    """S3: ci.yml runs no app jobs for a main push of prose alone; the judge must not refuse that full set."""
    from scripts.ci_workload import FocusedPlan
    from scripts.focused_plan import Scope

    workload = FocusedPlan(event, "refs/heads/main", "a" * 40, "b" * 40, "c", ("CI OK",), full=True)
    scope = Scope(workload, "b" * 40, "d" * 40, {changed: "M"}, {}, "e", {}, {}, {}, "f", full=True)
    jobs = [{"name": name, "conclusion": "success", "run_attempt": 1} for name in focused_gate.STATIC_JOBS]
    monkeypatch.setattr(focused_gate, "jobs_for", lambda _gate, _source: jobs)
    monkeypatch.setattr(focused_gate, "_check", lambda *_args: None)
    monkeypatch.setattr(focused_gate, "_artifact_inventory", lambda _gate, _run: {})
    source = focused_gate.GateSource({"id": 7, "event": event}, None, "b" * 40)
    if ok:
        focused_gate._verify_workload(None, source, scope, lambda _path: b"")
    else:
        with pytest.raises(focused_gate.CandidateError, match="did not run its matrix"):
            focused_gate._verify_workload(None, source, scope, lambda _path: b"")


def test_shared_test_helpers_and_collection_inputs_are_protected(monkeypatch):
    """S10: what runs inside a full-set job is pinned too: conftests, test helpers, pytest config, package scripts."""
    from scripts import focused_plan

    tree = {
        "c": [
            "tests/_e2e_lanes.py",
            "tests/e2e/_mounted.py",
            "tests/e2e/sub/_x.py",
            "tests/unit/_y.py",
            "tests/a_test.py",
            "conftest.py",
            "tests/conftest.py",
            "tests/integration/conftest.py",
            "tests/unit/heads/conftest.py",
            "web/conftest_notes.md",
        ],
        "b": ["tests/_e2e_lanes.py", "tests/conftest.py"],
    }
    monkeypatch.setattr(focused_plan, "tracked_paths", lambda commit: tree[commit])
    monkeypatch.setattr(focused_plan, "git_blob", lambda commit, path: f"{commit}:{path}")
    blobs = focused_plan.protected_blobs("c")
    conftests = {"conftest.py", "tests/conftest.py", "tests/integration/conftest.py", "tests/unit/heads/conftest.py"}
    assert {"tests/_e2e_lanes.py", "tests/e2e/_mounted.py", *conftests} <= blobs.keys()
    assert not {"tests/e2e/sub/_x.py", "tests/unit/_y.py", "tests/a_test.py", "web/conftest_notes.md"} & blobs.keys()
    for path in ("pyproject.toml", "tests/quarantine.toml", "web/vite.config.ts", "desktop/package.json"):
        assert path in focused_plan.PROTECTED_FILES
    assert not {"tests/e2e/_mounted.py", "tests/unit/heads/conftest.py"} & focused_plan.protected_blobs("b").keys()


def test_an_added_test_helper_needs_the_reviewed_execution_pin(focused_gate, monkeypatch):
    from scripts import focused_plan
    from scripts.ci_workload import FocusedPlan
    from scripts.focused_plan import Scope

    monkeypatch.setattr(focused_plan, "tracked_paths", lambda commit: ["tests/e2e/_new.py"] if commit == "c" else [])
    monkeypatch.setattr(focused_plan, "git_blob", lambda *_args: "e" * 40)
    workload = FocusedPlan("pull_request", "refs/pull/1/merge", "b", "h", "d", ("CI OK",), full=True)
    scope = Scope(workload, "c", "t", {}, {}, "e", {}, {}, {}, "f", full=True)
    with pytest.raises(focused_gate.CandidateError, match=r"producers changed \(tests/e2e/_new.py\)"):
        focused_gate.require_reviewed_execution(scope, {}, ("pull_request:h",))
    pinned = {"pull_request:h": {"execution": focused_plan.protected_blobs("c")}}
    assert focused_gate.require_reviewed_execution(scope, pinned, ("pull_request:h",)) == ["tests/e2e/_new.py"]


@pytest.mark.parametrize(
    ("ran", "full"),
    [
        ({"Coverage", "Test (ubuntu-24.04, Python 3.12, unit)", "CI OK"}, True),
        ({"CI OK", "Changed paths"}, True),
        ({"CI OK", "Focused tests (consumers)"}, True),
        ({"CI OK", "Focused tests (python)"}, False),
        ({"CI OK", "Focused tests (python)", "Focused tests (consumers)"}, False),
    ],
)
def test_a_run_that_went_full_is_recognised(focused_gate, ran, full):
    jobs = [{"name": name, "conclusion": "success"} for name in ran] + [{"name": "Coverage", "conclusion": "skipped"}]
    assert focused_gate.went_full(jobs) is full


def test_a_main_run_that_went_full_is_judged_full_after_a_late_proof(focused_gate, first_attempt, monkeypatch):
    """S9: run N+1 started before N's proof and took the full set; the proof is there by judging time."""
    from scripts import merge_gate

    gate, run_id, _manifest, _lane, rows, _logs, git_rows = first_attempt
    base, head = "a" * 40, gate.head
    rows[f"actions/runs/{run_id}"].update(event="push", head_branch="main", head_repository={"full_name": gate.repo})
    rows["git/ref/heads/main"] = {"object": {"sha": head}}
    rows[f"actions/workflows/{focused_gate.CI_WORKFLOW_ID}/runs?head_sha={head}&event=push&per_page=1"] = {
        "workflow_runs": [rows[f"actions/runs/{run_id}"]]
    }
    git_rows.update(
        {
            ("fetch", "--no-tags", "origin", "refs/heads/main"): b"",
            ("rev-parse", "FETCH_HEAD"): head.encode(),
            ("merge-base", base, head): base.encode(),
            ("diff-tree", "--no-commit-id", "-r", "--raw", "--no-abbrev", "--no-renames", "-z", base, head): b"",
        }
    )
    full_jobs = [{"name": "Coverage", "conclusion": "success"}, {"name": "CI OK", "conclusion": "success"}]
    monkeypatch.setattr(focused_gate, "latest_jobs", lambda _get, _run, which="latest": full_jobs)
    monkeypatch.setattr(
        focused_gate, "last_green_main", lambda _get, _head: base
    )  # the previous push, proven meanwhile
    git_rows[("merge-base", base, base)] = base.encode()
    seen = {}

    class StopError(Exception):
        pass

    def resolve(_source, _commit, _manifest, **kwargs):
        seen["selection"] = kwargs["select"]("c", [])
        raise StopError

    monkeypatch.setattr(focused_gate, "resolve", resolve)
    monkeypatch.setattr(merge_gate.Gate, "github", lambda _gate, path: rows[path])
    with pytest.raises(StopError):
        focused_gate.verify_main(gate, run_id, base, "{}", lambda _path: b"")
    assert seen["selection"] == (None, "the run took the full set")


def test_the_main_proof_race_accepts_the_full_run_that_followed(focused_gate, first_attempt, monkeypatch):
    """S9 end to end: N completes unproven, N+1 starts and goes full, N's proof appears, the judge accepts N+1."""
    from scripts import focused_coverage, focused_plan

    gate, run_id, _manifest, lane, rows, logs, git_rows = first_attempt
    base, head = "a" * 40, gate.head
    run = rows[f"actions/runs/{run_id}"]
    run.update(event="push", head_branch="main", head_repository={"full_name": gate.repo})
    rows["git/ref/heads/main"] = {"object": {"sha": head}}
    rows[f"actions/workflows/{focused_gate.CI_WORKFLOW_ID}/runs?head_sha={head}&event=push&per_page=1"] = {
        "workflow_runs": [run]
    }
    diff = git_rows[("diff-tree", "--no-commit-id", "-r", "--raw", "--no-abbrev", "--no-renames", "-z", base, "c" * 40)]
    git_rows.update(
        {
            ("fetch", "--no-tags", "origin", "refs/heads/main"): b"",
            ("rev-parse", "FETCH_HEAD"): head.encode(),
            ("merge-base", base, head): base.encode(),
            ("merge-base", "--is-ancestor", base, head): b"",
            ("merge-base", base, base): base.encode(),
            ("diff-tree", "--no-commit-id", "-r", "--raw", "--no-abbrev", "--no-renames", "-z", base, head): diff,
        }
    )
    # 1-2. When N+1 started, N (= base) had no proof yet: CI took the full set, with Coverage and the Test matrix.
    source = focused_plan.WorkloadSource("push", gate.repo, "refs/heads/main", {}, base, head, diff)
    ci_scope = focused_plan.resolve(
        source, head, "{}", select=lambda _c, _p: (None, "no green main run before this push")
    )
    assert ci_scope.full
    report = focused_coverage.canonical(focused_coverage.evidence(ci_scope, "coverage", run_id, 1, {}))
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("evidence.json", report)
    content = output.getvalue()
    jobs = rows[f"actions/runs/{run_id}/jobs?filter=latest&per_page=100&page=1"]
    lane["name"] = "Coverage"
    test_job = dict(lane, id=555, name="Test (ubuntu-24.04, Python 3.12, unit)")
    jobs["jobs"] = [job for job in jobs["jobs"] if job["name"] not in focused_gate.PR_JOBS] + [test_job]
    jobs["total_count"] = len(jobs["jobs"])
    for job in jobs["jobs"]:
        rows[f"check-runs/{job['id']}"] = {
            **job,
            "app": {"id": focused_gate.ACTIONS_APP_ID},
            "check_suite": {"id": run["check_suite_id"]},
        }
    artifact = rows[f"actions/runs/{run_id}/artifacts?per_page=100"]["artifacts"][0]
    artifact.update(
        name=f"focused-pr-evidence-coverage-{run_id}-1",
        size_in_bytes=len(content),
        digest=f"sha256:{hashlib.sha256(content).hexdigest()}",
    )
    logs[f"actions/artifacts/{artifact['id']}/zip"] = content
    logs[f"actions/jobs/{lane['id']}/steps/6/logs"] = (
        f"Artifact {artifact['name']} has been successfully uploaded! "
        f"Final size is {len(content)} bytes. Artifact ID is {artifact['id']}\n"
    ).encode()
    # 3. N's proof appears before N+1 is judged: the judge's own selection for this diff is now focused.
    rows["actions/workflows/ci.yml/runs?branch=main&event=push&status=success&per_page=30"] = {
        "workflow_runs": [{"id": 77, "head_sha": base}]
    }
    rows["actions/runs/77/jobs?filter=latest&per_page=100"] = {"jobs": [{"name": "CI OK", "conclusion": "success"}]}
    rows[f"commits/{base}/statuses?per_page=100"] = [{"context": "hz/main-proof", "state": "success"}]
    monkeypatch.setattr(type(gate), "github", lambda _gate, path: rows[path])
    assert not focused_plan.resolve(source, head, "{}").full
    # 4. The judge accepts N+1 as the full set it ran.
    proof = focused_gate.verify_main(gate, run_id, base, "{}", logs.__getitem__)
    assert (proof["full"], proof["head"], proof["base"]) == (True, head, base)


def test_a_prose_tip_after_an_untested_code_push_gets_no_main_proof(focused_gate, first_attempt, monkeypatch):
    """R4: C1 (code, its run replaced or red), then C2 (prose). C2's run has no app jobs, so it counts as full. Judged
    from base C1, newer than the last accepted green C0, it would pass as prose; C1's code was never tested."""
    gate, run_id, _manifest, lane, rows, logs, git_rows = first_attempt
    c0, c1, c2 = "1" * 40, "a" * 40, gate.head
    run = rows[f"actions/runs/{run_id}"]
    run.update(event="push", head_branch="main", head_repository={"full_name": gate.repo})
    rows["git/ref/heads/main"] = {"object": {"sha": c2}}
    rows[f"actions/workflows/{focused_gate.CI_WORKFLOW_ID}/runs?head_sha={c2}&event=push&per_page=1"] = {
        "workflow_runs": [run]
    }
    jobs = rows[f"actions/runs/{run_id}/jobs?filter=latest&per_page=100&page=1"]
    jobs["jobs"] = [job for job in jobs["jobs"] if job["name"] not in {*focused_gate.PR_JOBS, lane["name"]}]
    jobs["total_count"] = len(jobs["jobs"])
    prose = f":100644 100644 {c1} {'e' * 40} M\0README.md\0".encode()
    git_rows.update(
        {
            ("fetch", "--no-tags", "origin", "refs/heads/main"): b"",
            ("rev-parse", "FETCH_HEAD"): c2.encode(),
            ("merge-base", c1, c2): c1.encode(),
            ("merge-base", c1, c0): c0.encode(),  # C1 is newer than the last accepted green C0
            ("diff-tree", "--no-commit-id", "-r", "--raw", "--no-abbrev", "--no-renames", "-z", c1, c2): prose,
        }
    )
    monkeypatch.setattr(focused_gate, "last_green_main", lambda _get, _head: c0)
    monkeypatch.setattr(type(gate), "github", lambda _gate, path: rows[path])
    assert focused_gate.went_full(jobs["jobs"])
    with pytest.raises(focused_gate.CandidateError, match="newer than the last accepted green"):
        focused_gate.verify_main(gate, run_id, c1, "{}", logs.__getitem__)
