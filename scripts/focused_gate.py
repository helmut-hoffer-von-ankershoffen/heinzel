# /// script
# requires-python = ">=3.15"
# dependencies = ["coverage==7.17.0"]
# ///
"""Judge exact focused CI evidence from the protected default-branch workflow."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from scripts.ci_workload import WorkloadSource
from scripts.focused_coverage import MAX_FILE, REPORT, canonical, config_policy, evidence, parse
from scripts.focused_plan import (
    ARTIFACT_PREFIX,
    LANES,
    MAX_PLAN,
    PLAN_VARIABLE,
    PROTECTED_FILES,
    Scope,
    git,
    git_blob,
    raw_diff,
    registry_slots,
    require_pr_base_ancestor,
    resolve,
    selected_registry,
    unique_object,
    web_production,
)
from scripts.merge_gate import ARTIFACT, GITHUB, Gate, fetch_evidence, red_below, whole_total
from scripts.release_candidate import (
    REPOSITORY,
    WORKFLOW,
    CandidateError,
    expected_jobs,
    job_inventory,
    quarantine_lane,
    release_version,
    require,
    valid_sha,
)

if TYPE_CHECKING:
    from collections.abc import Callable

ACTIONS_APP_ID = 15368
CI_WORKFLOW_ID = 394704163
MAX_ARTIFACTS = 100
MAX_ARCHIVE = 5 * 1024 * 1024
REMOTE_FIELDS = 2
MAX_UPLOAD_LOG = 64 * 1024
FULL_EXECUTION_INPUTS = (*PROTECTED_FILES, "Makefile", "pyproject.toml")
UPLOAD_NAME = "Upload focused CI evidence"
CANDIDATE_UPLOAD_NAME = "Run actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9"
STATIC_JOBS = {
    "Main batch admission",
    "Changed paths",
    "Lint, contracts and types",
    "Secret scan",
    "Dependency audit",
    "Quarantine registry",
    "CI OK",
}
PR_JOBS = {"Conventional commits", "Dependency review"}
API_CHECKS = {"merge-gate", "coverage-floor"}
CLAUDE_REVIEW = "Claude review"
UPLOAD = re.compile(
    r"(?:[0-9T:.Z-]+ )?Artifact (focused-pr-evidence-[a-z0-9-]+|coverage-total) has been successfully uploaded! "
    r"Final size is ([1-9][0-9]{0,9}) bytes\. Artifact ID is ([1-9][0-9]{0,19})"
)


@dataclass(frozen=True)
class GateSource:
    """GitHub's original workload attempt and the exact source it tested."""

    run: dict[str, Any]
    source: WorkloadSource
    commit: str


def _positive(value: object) -> bool:
    return type(value) is int and value > 0


def pull_identity(gate: Gate, head: str) -> tuple[dict[str, Any], str, str]:
    """Read the current PR, its actual merge ref and protected main independently."""
    number = int(gate.pr)
    pr = gate.github(f"pulls/{number}")
    base = gate.github("git/ref/heads/main")["object"]["sha"]
    require(
        pr["number"] == number
        and pr["state"] == "open"
        and pr["draft"] is False
        and pr["base"]["repo"]["full_name"] == REPOSITORY
        and pr["base"]["ref"] == "main"
        and valid_sha(pr["base"]["sha"])
        and pr["head"]["sha"] == head
        and valid_sha(base)
        and valid_sha(head),
        "PR or protected base changed",
    )
    merge_ref = f"refs/pull/{number}/merge"
    fields = git("ls-remote", "--exit-code", "origin", merge_ref).decode().split()
    require(
        len(fields) == REMOTE_FIELDS and valid_sha(fields[0]) and fields[1] == merge_ref,
        "missing current GitHub test merge",
    )
    require(pr.get("merge_commit_sha") in (None, fields[0]), "GitHub test merge changed")
    return pr, fields[0], base


def _authenticated_run(gate: Gate, run_id: int, *, candidate_composite: bool = False) -> dict[str, Any]:
    require(run_id > 0 and gate.repo == REPOSITORY and valid_sha(gate.head), "invalid workflow event identity")
    run = gate.github(f"actions/runs/{run_id}")
    original = gate.github(f"actions/runs/{run_id}/attempts/1")
    attempt = run["run_attempt"]
    require(
        _positive(attempt)
        and original["run_attempt"] == 1
        and all(
            item["id"] == run_id
            and item["head_sha"] == gate.head
            and item["workflow_id"] == CI_WORKFLOW_ID
            and item["path"] == WORKFLOW
            and item["repository"]["full_name"] == REPOSITORY
            and item["status"] == "completed"
            for item in (run, original)
        )
        and run["conclusion"] == "success"
        and original["event"] == run["event"]
        and _positive(run["check_suite_id"])
        and original["check_suite_id"] == run["check_suite_id"],
        "missing completed evidence from the canonical CI workflow",
    )
    # Capacity recovery is judged separately by the release verifier. Even in a
    # canonical composite, this judge reads Coverage from the original attempt.
    if attempt == 1:
        require(original["conclusion"] == "success", "original workflow attempt was unsuccessful")
    else:
        require(
            candidate_composite and original["conclusion"] in {"cancelled", "failure"},
            "ordinary focused CI requires a successful original attempt",
        )
    latest = gate.github(
        f"actions/workflows/{CI_WORKFLOW_ID}/runs?head_sha={gate.head}&event={run['event']}&per_page=1"
    )["workflow_runs"]
    require(len(latest) == 1 and latest[0]["id"] == run_id, "a newer workflow run superseded this evidence")
    return run


