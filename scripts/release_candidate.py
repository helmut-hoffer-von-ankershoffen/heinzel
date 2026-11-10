# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Bind one full release-candidate CI run to its exact source tree and complete first-attempt job inventory."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

REPOSITORY = "helmut-hoffer-von-ankershoffen/heinzel"
WORKFLOW = ".github/workflows/ci.yml"
RECORDER = "scripts/release_candidate.py"
ARTIFACT = "release-candidate-source"
PROOF_FILE = "release-candidate.json"
VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"[0-9a-f]{64}")
MAX_JOBS = 1000
MAX_PROOF = 64 * 1024
CONSUMERS = ("atlassian", "clock", "example", "github", "google", "resource", "tailscale", "theme", "weather", "zoom")
LEGACY_WORKFLOW = "2ab124821108a4c6795627d33e70e871b1ee93e5f6f5ff8b57e331c98e1779ef"


class CandidateError(ValueError):
    """Candidate evidence is incomplete, changed or outside the reviewed release contract."""


def require(condition: object, message: str) -> None:
    """Refuse unknown evidence instead of inferring a successful test execution.

    Raises:
        CandidateError: The supplied evidence does not satisfy the contract.
    """
    if not condition:
        raise CandidateError(message)


@dataclass(frozen=True, slots=True)
class ExpectedCandidate:
    """The coordinator's exact run, open PR and reviewed source head to verify."""

    run_id: int
    pr_number: int
    head: str
    tree: str
    merged_sha: str | None = None


def command(argv: list[str]) -> bytes:
    """Run fixed read-only source/API commands through installed tools without a shell."""
    require(argv[0] in {"git", "gh"}, "unsupported release evidence tool")
    executable = shutil.which(argv[0])
    require(executable, "release evidence tool is not installed")
    return subprocess.run(  # noqa: S603 - fixed installed git/gh, no shell; callers validate all source identities
        [str(executable), *argv[1:]],
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout


def valid_sha(value: object) -> bool:
    """Recognize a complete immutable Git identity at an untyped API boundary."""
    return isinstance(value, str) and SHA.fullmatch(value) is not None


def release_version(event: str, repository: str, ref: str, payload: dict[str, Any]) -> str | None:
    """Select full CI only for canonical release branches in the owner repository; this grants no merge authority."""
    if repository != REPOSITORY:
        return None
    if event == "pull_request":
        pr = payload.get("pull_request", {})
        if (
            pr.get("head", {}).get("repo", {}).get("full_name") != REPOSITORY
            or pr.get("base", {}).get("repo", {}).get("full_name") != REPOSITORY
            or pr.get("base", {}).get("ref") != "main"
        ):
            return None
        ref = pr.get("head", {}).get("ref", "")
    elif event != "workflow_dispatch":
        return None
    version = ref.removeprefix("release/v") if ref.startswith("release/v") else ""
    if not VERSION.fullmatch(version):
        return None
    if event == "pull_request" and payload["pull_request"].get("title") != f"chore(release): v{version}":
        return None
    return version


def context() -> tuple[str, str, str, dict[str, Any]]:
    """Read only the public Actions event identity, never a token or workflow grant."""
    return (
        os.environ.get("GITHUB_EVENT_NAME", ""),
        os.environ.get("GITHUB_REPOSITORY", ""),
        os.environ.get("GITHUB_REF_NAME", ""),
        json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text()),
    )


