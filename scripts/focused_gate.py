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
from scripts.focused_coverage import MAX_FILE, REPORT, canonical, config_policy, evidence, parse
from scripts.focused_plan import (
    ARTIFACT_PREFIX,
    PLAN_VARIABLE,
    PROTECTED_FILES,
    Scope,
    git,
    git_blob,
    raw_diff,
    resolve,
    web_production,
)
from scripts.merge_gate import ARTIFACT, GITHUB, Gate, fetch, red_below, whole_total
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


def pull_identity(gate: Gate, head: str) -> tuple[dict[str, Any], str]:
    """Read current PR and resolve its actual merge ref independently of the event body."""
    number = int(gate.pr)
    pr = gate.github(f"pulls/{number}")
    base = gate.github("git/ref/heads/main")["object"]["sha"]
    require(
        pr["number"] == number
        and pr["state"] == "open"
        and pr["draft"] is False
        and pr["base"]["repo"]["full_name"] == REPOSITORY
        and pr["base"]["ref"] == "main"
        and pr["base"]["sha"] == base
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
    return pr, fields[0]


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


def _pr_source(gate: Gate, run: dict[str, Any], snapshot: tuple[dict[str, Any], str]) -> GateSource:
    pr, merge = snapshot
    event = run["event"]
    head, base = gate.head, pr["base"]["sha"]
    require(
        event in ("pull_request", "workflow_dispatch")
        and run["head_repository"]["id"] == pr["head"]["repo"]["id"]
        and run["head_branch"] == pr["head"]["ref"],
        "run does not belong to this pull request",
    )
    if event == "pull_request":
        ref = f"refs/pull/{gate.pr}/merge"
        associated = run["pull_requests"]
        require(
            (not associated and pr["head"]["repo"]["fork"])
            or (
                len(associated) == 1
                and associated[0]["number"] == pr["number"]
                and associated[0]["base"]["sha"] == base
                and associated[0]["head"]["sha"] == head
            ),
            "workflow run association does not bind the exact PR base and head",
        )
        git("fetch", "--no-tags", "origin", ref)
        require(git("rev-parse", "FETCH_HEAD").decode().strip() == merge, "GitHub test merge moved during fetch")
    else:
        require(
            release_version("pull_request", REPOSITORY, "", {"pull_request": pr}),
            "ordinary pull requests require their original PR CI run",
        )
        ref, merge = f"refs/heads/{pr['head']['ref']}", head
        git("fetch", "--no-tags", "origin", head)
    payload = {"pull_request": pr}
    return GateSource(run, WorkloadSource(event, REPOSITORY, ref, payload, base, head, raw_diff(base, merge)), merge)


def jobs_for(gate: Gate, source: GateSource) -> list[dict[str, Any]]:
    """Bind each job to the original attempt, exact source and genuine Actions suite."""
    run = source.run
    suite = gate.github(f"check-suites/{run['check_suite_id']}")
    require(
        suite["id"] == run["check_suite_id"]
        and suite["app"]["id"] == ACTIONS_APP_ID
        and suite["head_sha"] == source.source.head
        and suite["status"] == "completed"
        and suite["conclusion"] == "success",
        "unexpected GitHub Actions check suite",
    )
    jobs = job_inventory(gate.github, run["id"])
    for job in jobs:
        require(
            _positive(job["id"])
            and job["run_id"] == run["id"]
            and job["run_attempt"] == 1
            and job["head_sha"] == source.source.head
            and job["status"] == "completed",
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


def verify(gate: Gate, run_id: int, manifest: str, read: Callable[[str], bytes]) -> str:
    """Return a typed verdict for the completed original CI attempt, without publishing checks."""
    snapshot = pull_identity(gate, gate.head)
    candidate = bool(release_version("pull_request", REPOSITORY, "", {"pull_request": snapshot[0]}))
    run = _authenticated_run(gate, run_id, candidate_composite=candidate)
    source = _pr_source(gate, run, snapshot)
    scope = resolve(source.source, source.commit, manifest, source.source.payload["pull_request"])
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
    current, merge = pull_identity(gate, gate.head)
    require(
        current["base"]["sha"] == source.source.base
        and (source.source.event != "pull_request" or merge == source.commit)
        and _authenticated_run(gate, run_id, candidate_composite=candidate) == run,
        "source or workflow attempt changed during verification",
    )
    return verdict


def verify_main(
    gate: Gate,
    run_id: int,
    base: str,
    manifest: str,
    read: Callable[[str], bytes],
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
    scope = resolve(source, gate.head, manifest)
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

    storage = frozenset(filter(None, os.environ.get("FOCUSED_CI_STORAGE_HOSTS", "").split(",")))

    def read(path: str) -> bytes:
        url = f"{GITHUB}/repos/{REPOSITORY}/{path}"
        limit = MAX_UPLOAD_LOG if path.endswith("/logs") else MAX_ARCHIVE
        headers = {
            "Authorization": f"Bearer {gate.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
        }
        return fetch(url, headers, limit=limit, storage_hosts=storage)

    run = int(os.environ["CI_RUN_ID"])
    manifests = os.environ.get(PLAN_VARIABLE, "")
    if base := os.environ.get("FOCUSED_MAIN_BASE"):
        print(json.dumps(verify_main(gate, run, base, manifests, read), sort_keys=True))
        return
    verdict = verify(gate, run, manifests, read)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"verdict={verdict}\n")
    print(verdict)


if __name__ == "__main__":
    main()