def _pr_source(
    gate: Gate, run: dict[str, Any], snapshot: tuple[dict[str, Any], str, str], *, candidate: bool
) -> GateSource:
    pr, merge, base = snapshot
    event = run["event"]
    head = gate.head
    require(
        event in ("pull_request", "workflow_dispatch")
        and run["head_repository"]["id"] == pr["head"]["repo"]["id"]
        and run["head_branch"] == pr["head"]["ref"],
        "run does not belong to this pull request",
    )
    require(not candidate or pr["base"]["sha"] == base, "release PR or protected base changed")
    if event == "pull_request":
        ref = f"refs/pull/{gate.pr}/merge"
        associated = run["pull_requests"]
        require(
            (not associated and pr["head"]["repo"]["fork"])
            or (
                len(associated) == 1
                and associated[0]["number"] == pr["number"]
                and associated[0]["head"]["sha"] == head
            ),
            "workflow run association does not bind the exact PR and head",
        )
        git("fetch", "--no-tags", "origin", ref)
        require(git("rev-parse", "FETCH_HEAD").decode().strip() == merge, "GitHub test merge moved during fetch")
        if associated:
            reported = associated[0]["base"]
            if candidate:
                require(reported["sha"] == base, "release workflow run association does not bind the exact PR base")
            else:
                require(
                    reported["ref"] == pr["base"]["ref"] and reported["repo"]["id"] == pr["base"]["repo"]["id"],
                    "workflow run association does not target protected main",
                )
                require_pr_base_ancestor(reported["sha"], base)
    else:
        require(candidate, "ordinary pull requests require their original PR CI run")
        ref, merge = f"refs/heads/{pr['head']['ref']}", head
        git("fetch", "--no-tags", "origin", head)
    payload = {"pull_request": pr}
    return GateSource(run, WorkloadSource(event, REPOSITORY, ref, payload, base, head, raw_diff(base, merge)), merge)


def _is_published_check(gate: Gate, source: GateSource, job: dict[str, Any]) -> bool:
    # GitHub can list API-created checks on the PR head alongside Actions jobs.
    # Match their exact runner-less shape, as the release evidence reader does.
    url = job.get("html_url")
    run_id = source.run["id"]
    if not (
        _positive(job.get("id"))
        and job.get("runner_id") is None
        and job.get("runner_name") is None
        and job.get("steps") == []
        and job.get("labels") == []
        and job.get("run_id") == run_id
        and url == f"https://github.com/{REPOSITORY}/runs/{job['id']}"
        and f"/actions/runs/{run_id}/job/" not in str(job.get("check_run_url", "")) + str(url)
    ):
        return False
    if job.get("name") in API_CHECKS:
        return True
    if not (
        source.run["event"] == "pull_request"
        and job.get("name") == CLAUDE_REVIEW
        and job.get("status") == "completed"
        and job.get("conclusion") == "neutral"
        and "runner_id" in job
        and "runner_name" in job
        and type(job.get("run_attempt")) is int
        and job["run_attempt"] == 1
        and job.get("head_sha") == source.source.head
        and job.get("check_run_url") == f"{GITHUB}/repos/{REPOSITORY}/check-runs/{job['id']}"
    ):
        return False
    # The advisory workflow creates a neutral API check when no review ran.
    # Authenticate that check independently before excluding it from the CI jobs.
    check = gate.github(f"check-runs/{job['id']}")
    return (
        check.get("id") == job["id"]
        and check.get("name") == CLAUDE_REVIEW
        and check.get("head_sha") == source.source.head
        and check.get("status") == "completed"
        and check.get("conclusion") == "neutral"
        and check.get("html_url") == check.get("details_url") == url
        and check.get("app", {}).get("id") == ACTIONS_APP_ID
        and check.get("check_suite", {}).get("id") == source.run["check_suite_id"]
    )