def git(*args: str) -> str:
    """Read source identity without changing the checkout or executing its product code."""
    return command(["git", *args]).decode().strip()


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Refuse a source artifact that hides repeated provenance fields behind duplicate JSON keys."""
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate candidate source JSON key")
        result[key] = value
    return result


def record(root: Path) -> dict[str, Any]:
    """Record the checked-out tree and independently queried PR head tree before the full candidate is consumed."""
    event, repository, ref, payload = context()
    version = release_version(event, repository, ref, payload)
    require(version, "not an owner-repository canonical release candidate")
    source = git("rev-parse", "HEAD")
    require(source == os.environ.get("GITHUB_SHA"), "checkout differs from the Actions event SHA")
    tree = git("rev-parse", "HEAD^{tree}")
    if event == "pull_request":
        head = payload["pull_request"]["head"]["sha"]
        base = payload["pull_request"]["base"]["sha"]
        number = payload["pull_request"]["number"]
    else:
        head, base, number = source, git("merge-base", "HEAD", "origin/main"), None
    require(SHA.fullmatch(head) and SHA.fullmatch(base), "invalid source commit identity")
    require(git("rev-parse", f"{head}^{{tree}}") == tree, "tested merge tree differs from the exact release head tree")
    require(
        tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"] == version,
        "project version differs from the release branch",
    )
    return {
        "schema": 1,
        "repository": repository,
        "event": event,
        "version": version,
        "pr": number,
        "head": head,
        "base": base,
        "source_commit": source,
        "tree": tree,
        "run_id": int(os.environ["GITHUB_RUN_ID"]),
        "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
        "sources": {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in (WORKFLOW, RECORDER)},
    }


def expected_jobs(event: str, *, release_delegation: bool = True) -> dict[str, str]:
    """Name the complete reviewed full-candidate workload, including all twelve Python and eight E2E cells."""
    require(event in ("pull_request", "workflow_dispatch"), "unsupported candidate event")
    success = {
        "Main batch admission",
        "Changed paths",
        "Secret scan",
        "Dependency audit",
        "Coverage",
        "Optional source plugins",
        "Plugin SDK candidate",
        "Lint, contracts and types",
        "Performance tests",
        "Heinzel Notifier (macOS app)",
        "macOS-specific tests",
        "Public contract consumers",
        "Build",
        "Plugin template",
        "Release candidate source",
        "CI OK",
        *[f"make setup ({runner})" for runner in ("ubuntu-24.04", "ubuntu-24.04-arm", "macos-latest")],
        *[
            f"Test ({runner}, Python 3.{minor}, {level})"
            for runner in ("ubuntu-24.04", "macos-latest")
            for minor in range(12, 16)
            for level in ("unit", "integration")
        ],
        *[
            f"End-to-end {suite} ({runner})"
            for suite in ("supervised", "cli", "web-1", "web-2")
            for runner in ("ubuntu-24.04", "macos-latest")
        ],
        *[f"Electron ({runner})" for runner in ("ubuntu-24.04", "macos-latest")],
        *[f"Consumer (heinzel-{name})" for name in CONSUMERS],
    }
    skipped = {
        "Main batch full suite (${{ needs.admission.outputs.base }})",
        "Main batch delegated to ${{ needs.admission.outputs.run }}",
        "Site tests",
        "macOS smoke",
        "Source plugin (${{ matrix.name }})",
        *[
            f"Stop after {name} failure"
            for name in ("commits", "dependency-review", "secrets", "audit", "setup", "lint")
        ],
    }
    if release_delegation:
        skipped.add("Release candidate delegated to ${{ needs.admission.outputs.run }}")
    (success if event == "pull_request" else skipped).update(("Conventional commits", "Dependency review"))
    return dict.fromkeys(success, "success") | dict.fromkeys(skipped, "skipped")


QUARANTINE_REGISTRY = "Quarantine registry"
QUARANTINE_PLAN = "Quarantine plan"
QUARANTINE_RUNS = frozenset({"Quarantine (ubuntu-24.04)", "Quarantine (macos-latest)"})
QUARANTINE_IDLE = "Quarantine (${{ matrix.os }})"


def quarantine_lane(jobs: list[dict[str, Any]], run_id: object, head: str) -> list[dict[str, Any]]:
    """Check the quarantine lane's jobs and return the others for the exact inventory.

    The lane (tests/quarantine.toml, scripts/quarantine.py) is absent from a workflow that predates it. Otherwise the
    required registry check passed, and the non-blocking plan and lane jobs ran on this run's first attempt: both OS
    jobs, whatever their tests did, or the one skipped placeholder when nothing is quarantined. A lane failure never
    refuses a candidate; a missing, extra or rerun lane job does.

    Raises:
        CandidateError: The lane's jobs are incomplete, duplicated, rerun or the registry check did not pass.
    """
    names = {QUARANTINE_REGISTRY, QUARANTINE_PLAN, QUARANTINE_IDLE, *QUARANTINE_RUNS}
    lane = [job for job in jobs if job.get("name") in names]
    if not lane:
        return jobs
    found = sorted(str(job.get("name")) for job in lane)
    require(
        found
        in (
            sorted({QUARANTINE_REGISTRY, QUARANTINE_PLAN, *QUARANTINE_RUNS}),
            sorted({QUARANTINE_REGISTRY, QUARANTINE_PLAN, QUARANTINE_IDLE}),
        ),
        "missing, extra or duplicated quarantine lane jobs",
    )
    for job in lane:
        # A lane job that hit its time limit ends cancelled; it still never blocks the candidate.
        allowed = {"success"} if job["name"] == QUARANTINE_REGISTRY else {"success", "failure", "skipped", "cancelled"}
        if job["name"] == QUARANTINE_IDLE:
            allowed = {"skipped"}
        require(
            job.get("run_id") == run_id
            and job.get("run_attempt") == 1
            and job.get("head_sha") == head
            and job.get("status") == "completed"
            and job.get("conclusion") in allowed,
            "quarantine lane job failed its registry check, changed or reran",
        )
    return [job for job in jobs if job.get("name") not in names]


def job_inventory(get: Callable[[str], Any], run_id: int) -> list[dict[str, Any]]:
    """Exhaust the immutable first-attempt job pages and refuse inconsistent totals or duplicate IDs.

    Raises:
        CandidateError: The complete bounded inventory cannot be collected.
    """
    rows: list[Any] = []
    total = None
    for page in range(1, 11):
        data = get(f"actions/runs/{run_id}/attempts/1/jobs?per_page=100&page={page}")
        require(
            isinstance(data, dict) and type(data.get("total_count")) is int and isinstance(data.get("jobs"), list),
            "malformed candidate job page",
        )
        if total is None:
            total = data["total_count"]
        require(data["total_count"] == total and 0 < total <= MAX_JOBS, "candidate job count changed or exceeds bound")
        rows.extend(data["jobs"])
        require(len(rows) <= total, "extra jobs beyond declared inventory")
        if len(rows) == total:
            require(all(isinstance(row, dict) and type(row.get("id")) is int for row in rows), "malformed job identity")
            require(len({row["id"] for row in rows}) == total, "duplicate candidate job IDs")
            return rows
        require(data["jobs"], "truncated candidate job inventory")
    message = "candidate job pagination exceeded bound"
    raise CandidateError(message)


def validate(evidence: dict[str, Any], expected: ExpectedCandidate, approved_sources: dict[str, str]) -> dict[str, Any]:
    """Consume real completed first-attempt evidence only, without aliasing checks or replacing any main gate."""
    run, jobs, proof, pr, source, head = (evidence[key] for key in ("run", "jobs", "proof", "pr", "source", "head"))
    expected_head = expected.head
    expected_tree = expected.tree
    require(
        valid_sha(expected_head) and valid_sha(expected_tree),
        "invalid expected head or tree",
    )
    require(
        set(approved_sources) == {WORKFLOW, RECORDER}
        and all(DIGEST.fullmatch(value) for value in approved_sources.values()),
        "missing reviewed CI source pins",
    )
    require(
        type(run.get("id")) is int
        and run["id"] == expected.run_id
        and run["id"] > 0
        and type(run.get("run_attempt")) is int
        and run["run_attempt"] == 1,
        "candidate must be the original attempt",
    )
    require(
        run.get("repository", {}).get("full_name") == REPOSITORY
        and run.get("head_repository", {}).get("full_name") == REPOSITORY
        and run.get("path") == WORKFLOW,
        "wrong candidate repository or workflow",
    )
    require(
        run.get("status") == "completed" and run.get("conclusion") == "success",
        "candidate is not completed successfully",
    )
    event = run.get("event")
    require(event in ("pull_request", "workflow_dispatch"), "candidate event is not a PR or explicit dispatch")
    require(run.get("head_sha") == expected_head, "candidate run is not the exact expected head")
    require(
        pr.get("number") == expected.pr_number
        and pr.get("draft") is False
        and pr.get("head", {}).get("sha") == expected_head,
        "release PR head changed",
    )
    if expected.merged_sha is None:
        require(pr.get("state") == "open", "release PR is no longer open")
    else:
        merged = evidence.get("merge", {})
        require(
            event == "pull_request"
            and valid_sha(expected.merged_sha)
            and pr.get("state") == "closed"
            and pr.get("merged") is True
            and pr.get("merged_at")
            and pr.get("merge_commit_sha") == expected.merged_sha
            and merged.get("sha") == expected.merged_sha
            and [parent.get("sha") for parent in merged.get("parents", [])] == [proof.get("base")]
            and merged.get("commit", {}).get("tree", {}).get("sha") == expected_tree,
            "merged release identity, parent or tree differs",
        )
    version = release_version("pull_request", REPOSITORY, "", {"pull_request": pr})
    require(version and run.get("head_branch") == f"release/v{version}", "candidate branch/title is not canonical")
    require(
        proof.get("schema") == 1
        and proof.get("repository") == REPOSITORY
        and proof.get("event") == event
        and proof.get("run_id") == run["id"]
        and proof.get("run_attempt") == 1
        and proof.get("head") == expected_head
        and proof.get("tree") == expected_tree
        and proof.get("version") == version
        and proof.get("sources") == approved_sources,
        "candidate source artifact differs from reviewed run or release identity",
    )
    require(proof.get("base") == pr.get("base", {}).get("sha"), "release base changed after candidate execution")
    require(
        head.get("sha") == expected_head and head.get("commit", {}).get("tree", {}).get("sha") == expected_tree,
        "live release head tree differs",
    )
    require(
        source.get("sha") == proof.get("source_commit")
        and source.get("commit", {}).get("tree", {}).get("sha") == expected_tree,
        "tested checkout tree differs",
    )
    if event == "pull_request":
        require(
            proof.get("pr") == pr.get("number")
            and [p.get("sha") for p in source.get("parents", [])] == [proof["base"], expected_head],
            "PR merge source does not bind its exact base and head",
        )
    else:
        require(
            proof.get("pr") is None and proof.get("source_commit") == expected_head,
            "dispatch source differs from the exact release head",
        )
    jobs = quarantine_lane(jobs, run["id"], expected_head)
    inventory = expected_jobs(event, release_delegation=approved_sources[WORKFLOW] != LEGACY_WORKFLOW)
    require(
        len(jobs) == len(inventory) and {job.get("name") for job in jobs} == set(inventory),
        "missing, extra or short-matrix candidate jobs",
    )
    require(len({job.get("id") for job in jobs}) == len(jobs), "duplicate candidate job IDs")
    for job in jobs:
        require(
            job.get("run_id") == run["id"]
            and job.get("run_attempt") == 1
            and job.get("head_sha") == expected_head
            and job.get("status") == "completed"
            and job.get("conclusion") == inventory[job["name"]],
            "candidate job failed, changed, reran or skipped workload",
        )
        if inventory[job["name"]] == "success":
            require(
                type(job.get("runner_id")) is int and job["runner_id"] > 0 and job.get("steps"),
                "candidate workload never acquired a runner",
            )
    created, finished = run.get("created_at"), run.get("updated_at")
    require(isinstance(created, str) and isinstance(finished, str), "candidate timestamps missing")
    duration = (datetime.fromisoformat(finished) - datetime.fromisoformat(created)).total_seconds()
    require(duration >= 0, "candidate timestamps reversed")
    return {
        "schema": 1,
        "run_id": run["id"],
        "run_attempt": 1,
        "event": event,
        "head": expected_head,
        "tree": expected_tree,
        "base": proof["base"],
        "source_commit": proof["source_commit"],
        "job_count": len(jobs),
        "created_at": created,
        "updated_at": finished,
        "workflow_wall_seconds": duration,
        "sources": approved_sources,
    }


def discover(get: Callable[[str], Any], expected_head: str) -> int:
    """Require one original CI run on this exact head; never select a later green from competing attempts.

    Raises:
        CandidateError: Run identity is ambiguous, incomplete or outside the bounded listing.
    """
    require(valid_sha(expected_head), "invalid release head for candidate discovery")
    rows = []
    total = None
    for page in range(1, 11):
        data = get(f"actions/workflows/ci.yml/runs?head_sha={expected_head}&per_page=100&page={page}")
        require(
            isinstance(data, dict)
            and type(data.get("total_count")) is int
            and isinstance(data.get("workflow_runs"), list),
            "malformed candidate run listing",
        )
        if total is None:
            total = data["total_count"]
        require(data["total_count"] == total and 0 < total <= MAX_JOBS, "candidate run listing changed or is empty")
        rows.extend(data["workflow_runs"])
        require(len(rows) <= total, "extra candidate runs beyond listing total")
        if len(rows) == total:
            require(
                len(rows) == 1
                and type(rows[0].get("id")) is int
                and rows[0]["id"] > 0
                and rows[0].get("head_sha") == expected_head
                and rows[0].get("run_attempt") == 1,
                "ambiguous, changed or rerun candidate; retain all original runs",
            )
            return int(rows[0]["id"])
        require(data["workflow_runs"], "truncated candidate run listing")
    message = "candidate run listing exceeded bound"
    raise CandidateError(message)


def source_bytes(get: Callable[[str], Any], path: str, ref: str) -> bytes:
    """Decode an exact official source blob, never a moving branch or an unchecked path."""
    require(path in (WORKFLOW, RECORDER) and SHA.fullmatch(ref), "invalid reviewed source request")
    data = get(f"contents/{path}?ref={ref}")
    require(
        isinstance(data, dict) and data.get("encoding") == "base64" and data.get("type") == "file",
        "source blob missing",
    )
    return base64.b64decode("".join(data["content"].split()), validate=True)


def verify(
    get: Callable[[str], Any],
    download: Callable[[int], bytes],
    expected: ExpectedCandidate,
    approved_sources: dict[str, str],
) -> dict[str, Any]:
    """Read every original job and the unique same-run source artifact, then re-fetch the terminal run identity."""
    run_id, pr_number, expected_head = expected.run_id, expected.pr_number, expected.head
    require(
        type(run_id) is int
        and run_id > 0
        and type(pr_number) is int
        and pr_number > 0
        and valid_sha(expected_head)
        and valid_sha(expected.tree),
        "invalid expected candidate identity",
    )
    run = get(f"actions/runs/{run_id}")
    require(isinstance(run, dict) and run.get("status") == "completed", "candidate run is not terminal")
    inventory = job_inventory(get, run_id)
    data = get(f"actions/runs/{run_id}/artifacts?per_page=100")
    require(
        isinstance(data, dict) and data.get("total_count") == len(data.get("artifacts", [])),
        "truncated artifact inventory",
    )
    matches = [a for a in data["artifacts"] if a.get("name") == ARTIFACT]
    require(len(matches) == 1 and matches[0].get("expired") is False, "candidate source artifact absent or duplicated")
    artifact = matches[0]
    require(
        artifact.get("workflow_run", {}).get("id") == run_id
        and artifact.get("workflow_run", {}).get("head_sha") == expected_head,
        "artifact belongs to another run or head",
    )
    require(type(artifact.get("id")) is int and artifact["id"] > 0, "malformed source artifact ID")
    archive = download(artifact["id"])
    require(len(archive) <= 1024 * 1024, "candidate source artifact exceeds bound")
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        require(zipped.namelist() == [PROOF_FILE], "unexpected candidate source archive contents")
        require(zipped.getinfo(PROOF_FILE).file_size <= MAX_PROOF, "candidate source proof exceeds bound")
        proof = json.loads(zipped.read(PROOF_FILE), object_pairs_hook=unique_object)
    require(
        isinstance(proof, dict)
        and all(
            isinstance(proof.get(key), str) and SHA.fullmatch(proof[key])
            for key in ("source_commit", "head", "base", "tree")
        ),
        "malformed candidate source identities",
    )
    pr = get(f"pulls/{pr_number}")
    head = get(f"commits/{expected_head}")
    source = get(f"commits/{proof['source_commit']}")
    for path, digest in approved_sources.items():
        require(
            hashlib.sha256(source_bytes(get, path, proof["source_commit"])).hexdigest() == digest,
            "executed CI source differs from independent review",
        )
    require(get(f"actions/runs/{run_id}") == run, "candidate run changed during evidence collection")
    evidence = {"run": run, "jobs": inventory, "proof": proof, "pr": pr, "source": source, "head": head}
    if expected.merged_sha is not None:
        evidence["merge"] = get(f"commits/{expected.merged_sha}")
    return validate(
        evidence,
        expected,
        approved_sources,
    )


def main() -> int:
    """Select full release CI, record its real checkout, or verify one externally selected immutable run."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    select = commands.add_parser("select")
    select.add_argument("--output", type=Path, required=True)
    recording = commands.add_parser("record")
    recording.add_argument("--output", type=Path, required=True)
    checking = commands.add_parser("verify")
    checking.add_argument("--run-id", type=int, required=True)
    checking.add_argument("--pr", type=int, required=True)
    checking.add_argument("--head", required=True)
    checking.add_argument("--tree", required=True)
    checking.add_argument("--workflow-sha256", required=True)
    checking.add_argument("--recorder-sha256", required=True)
    args = parser.parse_args()
    if args.command == "select":
        version = release_version(*context())
        with args.output.open("a") as output:
            output.write(f"full_release={'true' if version else 'false'}\n")
    elif args.command == "verify":

        def get(path: str) -> Any:  # noqa: ANN401 - the GitHub JSON response is validated at the evidence boundary
            return json.loads(command(["gh", "api", f"repos/{REPOSITORY}/{path}"]))

        def download(artifact_id: int) -> bytes:
            return command(["gh", "api", f"repos/{REPOSITORY}/actions/artifacts/{artifact_id}/zip"])

        result = verify(
            get,
            download,
            ExpectedCandidate(args.run_id, args.pr, args.head, args.tree),
            {WORKFLOW: args.workflow_sha256, RECORDER: args.recorder_sha256},
        )
        print(json.dumps(result, indent=2))
    else:
        proof = record(Path.cwd())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x") as output:
            output.write(json.dumps(proof, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
