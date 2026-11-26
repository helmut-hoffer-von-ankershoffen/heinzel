# /// script
# requires-python = ">=3.15"
# dependencies = ["coverage==7.17.3"]
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
from scripts.focused_coverage import (
    FLOOR,
    MAX_FILE,
    MEASUREMENT,
    REPORT,
    THRESHOLD,
    canonical,
    config_policy,
    evidence,
    parse,
)
from scripts.focused_plan import (
    ARTIFACT_PREFIX,
    LANES,
    MAX_PLAN,
    PLAN_VARIABLE,
    PROTECTED_FILES,
    Scope,
    changed_paths,
    decode_slot,
    git,
    git_blob,
    object_id,
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
FULL_PR_KIND = "full-pr-reviewed"
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


TOP_FILES = 5
TOP_LINES = 12


def _ranges(lines: list[int]) -> str:
    """Compress sorted line numbers into `3-5, 9` form, at most TOP_LINES groups."""
    groups: list[list[int]] = []
    for line in sorted(set(lines)):
        if groups and line == groups[-1][1] + 1:
            groups[-1][1] = line
        else:
            groups.append([line, line])
    shown = [f"{a}" if a == b else f"{a}-{b}" for a, b in groups[:TOP_LINES]]
    return ", ".join(shown) + (", ..." if len(groups) > TOP_LINES else "")


def coverage_notes(lane: str, recorded: dict[str, Any], raw: bytes) -> list[str]:
    """Name measured vs required percent and the least covered files, with their missing lines."""
    coverage = recorded.get("coverage")
    if not isinstance(coverage, dict):
        return []
    covered, total = coverage["covered"], coverage["total"]
    measured = f"{100 * covered / total:.2f}% ({covered}/{total} statements and branches)"
    notes = [f"Lane {lane}: measured {measured}, required {FLOOR}% floor and {THRESHOLD}% report threshold."]
    missing = {}
    try:
        missing = {path: row.get("missing_lines", []) for path, row in json.loads(raw)["files"].items()}
    except (ValueError, KeyError, AttributeError):
        notes.append("(per-line misses unavailable: the coverage report could not be read)")
    ranked = sorted(
        (
            (path, sum(c for c, _ in row.values()), sum(t for _, t in row.values()))
            for path, row in coverage["files"].items()
        ),
        key=lambda item: (item[1] - item[2], item[0]),
    )
    for path, hit, count in ranked[:TOP_FILES]:
        if hit < count:
            lines = f"; missing lines {_ranges(missing[path])}" if missing.get(path) else ""
            notes.append(f"  {path}: {hit}/{count} ({100 * hit / count:.1f}%), {count - hit} uncovered{lines}")
    return notes


def _verify_workload(
    gate: Gate,
    source: GateSource,
    scope: Scope,
    read: Callable[[str], bytes],
    notes: list[str] | None = None,
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
        if notes is not None:
            notes.extend(coverage_notes(lane, recorded, files.get(MEASUREMENT, b"")))
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


def full_pr_inputs(source: GateSource, base: str, authorization: dict[str, Any] | None) -> None:
    """Authorize only exact reviewed Makefile changes from the protected registry, never from PR bytes."""
    actual = {path: git_blob(source.commit, path) for path in FULL_EXECUTION_INPUTS}
    baseline = {path: git_blob(base, path) for path in FULL_EXECUTION_INPUTS}
    if authorization is None:
        require(actual == baseline, "full PR execution inputs differ from protected main")
        return
    require(
        set(authorization) == {"schema", "kind", "repository", "head", "base", "tree", "changed", "execution"}
        and type(authorization["schema"]) is int
        and authorization["schema"] == 1
        and authorization["kind"] == FULL_PR_KIND
        and authorization["repository"] == REPOSITORY
        and authorization["head"] == source.source.head
        and authorization["base"] == base
        and authorization["tree"] == object_id(f"{source.commit}^{{tree}}"),
        "full PR authorization identity differs from the exact reviewed source",
    )
    changed = changed_paths(raw_diff(base, source.commit))
    require(
        authorization["changed"] == changed
        and set(FULL_EXECUTION_INPUTS).intersection(changed) == {"Makefile"}
        and changed.get("Makefile") == "M"
        and authorization["execution"] == actual
        and actual["Makefile"] != baseline["Makefile"]
        and all(actual[path] == baseline[path] for path in FULL_EXECUTION_INPUTS if path != "Makefile"),
        "full PR authorization must cover the exact change and only a reviewed Makefile execution input",
    )


def selected_pr_registry(manifest: str, slots: tuple[str, ...], key: str) -> str:
    """Keep legacy focused duplicate compatibility, but require unique full-PR authorizations."""
    selected = selected_registry(manifest, slots, key)  # validate bounds and conflicts before decoding again
    seen = set()
    registries = [json.loads(manifest or "{}", object_pairs_hook=unique_object), *(decode_slot(slot) for slot in slots)]
    for registry in registries:
        for entry_key, entry in registry.items():
            if isinstance(entry, dict) and entry.get("kind") == FULL_PR_KIND:
                require(entry_key not in seen, "duplicate full PR authorization")
                seen.add(entry_key)
    return selected


MAIN_ONLY_MACOS = ("make setup", "End-to-end", "Electron")
SELECTOR_KEYS = frozenset(
    {
        "core",
        "site_only",
        "surface_only",
        "macos_full",
        "notifier",
        "contracts",
        "electron",
        "plugins",
        "release",
    }
)
# What GitHub names a matrix job that was skipped: the unexpanded template of its `name:` in ci.yml.
SKIPPED_TEMPLATES = {
    "setup": "make setup (${{ matrix.os }})",
    "test": (
        "Test (${{ matrix.os }}, Python ${{ matrix.python }}"
        "${{ matrix.levels && format(', {0}', matrix.levels) || '' }})"
    ),
    "e2e": "End-to-end ${{ matrix.suite }} (${{ matrix.os }})",
    "electron": "Electron (${{ matrix.os }})",
    "consumer": "Consumer (${{ matrix.name }})",
}


def selector_output(log: bytes) -> dict[str, bool]:
    """Read the selection the `Changed paths` job printed, each key exactly once as true or false.

    Raises:
        CandidateError: The log is oversized or a key is missing, repeated, unknown or not a boolean.
    """
    require(len(log) <= 1024 * 1024, "oversized selector log")
    found = re.findall(rb"(?m)^\d{4}-[^ \r\n]+ selector-output: ([a-z_]+)=(true|false)\r?$", log)
    keys = [key.decode() for key, _ in found]
    require(len(keys) == len(set(keys)) and set(keys) == SELECTOR_KEYS, "missing or ambiguous selector output")
    return {key.decode(): value == b"true" for key, value in found}


def selected_pr_inventory(selected: dict[str, bool]) -> dict[str, str]:
    """Name every job of an ordinary pull request run: what the selector chose succeeds, the rest is skipped.

    The rules mirror the `if:` of each job in ci.yml (and its `Require the complete direct CI workload` step), so a
    selected job that was skipped, and a job that ran without being selected, both differ from the inventory.
    A release candidate run (the release step's flag) keeps the complete inventory.
    """
    inventory = expected_jobs("pull_request")
    inventory["Release candidate source"] = "skipped"  # ordinary full PRs are not release candidates
    if selected["release"]:
        return inventory
    core, site, surface = selected["core"], selected["site_only"], selected["surface_only"]
    tested = core and not site
    for name in [n for n in inventory if n.endswith("(macos-latest)") and n.startswith(MAIN_ONLY_MACOS)]:
        del inventory[name]  # a pull request that is not a release candidate runs these on Linux only

    def rule(prefix: str, on: bool, template: str | None = None) -> None:
        names = [name for name in inventory if name.startswith(prefix)]
        for name in names:
            del inventory[name]
        if on:
            inventory.update(dict.fromkeys(names, "success"))
        elif template:
            inventory[template] = "skipped"
        else:
            inventory.update(dict.fromkeys(names, "skipped"))

    rule("make setup", core, SKIPPED_TEMPLATES["setup"])
    rule("Test (", tested, SKIPPED_TEMPLATES["test"])
    rule("End-to-end ", tested, SKIPPED_TEMPLATES["e2e"])
    rule("Electron (", selected["electron"], SKIPPED_TEMPLATES["electron"])
    rule("Consumer (heinzel-", selected["contracts"], SKIPPED_TEMPLATES["consumer"])
    for name, on in {
        "Lint, contracts and types": core or surface,
        "Dependency audit": core,
        "Coverage": tested,
        "Performance tests": tested,
        "Plugin template": not site,
        "Heinzel Notifier (macOS app)": selected["notifier"],
        "macOS-specific tests": (selected["macos_full"] or surface) and (core or surface) and not site,
        "Build": core,
        "Plugin SDK candidate": True,
        "Optional source plugins": selected["plugins"] or selected["contracts"],
        "Public contract consumers": selected["contracts"],
        "Site tests": site,
    }.items():
        inventory[name] = "success" if on else "skipped"
    return inventory


def full_pr_jobs(
    gate: Gate,
    source: GateSource,
    base: str,
    read: Callable[[str], bytes],
    authorization: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Verify ordinary full CI without inventing a focused plan or release-candidate identity."""
    require(source.run["event"] == "pull_request", "full PR evidence requires the PR event")
    associated = source.run["pull_requests"]
    require(len(associated) == 1 and associated[0]["base"]["sha"] == base, "full PR evidence has a stale tested base")
    full_pr_inputs(source, base, authorization)
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
    jobs = quarantine_lane(jobs_for(gate, source), source.run["id"], source.source.head)
    changed_jobs = [job for job in jobs if job["name"] == "Changed paths"]
    require(len(changed_jobs) == 1 and changed_jobs[0]["conclusion"] == "success", "full PR selector job is missing")
    log = read(f"actions/jobs/{changed_jobs[0]['id']}/logs")
    inventory = selected_pr_inventory(selector_output(log))
    require(
        len(jobs) == len(inventory) and {job["name"] for job in jobs} == set(inventory), "full PR job inventory differs"
    )
    for job in jobs:
        require(job["conclusion"] == inventory[job["name"]], "full PR job did not meet its required conclusion")
        if job["conclusion"] == "success":
            _check(gate, source, job)
    require(full_checkout(log) == source.commit, "full CI tested another merge commit")
    return jobs


def verify(  # noqa: PLR0913 - one linear verdict; the notes only describe it
    gate: Gate,
    run_id: int,
    manifest: str,
    read: Callable[[str], bytes],
    *,
    slots: tuple[str, ...] = (),
    notes: list[str] | None = None,
) -> str:
    """Return a typed verdict for the completed original CI attempt, without publishing checks.

    `notes` collects what a reader of the check run needs: the scope and the measured and required coverage.
    """
    notes = [] if notes is None else notes
    snapshot = pull_identity(gate, gate.head)
    candidate = bool(release_version("pull_request", REPOSITORY, "", {"pull_request": snapshot[0]}))
    if not candidate:
        manifest = selected_pr_registry(manifest, slots, f"pull_request:{gate.head}")
    registry = json.loads(manifest, object_pairs_hook=unique_object) if not candidate else {}
    authorization = registry.get(f"pull_request:{gate.head}") if not candidate else None
    reviewed_full = isinstance(authorization, dict) and authorization.get("kind") == FULL_PR_KIND
    require(
        not isinstance(authorization, dict) or "kind" not in authorization or reviewed_full,
        "unknown PR authorization kind",
    )
    full = not candidate and (not registry or reviewed_full)
    run = _authenticated_run(gate, run_id, candidate_composite=candidate)
    source = _pr_source(gate, run, snapshot, candidate=candidate)
    if (
        not candidate
        and not full
        and json.loads(manifest, object_pairs_hook=unique_object).get(f"pull_request:{gate.head}", {}).get("base")
        != snapshot[2]
    ):
        source = _tested_source_after_main_moves(gate, source, manifest, snapshot[2], read)
    full_jobs = full_pr_jobs(gate, source, snapshot[2], read, authorization) if full else None
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
        floor = red_below(Path(WORKFLOW).read_text())
        notes.append(f"Whole-project coverage: measured {total}%, required {floor}%. Scope: the whole project.")
        require(total >= floor, f"full candidate coverage {total}% is below its {floor}% floor")
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
        paths = sorted(scope.changed)
        shown = ", ".join(paths[:TOP_LINES]) + (f", ... ({len(paths)} files)" if len(paths) > TOP_LINES else "")
        notes.append(f"Scope: {len(paths)} changed file(s), {len(scope.production)} production: {shown}")
        _verify_workload(gate, source, scope, read, notes)
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
    """Run from the protected merge gate, with the workload Git tree fetched as data.

    Raises:
        CandidateError: The evidence does not meet the gate; the report names why.
    """
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
    notes: list[str] = []
    try:
        verdict = verify(gate, run, manifests, read, slots=registry_slots(dict(os.environ)), notes=notes)
    except CandidateError as error:
        publish(f"coverage-floor FAILED (not met): {error}", notes)
        raise
    except Exception as error:
        # A tool error is not a coverage verdict: say so, so nobody hunts for a coverage gap that isn't there.
        publish(
            "coverage-floor TOOL ERROR (the gate itself broke, coverage was not judged): "
            f"{type(error).__name__}: {error}",
            notes,
        )
        raise
    publish(verdict, notes)


def publish(headline: str, notes: list[str]) -> None:
    """Print the verdict and hand it, with its details, to the workflow's report step."""
    text = "\n".join([headline, *notes])
    print(text)
    if path := os.environ.get("GITHUB_OUTPUT"):
        with Path(path).open("a", encoding="utf-8") as output:
            output.write(f"verdict<<GATE_EOF\n{text[:60000].replace('GATE_EOF', 'GATE EOF')}\nGATE_EOF\n")


if __name__ == "__main__":
    main()