def jobs_for(gate: Gate, source: GateSource) -> list[dict[str, Any]]:
    """Bind each job to the original attempt, exact source and genuine Actions suite."""
    run = source.run
    suite = gate.github(f"check-suites/{run['check_suite_id']}")
    # A pending API check can change the suite's aggregate. The original Actions
    # attempt and its individual workload checks supply execution conclusions.
    require(
        suite["id"] == run["check_suite_id"]
        and suite["app"]["id"] == ACTIONS_APP_ID
        and suite["head_sha"] == source.source.head,
        "unexpected GitHub Actions check suite",
    )
    raw_jobs = job_inventory(gate.github, run["id"])
    jobs = [job for job in raw_jobs if not _is_published_check(gate, source, job)]
    for job in jobs:
        require(
            _positive(job["id"])
            and job.get("run_id") == run["id"]
            and job.get("run_attempt") == 1
            and job.get("head_sha") == source.source.head
            and job.get("status") == "completed",
            "job identity or attempt mismatch",
        )
    return jobs


def _check(gate: Gate, source: GateSource, job: dict[str, Any]) -> None:
    check = gate.github(f"check-runs/{job['id']}")
    require(
        check["id"] == job["id"]
        and check["name"] == job["name"]
        and check["head_sha"] == source.source.head
        and check["status"] == "completed"
        and check["conclusion"] == job["conclusion"] == "success"
        and check["app"]["id"] == ACTIONS_APP_ID
        and check["check_suite"]["id"] == source.run["check_suite_id"]
        and _positive(job["runner_id"])
        and job["steps"],
        f"required job has no authentic successful execution: {job['name']}",
    )


def _artifact_inventory(gate: Gate, run: int) -> dict[str, dict[str, Any]]:
    found = gate.github(f"actions/runs/{run}/artifacts?per_page={MAX_ARTIFACTS}")
    artifacts = found["artifacts"]
    require(
        len(artifacts) == found["total_count"] < MAX_ARTIFACTS
        and len({item["id"] for item in artifacts}) == len(artifacts),
        "incomplete or duplicated focused artifact inventory",
    )
    require(
        all(item["name"].startswith(f"{ARTIFACT_PREFIX}-") and item["workflow_run"]["id"] == run for item in artifacts),
        "unexpected artifact in focused CI",
    )
    names = {item["name"]: item for item in artifacts}
    require(len(names) == len(artifacts), "duplicate focused artifact name")
    return names


def _verified_archive(
    read: Callable[[str], bytes],
    job: dict[str, Any],
    artifact: dict[str, Any],
    name: str,
    upload_name: str,
) -> bytes:
    steps = job["steps"]
    uploads = [step for step in steps if step["name"] == upload_name]
    require(len(uploads) == 1, "missing unique focused upload step")
    step = uploads[0]
    require(
        _positive(step["number"])
        and step["status"] == "completed"
        and step["conclusion"] == "success"
        and sum(row["number"] == step["number"] for row in steps) == 1,
        "focused upload step did not succeed",
    )
    log = read(f"actions/jobs/{job['id']}/steps/{step['number']}/logs")
    require(len(log) <= MAX_UPLOAD_LOG, "focused upload step log is oversized")
    records = [match.groups() for line in log.decode().splitlines() if (match := UPLOAD.fullmatch(line))]
    artifact_id, size = artifact["id"], artifact["size_in_bytes"]
    require(
        _positive(artifact_id)
        and _positive(size)
        and size <= MAX_ARCHIVE
        and artifact["expired"] is False
        and records == [(name, str(size), str(artifact_id))],
        "focused upload does not bind the live immutable artifact",
    )
    content = read(f"actions/artifacts/{artifact_id}/zip")
    require(
        len(content) == size and artifact["digest"] == f"sha256:{hashlib.sha256(content).hexdigest()}",
        "focused artifact archive digest or size mismatch",
    )
    return content


def _artifact(
    read: Callable[[str], bytes],
    job: dict[str, Any],
    artifact: dict[str, Any],
    name: str,
) -> dict[str, bytes]:
    content = _verified_archive(read, job, artifact, name, UPLOAD_NAME)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        files = archive.infolist()
        require(
            len(files) in (2, 3)
            and len({entry.filename for entry in files}) == len(files)
            and all(0 < entry.file_size <= MAX_FILE for entry in files),
            "focused artifact entries are incomplete, duplicated or oversized",
        )
        return {entry.filename: archive.read(entry) for entry in files}


