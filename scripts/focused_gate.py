# /// script
# requires-python = ">=3.12"
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
from scripts.focused_coverage import FULL_LANE, MAX_FILE, REPORT, canonical, config_policy, evidence, parse
from scripts.focused_plan import (
    ADVISORY_LANES,
    ARTIFACT_PREFIX,
    FULL_JOB,
    PLAN_VARIABLE,
    PROTECTED_FILES,
    Scope,
    git,
    git_blob,
    last_green_main,
    plan_registry,
    pr_merge_base,
    prose_only,
    raw_diff,
    registry_slots,
    require_pr_base_ancestor,
    resolve,
    selected_registry,
    web_production,
)
from scripts.merge_gate import ARTIFACT, GITHUB, Gate, fetch_evidence, red_below, whole_total
from scripts.release_candidate import (
    REPOSITORY,
    WORKFLOW,
    CandidateError,
    job_inventory,
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
    release: bool = False  # release tier: original attempt only


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
    # Release tier: capacity recovery is judged separately by the release verifier, and this judge reads Coverage
    # from the original attempt. Merge tier: a rerun may complete the evidence; every attempt stays on record.
    if attempt == 1:
        require(original["conclusion"] == "success", "original workflow attempt was unsuccessful")
    else:
        allowed = {"cancelled", "failure"} if candidate_composite else {"cancelled", "failure", "timed_out"}
        require(original["conclusion"] in allowed, "a rerun requires an unsuccessful original attempt")
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
    workload = WorkloadSource(event, REPOSITORY, ref, payload, base, head, raw_diff(base, merge))
    return GateSource(run, workload, merge, release=candidate)


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


def latest_jobs(get: Callable[[str], Any], run_id: int, which: str = "latest") -> list[dict[str, Any]]:
    """Every job of a merge-tier run: the latest execution of each (which="latest") or of every attempt ("all").

    Raises:
        CandidateError: The bounded inventory cannot be collected.
    """
    rows: list[Any] = []
    for page in range(1, 11):
        data = get(f"actions/runs/{run_id}/jobs?filter={which}&per_page=100&page={page}")
        require(isinstance(data, dict) and isinstance(data.get("jobs"), list), "malformed job page")
        rows.extend(data["jobs"])
        if len(rows) >= data.get("total_count", 0) or not data["jobs"]:
            require(all(isinstance(row, dict) and _positive(row.get("id")) for row in rows), "malformed job identity")
            require(len({row["id"] for row in rows}) == len(rows), "duplicate job IDs")
            return rows
    message = "job pagination exceeded bound"
    raise CandidateError(message)


def rerun_ledger(get: Callable[[str], Any], run: dict[str, Any]) -> list[str]:
    """One line per job that failed on an earlier attempt and passed on the latest: a flake, never hidden."""
    if run["run_attempt"] == 1:
        return []
    jobs = latest_jobs(get, run["id"], "all")
    passed = {
        job["name"]
        for job in jobs
        if job.get("run_attempt") == max_attempt(jobs, job["name"]) and job.get("conclusion") == "success"
    }
    earlier = sorted(
        (job for job in jobs if job["name"] in passed and job.get("conclusion") not in {"success", "skipped", None}),
        key=lambda row: (row["name"], row.get("run_attempt", 0)),
    )
    return [
        f"passed on rerun: {job['name']} (attempt {job.get('run_attempt')} {job['conclusion']}, "
        f"run {run['id']}, head {run['head_sha']})"
        for job in earlier
    ]


def max_attempt(jobs: list[dict[str, Any]], name: str) -> int:
    """The latest attempt in which a job of this name ran."""
    return max(int(job.get("run_attempt", 0)) for job in jobs if job["name"] == name)


def jobs_for(gate: Gate, source: GateSource) -> list[dict[str, Any]]:
    """Bind each job to its attempt (release tier: the original one), the exact source and the Actions suite."""
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
    raw_jobs = job_inventory(gate.github, run["id"]) if source.release else latest_jobs(gate.github, run["id"])
    jobs = [job for job in raw_jobs if not _is_published_check(gate, source, job)]
    for job in jobs:
        require(
            _positive(job["id"])
            and job.get("run_id") == run["id"]
            and type(job.get("run_attempt")) is int
            and 1 <= job["run_attempt"] <= (1 if source.release else run["run_attempt"])
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
    """The run's artifacts by name. Evidence is looked up by its exact lane, run and attempt name."""
    found = gate.github(f"actions/runs/{run}/artifacts?per_page={MAX_ARTIFACTS}")
    artifacts = found["artifacts"]
    require(
        len(artifacts) == found["total_count"] < MAX_ARTIFACTS
        and len({item["id"] for item in artifacts}) == len(artifacts),
        "incomplete or duplicated artifact inventory",
    )
    require(all(item["workflow_run"]["id"] == run for item in artifacts), "artifact from another run")
    names = {item["name"]: item for item in artifacts}
    require(len(names) == len(artifacts), "duplicate artifact name")
    return names


def evidence_name(lane: str, run: int, attempt: int) -> str:
    """The artifact a lane uploads in one attempt of one run."""
    return f"{ARTIFACT_PREFIX}-{lane}-{run}-{attempt}"


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
            len(files) in (1, 2, 3)
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
    names = {job["name"]: job for job in jobs}
    require(len(names) == len(jobs), "duplicate job names in the workload")
    ran = {name for name, job in names.items() if job["conclusion"] != "skipped"}
    run = source.run["id"]
    artifacts = _artifact_inventory(gate, run)
    if scope.full:
        # The selector failed open: every job that ran must pass (advisory lanes aside), with the full matrix there.
        # A main push of prose alone runs no app jobs (ci.yml's prose_push): its PR already ran everything.
        prose = source.run["event"] == "push" and prose_only(scope.changed)
        require(
            {FULL_JOB, *STATIC_JOBS} <= ran
            and (prose or ("Coverage" in ran and any(name.startswith("Test (") for name in ran))),
            "the full set did not run its matrix and Coverage",
        )
        for name in sorted(ran - {f"Focused tests ({lane})" for lane in ADVISORY_LANES}):
            _check(gate, source, names[name])
        lanes = {} if prose and "Coverage" not in ran else {FULL_LANE: names["Coverage"]}
    else:
        expected = STATIC_JOBS | set(scope.workload.jobs)
        if source.run["event"] == "pull_request":
            expected |= PR_JOBS
        marker = f"Main batch focused scope ({source.source.base})"
        if source.run["event"] == "push" and marker in names:
            expected.add(marker)
        require(ran == expected, "focused workload job inventory is missing, unexpected or skipped")
        advisory = {f"Focused tests ({lane})" for lane in ADVISORY_LANES}
        for name in sorted(expected - advisory):
            _check(gate, source, names[name])
        lanes = {
            lane: names[f"Focused tests ({lane})"]
            for lane in scope.selection
            if lane not in ADVISORY_LANES or names[f"Focused tests ({lane})"]["conclusion"] == "success"
        }
    for lane, job in lanes.items():
        name = evidence_name(lane, run, job["run_attempt"])
        require(name in artifacts, f"missing evidence {name}")
        files = _artifact(read, job, artifacts[name], name)
        recorded = parse(files.pop(REPORT))
        require(
            canonical(recorded) == canonical(evidence(scope, lane, run, job["run_attempt"], files)),
            "focused typed evidence differs from exact source or recorded tests",
        )


def require_reviewed_execution(scope: Scope, registry: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    """A change to the workflow or its producers runs code this judge has not seen: require a reviewed pin of it.

    The selection is this judge's own, but the jobs that ran are the tested tree's workflow. When a protected file
    differs from the base, a reviewed manifest (the plan registry) must pin every protected blob of the tested tree.

    Raises:
        CandidateError: A protected file changed without a reviewed execution manifest for these exact blobs.
    """
    tested = {path: git_blob(scope.commit, path) for path in PROTECTED_FILES}
    changed = sorted(path for path, blob in tested.items() if git_blob(scope.workload.base, path) != blob)
    if changed:
        plans = [registry[key] for key in keys if isinstance(registry.get(key), dict)]
        require(
            any(plan.get("execution") == tested for plan in plans),
            "the workflow or its producers changed (" + ", ".join(changed) + "): a reviewed execution manifest "
            "must pin the tested blobs",
        )
    return changed


def advisory_summary(gate: Gate, source: GateSource, scope: Scope, read: Callable[[str], bytes]) -> str:
    """One verdict clause per advisory lane: what it found, or that it did not finish. It never blocks."""
    names = {job["name"]: job for job in jobs_for(gate, source)}
    artifacts = _artifact_inventory(gate, source.run["id"])
    clauses = []
    for lane in sorted(ADVISORY_LANES & scope.selection.keys()):
        job = names.get(f"Focused tests ({lane})")
        name = evidence_name(lane, source.run["id"], job["run_attempt"]) if job else ""
        if not job or job["conclusion"] != "success" or name not in artifacts:
            clauses.append(f"{lane} advisory: did not finish")
            continue
        failed = parse(_artifact(read, job, artifacts[name], name)[REPORT]).get("failed", [])
        clauses.append(
            f"{lane} advisory: {len(failed)} failing: {', '.join(failed)}" if failed else f"{lane} advisory: passed"
        )
    return "; ".join(clauses)


def _verify_config(scope: Scope) -> None:
    config = config_policy(git("cat-file", "blob", scope.config_blob))
    require(
        config == config_policy(git("show", f"{scope.workload.base}:pyproject.toml")),
        "focused coverage policy changed and requires protected review",
    )


def _tested_commit(gate: Gate, source: GateSource, read: Callable[[str], bytes]) -> str:
    """The merge commit the run tested, as every evidence artifact of its latest jobs records it."""
    jobs = {job["name"]: job for job in jobs_for(gate, source)}
    artifacts = _artifact_inventory(gate, source.run["id"])
    commits = set()
    for name, job in jobs.items():
        lane = "coverage" if name == "Coverage" else name.removeprefix("Focused tests (").removesuffix(")")
        artifact = evidence_name(lane, source.run["id"], job["run_attempt"])
        if job["conclusion"] == "success" and artifact in artifacts:
            files = _artifact(read, job, artifacts[artifact], artifact)
            commits.add(parse(files[REPORT]).get("commit"))
    require(len(commits) == 1, "evidence artifacts are missing or disagree on the tested commit")
    commit = commits.pop()
    require(valid_sha(commit), "invalid artifact tested commit")
    return commit


def _tested_source_after_main_moves(
    gate: Gate, source: GateSource, current_main: str, read: Callable[[str], bytes]
) -> GateSource:
    """Judge the merge the run actually tested when main has moved since: its base must precede main."""
    commit = _tested_commit(gate, source, read)
    if commit == source.commit:
        return source
    git("fetch", "--no-tags", "origin", commit)
    require(git("rev-parse", "FETCH_HEAD").decode().strip() == commit, "tested commit fetch changed")
    base = pr_merge_base(commit, source.source.head)
    require_pr_base_ancestor(base, current_main)
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


def verify(gate: Gate, run_id: int, manifest: str, read: Callable[[str], bytes], *, slots: tuple[str, ...] = ()) -> str:
    """Return a typed verdict for the completed original CI attempt, without publishing checks."""
    snapshot = pull_identity(gate, gate.head)
    candidate = bool(release_version("pull_request", REPOSITORY, "", {"pull_request": snapshot[0]}))
    if not candidate:
        manifest = selected_registry(manifest, slots, f"pull_request:{gate.head}")
    run = _authenticated_run(gate, run_id, candidate_composite=candidate)
    source = _pr_source(gate, run, snapshot, candidate=candidate)
    if not candidate:
        source = _tested_source_after_main_moves(gate, source, snapshot[2], read)
    scope = resolve(
        source.source,
        source.commit,
        manifest,
        source.source.payload["pull_request"],
        pr_base_limit=None if candidate else snapshot[2],
    )
    if not isinstance(scope, Scope):
        jobs = [job for job in jobs_for(gate, source) if job["name"] == "Coverage"]
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
        verdict = "Whole-project coverage measured by the full release candidate"
    else:
        # This judge runs from main: its own selector recomputed the scope, so the tested tree cannot narrow it.
        registry = plan_registry(manifest, slots)
        protected = require_reviewed_execution(scope, registry, (f"pull_request:{gate.head}",))
        _verify_config(scope)
        _verify_workload(gate, source, scope, read)
        verdict = ("Full set (" + scope.reason + "); " if scope.full else "") + (
            "PR scoped coverage passed; whole-project validation deferred to release candidate"
            if scope.production
            else "Python coverage: not applicable; web production changed"
            if any(web_production(path) for path in scope.changed)
            else "Python coverage: not applicable to the selected scope"
        )
        if protected:
            verdict += "; reviewed workflow change: " + ", ".join(protected)
        if advisory := advisory_summary(gate, source, scope, read):
            verdict += "; " + advisory

    current, merge, main = pull_identity(gate, gate.head)
    require(
        main == snapshot[2]
        and (not candidate or current["base"]["sha"] == main)
        and (source.source.event != "pull_request" or merge == snapshot[1])
        and (not candidate or source.source.event != "pull_request" or merge == source.commit)
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
    green = last_green_main(gate.github, gate.head)
    require(
        green is None or git("merge-base", base, green).decode().strip() == base,
        "a main base must not be newer than the last accepted green main commit",
    )
    source = WorkloadSource("push", REPOSITORY, "refs/heads/main", {}, base, gate.head, raw_diff(base, gate.head))
    unproven = "no green main run before this push"  # CI ran the full set; this judge requires the same
    scope = resolve(
        source, gate.head, manifest, slots=slots, select=(lambda _c, _p: (None, unproven)) if green is None else None
    )
    if not isinstance(scope, Scope):
        message = "focused main evidence cannot stand in for a full candidate"
        raise CandidateError(message)
    keys = (f"push:{gate.head}", f"push-tree:{scope.tree}")
    protected = require_reviewed_execution(scope, plan_registry(manifest, slots), keys)
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
        "attempt": run["run_attempt"],
        "manifest_sha256": scope.manifest_digest,
        "full": scope.full,
        "reviewed_workflow_change": protected,
        # The flake ledger for main: every job that passed only on a rerun (ADR 0149). The caller files them.
        "passed_on_rerun": rerun_ledger(gate.github, run),
    }


def main() -> None:
    """Run from the protected merge gate, with the workload Git tree fetched as data."""
    gate = Gate(REPOSITORY, os.environ.get("PR_NUMBER", ""), os.environ["PR_HEAD_SHA"], os.environ["GITHUB_TOKEN"])

    def read(path: str) -> bytes:
        url = f"{GITHUB}/repos/{REPOSITORY}/{path}"
        limit = MAX_UPLOAD_LOG if path.endswith("/logs") else MAX_ARCHIVE
        return fetch_evidence(url, gate.token, limit=limit)

    run = int(os.environ["CI_RUN_ID"])
    manifests = os.environ.get(PLAN_VARIABLE, "")
    if base := os.environ.get("FOCUSED_MAIN_BASE"):
        proof = verify_main(gate, run, base, manifests, read, slots=registry_slots(dict(os.environ)))
        if proof["passed_on_rerun"]:
            ledger = Path(os.environ.get("FLAKE_LEDGER", "flake-ledger.txt"))
            ledger.write_text("\n".join(proof["passed_on_rerun"]) + "\n", encoding="utf-8")
        print(json.dumps(proof, sort_keys=True))
        return
    verdict = verify(gate, run, manifests, read, slots=registry_slots(dict(os.environ)))
    ledger = rerun_ledger(gate.github, gate.github(f"actions/runs/{run}"))
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"verdict={verdict}{'; ' + str(len(ledger)) + ' job(s) passed on rerun' if ledger else ''}\n")
    if ledger:
        # The flake ledger: the merge gate uploads it, and the coordinator files a flaky bead per line.
        Path(os.environ.get("FLAKE_LEDGER", "flake-ledger.txt")).write_text("\n".join(ledger) + "\n", encoding="utf-8")
    print(verdict, *ledger, sep="\n")


if __name__ == "__main__":
    main()