def _verify_workload(
    gate: Gate,
    source: GateSource,
    scope: Scope,
    read: Callable[[str], bytes],
) -> None:
    jobs = jobs_for(gate, source)
    expected = STATIC_JOBS | set(scope.workload.jobs)
    if source.run["event"] == "pull_request":
        expected |= PR_JOBS
    names = {job["name"]: job for job in jobs}
    marker = f"Main batch focused scope ({source.source.base})"
    if source.run["event"] == "push" and marker in names:
        expected.add(marker)
    require(
        len(names) == len(jobs) and {name for name, job in names.items() if job["conclusion"] != "skipped"} == expected,
        "focused workload job inventory is missing, unexpected or skipped",
    )
    for name in sorted(expected):
        _check(gate, source, names[name])
    run = source.run["id"]
    artifacts = _artifact_inventory(gate, run)
    by_lane = {lane: f"{ARTIFACT_PREFIX}-{lane}-{run}-1" for lane in scope.selection}
    require(set(artifacts) == set(by_lane.values()), "focused artifact lane inventory mismatch")
    for lane, name in by_lane.items():
        files = _artifact(read, names[f"Focused tests ({lane})"], artifacts[name], name)
        recorded = parse(files.pop(REPORT))
        require(
            canonical(recorded) == canonical(evidence(scope, lane, run, 1, files)),
            "focused typed evidence differs from exact source or recorded tests",
        )


def _verify_config(scope: Scope) -> None:
    config = config_policy(git("cat-file", "blob", scope.config_blob))
    require(
        config == config_policy(git("show", f"{scope.workload.base}:pyproject.toml")),
        "focused coverage policy changed and requires protected review",
    )


def _tested_source_after_main_moves(
    gate: Gate, source: GateSource, manifest: str, current_main: str, read: Callable[[str], bytes]
) -> GateSource:
    """Recover only an artifact-authenticated candidate; the detached plan still judges it."""
    require(len(manifest.encode()) <= MAX_PLAN, "focused plan registry exceeds its supported size")
    registry = json.loads(manifest or "{}", object_pairs_hook=unique_object)
    plan = registry.get(f"pull_request:{source.source.head}") if isinstance(registry, dict) else None
    require(isinstance(plan, dict) and isinstance(plan.get("selection"), dict), "missing reviewed focused plan")
    lanes = plan["selection"]
    require(lanes and set(lanes) <= set(LANES), "invalid focused lane inventory")
    base = plan.get("base")
    require(valid_sha(base), "invalid reviewed tested base")
    git("fetch", "--no-tags", "origin", base)
    require_pr_base_ancestor(base, current_main)
    jobs = jobs_for(gate, source)
    names = {job["name"]: job for job in jobs}
    expected = STATIC_JOBS | PR_JOBS | {f"Focused tests ({lane})" for lane in lanes}
    require(
        len(names) == len(jobs) and {n for n, j in names.items() if j["conclusion"] != "skipped"} == expected,
        "focused workload job inventory is missing, unexpected or skipped",
    )
    for name in sorted(expected):
        _check(gate, source, names[name])
    artifacts = _artifact_inventory(gate, source.run["id"])
    by_lane = {lane: f"{ARTIFACT_PREFIX}-{lane}-{source.run['id']}-1" for lane in lanes}
    require(set(artifacts) == set(by_lane.values()), "focused artifact lane inventory mismatch")
    commits = set()
    for lane, name in by_lane.items():
        files = _artifact(read, names[f"Focused tests ({lane})"], artifacts[name], name)
        commits.add(parse(files[REPORT]).get("commit"))
    require(len(commits) == 1, "focused artifacts disagree on tested commit")
    commit = commits.pop()
    require(valid_sha(commit), "invalid artifact tested commit")
    git("fetch", "--no-tags", "origin", commit)
    require(git("rev-parse", "FETCH_HEAD").decode().strip() == commit, "tested commit fetch changed")
    recovered = WorkloadSource(
        source.source.event,
        source.source.repository,
        source.source.ref,
        source.source.payload,
        base,
        source.source.head,
        raw_diff(base, commit),
    )
    return GateSource(source.run, recovered, commit)


def full_checkout(log: bytes) -> str:
    """Read the unique checkout action's actual commit, never a branch name or inferred head."""
    matches = re.findall(
        rb"(?m)^\d{4}-[^ \r\n]+ \[command\][^\r\n]+/git log -1 --format=%H\r?\n"
        rb"\d{4}-[^ \r\n]+ ([0-9a-f]{40})\r?$",
        log,
    )
    require(len(log) <= 1024 * 1024 and len(matches) == 1, "missing or ambiguous full CI checkout identity")
    return matches[0].decode()


def _full_pr_source(source: GateSource, base: str) -> None:
    """Bind the full PR workload to protected execution inputs and the exact test merge."""
    require(source.run["event"] == "pull_request", "full PR evidence requires the PR event")
    associated = source.run["pull_requests"]
    require(len(associated) == 1 and associated[0]["base"]["sha"] == base, "full PR evidence has a stale tested base")
    require(
        all(git_blob(source.commit, path) == git_blob(base, path) for path in FULL_EXECUTION_INPUTS),
        "full PR execution inputs differ from protected main",
    )
    workflow = git("show", f"{base}:{WORKFLOW}")
    require(
        b"name: Run the complete CI workload without an external admission plan" in workflow
        and b"run: echo 'selected=true' >> \"$GITHUB_OUTPUT\"" in workflow,
        "protected main does not select unconditional full CI",
    )
    require(
        git("rev-list", "--parents", "-n", "1", source.commit).decode().split()
        == [source.commit, base, source.source.head],
        "full CI merge parents differ from the exact base and head",
    )


def full_pr_jobs(gate: Gate, source: GateSource, base: str, read: Callable[[str], bytes]) -> list[dict[str, Any]]:
    """Verify ordinary full CI without inventing a focused plan or release-candidate identity."""
    _full_pr_source(source, base)
    jobs = quarantine_lane(jobs_for(gate, source), source.run["id"], source.source.head)
    inventory = expected_jobs("pull_request")
    inventory["Release candidate source"] = "skipped"  # ordinary full PRs are not release candidates
    require(
        len(jobs) == len(inventory) and {job["name"] for job in jobs} == set(inventory), "full PR job inventory differs"
    )
    for job in jobs:
        require(job["conclusion"] == inventory[job["name"]], "full PR job did not meet its required conclusion")
        if job["conclusion"] == "success":
            _check(gate, source, job)
    changed = next(job for job in jobs if job["name"] == "Changed paths")
    log = read(f"actions/jobs/{changed['id']}/logs")
    require(full_checkout(log) == source.commit, "full CI tested another merge commit")
    return jobs


CAPACITY_NOT_ACQUIRED = "The job was not acquired by Runner of type hosted even after multiple attempts"
CAPACITY_NOTICE = "Due to capacity constraints, jobs targeting macOS arm64 runners may experience longer queue times."
CAPACITY_ATTEMPT = 2
_EXECUTION_FIELDS = (
    "name",
    "run_id",
    "head_sha",
    "status",
    "conclusion",
    "runner_id",
    "runner_name",
    "labels",
    "started_at",
    "completed_at",
    "steps",
)
_NOTIFIER_BUILD_STEPS = {
    "Run actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333",
    "Unpack Heinzel Notifier into the package",
    "Check the wheel ships Heinzel Notifier",
}
_RECOVERY_STEPS = {
    "Test (macos-latest, Python 3.15, unit)": {
        "Unit and integration tests (unit first, 4 workers, stop at the first failure)"
    },
    "make setup (macos-latest)": {"Run make setup", "Run make doctor"},
    "Heinzel Notifier (macOS app)": {
        "Run make notifier",
        "Real launches leave no process behind (ADR 0043)",
        "Pack the app, keeping its executable bit and signature",
        CANDIDATE_UPLOAD_NAME,
    },
    **{
        f"End-to-end {suite} (macos-latest)": {f"Run make test-e2e SUITE={suite}"}
        | ({"Install browser runtimes and host dependencies"} if suite != "cli" else set())
        for suite in ("cli", "supervised", "web-1", "web-2", "web-3")
    },
    "CI OK": {"Fail if any required job failed", "Require the complete direct CI workload"},
    "Build": _NOTIFIER_BUILD_STEPS | {"Run make build", CANDIDATE_UPLOAD_NAME},
}


def _capacity_annotations(gate: Gate, source: GateSource, job: dict[str, Any]) -> None:
    require(
        job.get("check_run_url") == f"{GITHUB}/repos/{REPOSITORY}/check-runs/{job['id']}",
        "capacity annotation job identity differs",
    )
    check = gate.github(f"check-runs/{job['id']}")
    require(
        check.get("id") == job["id"]
        and check.get("name") == job["name"]
        and check.get("head_sha") == source.source.head
        and check.get("status") == "completed"
        and check.get("conclusion") == "cancelled"
        and check.get("app", {}).get("id") == ACTIONS_APP_ID
        and check.get("check_suite", {}).get("id") == source.run["check_suite_id"],
        "capacity cancellation check is not authentic",
    )
    rows = gate.github(f"check-runs/{job['id']}/annotations?per_page=100&page=1")
    require(isinstance(rows, list) and 0 < len(rows) < MAX_ARTIFACTS, "missing or unbounded capacity annotations")
    allowed = {("failure", CAPACITY_NOT_ACQUIRED), ("notice", CAPACITY_NOTICE)}
    require(
        all(
            isinstance(a, dict)
            and a.get("path") == ".github"
            and a.get("blob_href") == f"https://github.com/{REPOSITORY}/blob/{source.source.head}/.github"
            and (a.get("annotation_level"), a.get("message")) in allowed
            for a in rows
        )
        and {(a.get("annotation_level"), a.get("message")) for a in rows} == allowed,
        "capacity annotations are incomplete or unexpected",
    )


def _successful_steps(job: dict[str, Any]) -> None:
    require(
        _positive(job.get("runner_id"))
        and isinstance(job.get("runner_name"), str)
        and job["runner_name"]
        and isinstance(job.get("steps"), list)
        and job["steps"]
        and all(
            isinstance(step, dict)
            and step.get("status") == "completed"
            and step.get("conclusion") in {"success", "skipped"}
            for step in job["steps"]
        ),
        "successful workload has missing or failed steps",
    )


def _recovery_inventory(gate: Gate, source: GateSource) -> list[dict[str, Any]]:
    def second(path: str) -> Any:  # noqa: ANN401 - mirrors the existing GitHub transport
        return gate.github(path.replace("/attempts/1/jobs?", "/attempts/2/jobs?"))

    raw = job_inventory(second, source.run["id"])
    jobs = [job for job in raw if not _is_published_check(gate, source, job)]
    require(
        all(
            _positive(j.get("id"))
            and j.get("run_id") == source.run["id"]
            and j.get("head_sha") == source.source.head
            and j.get("run_attempt") == CAPACITY_ATTEMPT
            and j.get("status") == "completed"
            for j in jobs
        ),
        "recovered job identity differs",
    )
    return jobs


def _unchanged_carrier(old: dict[str, Any], job: dict[str, Any]) -> None:
    if old.get("conclusion") == "skipped":
        runner = (old.get("runner_id"), old.get("runner_name"))
        require(
            (runner == (None, None) or (type(runner[0]) is int and runner == (0, "")))
            and (job.get("runner_id"), job.get("runner_name")) == runner
            and old.get("steps") == job.get("steps") == []
            and all(
                job.get(key) == old.get(key) for key in _EXECUTION_FIELDS if key not in {"started_at", "completed_at"}
            ),
            "skipped carrier acquired execution",
        )
    else:
        require(all(job.get(key) == old.get(key) for key in _EXECUTION_FIELDS), "original execution changed")


def verify_full_pr_capacity(gate: Gate, run_id: int, read: Callable[[str], bytes]) -> dict[str, Any]:  # noqa: C901, PLR0915 - one continuous evidence boundary
    """Verify a separate capacity-recovered full PR proof, never release-candidate evidence."""
    snapshot = pull_identity(gate, gate.head)
    require(
        not release_version("pull_request", REPOSITORY, "", {"pull_request": snapshot[0]}),
        "release candidates cannot use PR capacity proof",
    )
    run = _authenticated_run(gate, run_id, candidate_composite=True)
    require(run["run_attempt"] == CAPACITY_ATTEMPT and run["event"] == "pull_request", "requires the second PR attempt")
    runs = gate.github(f"actions/workflows/{CI_WORKFLOW_ID}/runs?head_sha={gate.head}&event=pull_request&per_page=2")
    require(
        runs.get("total_count") == 1
        and len(runs.get("workflow_runs", [])) == 1
        and runs["workflow_runs"][0]["id"] == run_id,
        "ambiguous PR workflow runs",
    )
    source = _pr_source(gate, run, snapshot, candidate=False)
    _full_pr_source(source, snapshot[2])
    original_all = jobs_for(gate, source)
    original = quarantine_lane(original_all, run_id, gate.head)
    inventory = expected_jobs("pull_request")
    inventory["Release candidate source"] = "skipped"
    first = {j["name"]: j for j in original}
    require(
        len(first) == len(original) == len(inventory) and set(first) == set(inventory),
        "original PR inventory is incomplete or duplicated",
    )
    recovered_all = _recovery_inventory(gate, source)
    before = {j["name"]: j for j in original_all}
    after = {j["name"]: j for j in recovered_all}
    require(
        len(before) == len(original_all) == len(after) == len(recovered_all) and set(before) == set(after),
        "recovered PR inventory is incomplete or duplicated",
    )
    missing = set()
    for name, job in first.items():
        if inventory[name] == "skipped":
            require(job.get("conclusion") == "skipped" and job.get("steps") == [], "skipped job executed")
        elif job.get("conclusion") == "success":
            _check(gate, source, job)
            _successful_steps(job)
        elif name == "CI OK":
            check = gate.github(f"check-runs/{job['id']}")
            require(
                check.get("id") == job["id"]
                and check.get("name") == name
                and check.get("head_sha") == gate.head
                and check.get("app", {}).get("id") == ACTIONS_APP_ID
                and check.get("check_suite", {}).get("id") == run["check_suite_id"]
                and check.get("status") == "completed"
                and check.get("conclusion") == job.get("conclusion") == "failure"
                and _positive(job.get("runner_id"))
                and isinstance(job.get("steps"), list)
                and all(
                    s.get("status") == "completed" and s.get("conclusion") in {"success", "skipped", "failure"}
                    for s in job["steps"]
                )
                and {s.get("name") for s in job["steps"] if s.get("conclusion") == "failure"}
                == {"Fail if any required job failed"},
                "unexpected original aggregate failure",
            )
        else:
            require(
                name in _RECOVERY_STEPS
                and job.get("conclusion") == "cancelled"
                and type(job.get("runner_id")) is int
                and job["runner_id"] == 0
                and job.get("runner_name") == ""
                and job.get("steps") == []
                and job.get("labels") == ["macos-latest"],
                "job was not an unacquired macOS runner",
            )
            _capacity_annotations(gate, source, job)
            missing.add(name)
    require(missing and first["CI OK"]["conclusion"] == "failure", "no capacity recovery to prove")
    replacements = missing | {"CI OK"}
    if "Heinzel Notifier (macOS app)" in missing:
        replacements.add("Build")
        require(
            {s["name"] for s in first["Build"]["steps"] if s.get("conclusion") == "skipped"} == _NOTIFIER_BUILD_STEPS,
            "original Build closure differs",
        )
    for name, job in after.items():
        old = before[name]
        if name in replacements:
            require(
                job["id"] not in {entry["id"] for entry in original_all} and job.get("conclusion") == "success",
                "missing successful replacement",
            )
            _check(gate, source, job)
            _successful_steps(job)
            require(
                all(
                    sum(s.get("name") == step and s.get("conclusion") == "success" for s in job["steps"]) == 1
                    for step in _RECOVERY_STEPS[name]
                ),
                "recovered workload did not run its required steps",
            )
            if name != "CI OK":
                require(
                    full_checkout(read(f"actions/jobs/{job['id']}/logs")) == source.commit,
                    "recovered workload checkout differs",
                )
        else:
            _unchanged_carrier(old, job)
    if "Build" in replacements:
        require(
            all(
                sum(s.get("name") == name and s.get("conclusion") == "success" for s in after["Build"]["steps"]) == 1
                for name in _NOTIFIER_BUILD_STEPS
            ),
            "recovered Build lacks notifier checks",
        )
    changed = first["Changed paths"]
    require(full_checkout(read(f"actions/jobs/{changed['id']}/logs")) == source.commit, "full CI checkout differs")
    coverage = first["Coverage"]
    artifacts = gate.github(f"actions/runs/{run_id}/artifacts?name={ARTIFACT}")
    require(
        artifacts["total_count"] == len(artifacts["artifacts"]) == 1
        and artifacts["artifacts"][0]["name"] == ARTIFACT
        and artifacts["artifacts"][0]["workflow_run"]["id"] == run_id,
        "original coverage artifact missing",
    )
    total = whole_total(_verified_archive(read, coverage, artifacts["artifacts"][0], ARTIFACT, CANDIDATE_UPLOAD_NAME))
    require(total >= red_below(Path(WORKFLOW).read_text()), "original coverage below floor")
    tree = git("rev-parse", f"{gate.head}^{{tree}}").decode().strip()
    require(valid_sha(tree), "invalid PR tree")
    require(
        pull_identity(gate, gate.head) == snapshot
        and _authenticated_run(gate, run_id, candidate_composite=True) == run
        and jobs_for(gate, source) == original_all
        and _recovery_inventory(gate, source) == recovered_all,
        "PR or run changed during recovery verification",
    )
    for name in missing:
        _capacity_annotations(gate, source, first[name])
    return {
        "type": "full-pr-capacity/v1",
        "head": gate.head,
        "base": snapshot[2],
        "tree": tree,
        "run": run_id,
        "attempt": 2,
        "original_attempt": 1,
    }


def verify(gate: Gate, run_id: int, manifest: str, read: Callable[[str], bytes], *, slots: tuple[str, ...] = ()) -> str:
    """Return a typed verdict for the completed original CI attempt, without publishing checks."""
    snapshot = pull_identity(gate, gate.head)
    candidate = bool(release_version("pull_request", REPOSITORY, "", {"pull_request": snapshot[0]}))
    if not candidate:
        manifest = selected_registry(manifest, slots, f"pull_request:{gate.head}")
    full = not candidate and not json.loads(manifest, object_pairs_hook=unique_object)
    run = _authenticated_run(gate, run_id, candidate_composite=candidate)
    source = _pr_source(gate, run, snapshot, candidate=candidate)
    if (
        not candidate
        and not full
        and json.loads(manifest, object_pairs_hook=unique_object).get(f"pull_request:{gate.head}", {}).get("base")
        != snapshot[2]
    ):
        source = _tested_source_after_main_moves(gate, source, manifest, snapshot[2], read)
    full_jobs = full_pr_jobs(gate, source, snapshot[2], read) if full else None
    scope = None
    if not full:
        scope = resolve(
            source.source,
            source.commit,
            manifest,
            source.source.payload["pull_request"],
            pr_base_limit=None if candidate else snapshot[2],
        )
    if not isinstance(scope, Scope):
        jobs = [
            job for job in (full_jobs if full_jobs is not None else jobs_for(gate, source)) if job["name"] == "Coverage"
        ]
        require(len(jobs) == 1, "canonical candidate requires the exact Coverage job")
        _check(gate, source, jobs[0])
        response = gate.github(f"actions/runs/{run_id}/artifacts?name={ARTIFACT}")
        artifacts = response["artifacts"]
        require(
            response["total_count"] == len(artifacts) == 1
            and artifacts[0]["name"] == ARTIFACT
            and artifacts[0]["workflow_run"]["id"] == run_id,
            "candidate needs one coverage artifact from its own attempt",
        )
        total = whole_total(_verified_archive(read, jobs[0], artifacts[0], ARTIFACT, CANDIDATE_UPLOAD_NAME))
        require(total >= red_below(Path(WORKFLOW).read_text()), "full candidate coverage is below its floor")
        verdict = (
            "Whole-project coverage measured by full PR CI"
            if full
            else "Whole-project coverage measured by the full release candidate"
        )
    else:
        require(
            all(git_blob(source.commit, path) == scope.execution[path] for path in PROTECTED_FILES),
            "focused workflow or producer does not match the detached review",
        )
        _verify_config(scope)
        _verify_workload(gate, source, scope, read)
        verdict = (
            "PR scoped coverage passed; whole-project validation deferred to release candidate"
            if scope.production
            else "Python coverage: not applicable; web production changed"
            if any(web_production(path) for path in scope.changed)
            else "Python coverage: not applicable to the reviewed scope"
        )
    current, merge, main = pull_identity(gate, gate.head)
    require(
        main == snapshot[2]
        and (not (candidate or full) or current["base"]["sha"] == main)
        and (source.source.event != "pull_request" or merge == snapshot[1])
        and (not (candidate or full) or source.source.event != "pull_request" or merge == source.commit)
        and _authenticated_run(gate, run_id, candidate_composite=candidate) == run,
        "source or workflow attempt changed during verification",
    )
    if not candidate:
        require_pr_base_ancestor(source.source.base, main)
        require_pr_base_ancestor(current["base"]["sha"], main)
    return verdict


def verify_main(  # noqa: PLR0913 - preserve the independently supplied source, evidence and transport
    gate: Gate,
    run_id: int,
    base: str,
    manifest: str,
    read: Callable[[str], bytes],
    *,
    slots: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Verify focused resulting-main evidence for the caller's independently validated aggregate base.

    Raises:
        CandidateError: The run cannot establish focused evidence for this exact main commit and base.
    """
    run = _authenticated_run(gate, run_id)
    require(
        run["event"] == "push"
        and run["head_branch"] == "main"
        and run["head_repository"]["full_name"] == REPOSITORY
        and valid_sha(base)
        and gate.github("git/ref/heads/main")["object"]["sha"] == gate.head,
        "focused main evidence requires the actual current main push and aggregate base",
    )
    git("fetch", "--no-tags", "origin", "refs/heads/main")
    require(
        git("rev-parse", "FETCH_HEAD").decode().strip() == gate.head
        and git("merge-base", base, gate.head).decode().strip() == base,
        "main advanced or does not contain the expected aggregate base",
    )
    source = WorkloadSource("push", REPOSITORY, "refs/heads/main", {}, base, gate.head, raw_diff(base, gate.head))
    scope = resolve(source, gate.head, manifest, slots=slots)
    if not isinstance(scope, Scope):
        message = "focused main evidence cannot stand in for a full candidate"
        raise CandidateError(message)
    _verify_config(scope)
    _verify_workload(gate, GateSource(run, source, gate.head), scope, read)
    require(
        gate.github("git/ref/heads/main")["object"]["sha"] == gate.head and _authenticated_run(gate, run_id) == run,
        "main or its workflow attempt changed during verification",
    )
    return {
        "type": "focused-main/v1",
        "base": base,
        "head": gate.head,
        "tree": scope.tree,
        "run": run_id,
        "attempt": 1,
        "manifest_sha256": scope.manifest_digest,
    }


def main() -> None:
    """Run from the protected merge gate, with the workload Git tree fetched as data."""
    gate = Gate(REPOSITORY, os.environ.get("PR_NUMBER", ""), os.environ["PR_HEAD_SHA"], os.environ["GITHUB_TOKEN"])

    def read(path: str) -> bytes:
        url = f"{GITHUB}/repos/{REPOSITORY}/{path}"
        limit = 1024 * 1024 if path.endswith("/logs") else MAX_ARCHIVE
        return fetch_evidence(url, gate.token, limit=limit)

    run = int(os.environ["CI_RUN_ID"])
    manifests = os.environ.get(PLAN_VARIABLE, "")
    if base := os.environ.get("FOCUSED_MAIN_BASE"):
        print(
            json.dumps(
                verify_main(gate, run, base, manifests, read, slots=registry_slots(dict(os.environ))), sort_keys=True
            )
        )
        return
    verdict = verify(gate, run, manifests, read, slots=registry_slots(dict(os.environ)))
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"verdict={verdict}\n")
    print(verdict)


if __name__ == "__main__":
    main()
