# This read-only verifier uses fixed GitHub API paths and reports evidence counts.
# ruff: noqa: INP001, S603, S607, T201
"""Verify the exact seven-job #683 attempt before the separately owned status writer."""

import argparse
import hashlib
import io
import json
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import coverage_evidence  # noqa: E402 - only the reviewed helper directory is added under -I

MANIFEST = json.loads((HERE / "manifest.json").read_text())
REPO = MANIFEST["repository"]
BASE = MANIFEST["base"]
HEAD = MANIFEST["candidate"]
TREE = MANIFEST["tree"]
BRANCH = "bootstrap/one-shot-683"
REPOSITORY_ID = 1431672693
RULESET_ID = 25762292
MAX_ARCHIVE_BYTES = 2_000_000
MAX_MEMBER_BYTES = 5_000_000
MAX_LOG_BYTES = 10_000_000
TEST_MERGE_WAIT = 90
TEST_MERGE_POLL = 3
MEMBERS = {
    "python": {"results.xml", "pr-coverage.json", *coverage_evidence.PYTHON_RAW_FILES},
    "python315": {"results.xml"},
    "macos": {"results.xml"},
    "web": {"web-results.json"},
    "structural": set(),
    "security": set(),
    "sonar": set(),
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def _api(path: str, *, binary: bool = False) -> Any:  # noqa: ANN401 - GitHub returns either JSON or bytes
    # PR snapshots need merge_commit_sha, which the 2026 REST version removed.
    version = "2022-11-28" if path.startswith("pulls/") else "2026-03-10"
    command = ["gh", "api", "-H", f"X-GitHub-Api-Version: {version}", f"repos/{REPO}/{path}"]
    if not binary:
        command.extend(("--paginate", "--slurp"))
    response = subprocess.run(command, check=True, capture_output=True, timeout=60).stdout
    return response if binary else json.loads(response)


def _one(path: str) -> dict[str, Any]:
    pages = _api(path)
    _require(len(pages) == 1 and isinstance(pages[0], dict), "unexpected GitHub response")
    return pages[0]


def _inventory(path: str, key: str) -> list[dict[str, Any]]:
    pages = _api(path)
    entries = [entry for page in pages for entry in page[key]]
    _require(bool(pages) and all(page["total_count"] == len(entries) for page in pages), f"incomplete {key}")
    return entries


def _refs(expected_head: str) -> None:
    base_ref, pr_ref, validation_ref = (
        MANIFEST[key].removeprefix("refs/") for key in ("base_ref", "pr_ref", "candidate_ref")
    )
    _require(_one(f"git/ref/{base_ref}")["object"]["sha"] == BASE, "main drift")
    _require(_one(f"git/ref/{pr_ref}")["object"]["sha"] == expected_head, "PR branch drift")
    _require(_one(f"git/ref/{validation_ref}")["object"]["sha"] == HEAD, "validation ref drift")


def _pull_request(expected_head: str, *, allow_original: bool = False) -> dict[str, Any]:
    pr = _one(f"pulls/{MANIFEST['pull_request']}")
    _require(pr["state"] == "open" and not pr["draft"], "PR state drift")
    _require(pr["head"]["repo"]["full_name"] == pr["base"]["repo"]["full_name"] == REPO, "PR repository drift")
    _require(
        (pr["head"]["sha"] == expected_head or (allow_original and pr["head"]["sha"] == MANIFEST["current_pr_head"]))
        and f"refs/heads/{pr['head']['ref']}" == MANIFEST["pr_ref"]
        and f"refs/heads/{pr['base']['ref']}" == MANIFEST["base_ref"],
        "PR refs drift",
    )
    return pr


def _merge_ref(*, allow_missing: bool = False) -> str | None:
    try:
        return _one(f"git/ref/pull/{MANIFEST['pull_request']}/merge")["object"]["sha"]
    except subprocess.CalledProcessError as error:
        if not allow_missing or error.returncode != 1 or b"(HTTP 404)" not in error.stderr:
            raise
        return None


def _test_merge(pr: dict[str, Any], *, before_update: bool, wait: bool) -> None:
    original = MANIFEST["current_test_merge"]
    expected_head = MANIFEST["current_pr_head"] if before_update else HEAD
    if before_update:
        parents, tree = MANIFEST["current_test_merge_parents"], MANIFEST["current_test_merge_tree"]
        _require(pr["merge_commit_sha"] == original, "old test merge SHA drift")
    else:
        parents, tree = [BASE, HEAD], TREE
    deadline = time.monotonic() + TEST_MERGE_WAIT
    timeout = "test merge did not converge; inspect the actual PR head and ref"
    while True:
        sha = pr["merge_commit_sha"]
        ref = _merge_ref(allow_missing=wait)
        _require(not wait or time.monotonic() < deadline, timeout)
        if pr["head"]["sha"] == expected_head and sha and sha == ref and (before_update or sha != original):
            merge = _one(f"git/commits/{sha}")
            _require([parent["sha"] for parent in merge["parents"]] == parents, "test merge parents drift")
            _require(merge["tree"]["sha"] == tree, "test merge tree drift")
            return
        _require(
            wait and time.monotonic() < deadline, "test merge did not converge; inspect the actual PR head and ref"
        )
        time.sleep(TEST_MERGE_POLL)
        _refs(HEAD)
        pr = _pull_request(HEAD, allow_original=True)


def _source(*, before_update: bool = False, wait_test_merge: bool = False) -> None:
    original = MANIFEST["current_pr_head"]
    expected_head = original if before_update else HEAD
    _refs(expected_head)
    predecessor = _one("pulls/650")
    _require(predecessor["merged"] is True and bool(predecessor["merge_commit_sha"]), "#650 has not merged")
    predecessor_sha = predecessor["merge_commit_sha"]
    if predecessor_sha != BASE:
        comparison = _one(f"compare/{predecessor_sha}...{BASE}")
        _require(
            comparison["status"] == "ahead" and comparison["merge_base_commit"]["sha"] == predecessor_sha,
            "protected base does not contain #650",
        )
    pr = _pull_request(expected_head, allow_original=wait_test_merge)
    candidate = _one(f"git/commits/{HEAD}")
    _require(candidate["tree"]["sha"] == TREE, "candidate tree drift")
    _require(
        [parent["sha"] for parent in candidate["parents"]] == MANIFEST["candidate_parents"], "candidate parent drift"
    )
    comparison = _one(f"compare/{BASE}...{HEAD}?per_page=100")
    _require(comparison["merge_base_commit"]["sha"] == BASE and comparison["status"] == "ahead", "base ancestry drift")
    statuses = {"M": "modified", "A": "added"}
    _require(
        {item["filename"]: item["status"] for item in comparison["files"]}
        == {path: statuses[status] for path, status in MANIFEST["changed_paths"].items()},
        "candidate paths drift",
    )
    coverage_evidence.scope(MANIFEST)
    repair = MANIFEST["reviewed_repair"]
    test_commit = MANIFEST["coverage_test_commit"]
    test_change = _one(f"compare/{repair}...{test_commit}?per_page=100")
    _require(
        test_change["status"] == "ahead"
        and test_change["ahead_by"] == 1
        and test_change["merge_base_commit"]["sha"] == repair
        and {item["filename"]: item["status"] for item in test_change["files"]}
        == dict.fromkeys(MANIFEST["coverage_test_paths"], "modified")
        and all(path.startswith("tests/") for path in MANIFEST["coverage_test_paths"]),
        "coverage test commit changed the reviewed repair outside its tests",
    )
    ancestry = _one(f"compare/{original}...{HEAD}?per_page=100")
    _require(ancestry["merge_base_commit"]["sha"] == original and ancestry["status"] == "ahead", "original PR lost")
    _test_merge(pr, before_update=before_update, wait=wait_test_merge)
    _refs(expected_head)


def _rules() -> None:
    rules = _api("rules/branches/main?per_page=100")
    status_rules = [rule for page in rules for rule in page if rule.get("type") == "required_status_checks"]
    _require(len(status_rules) == 1 and status_rules[0]["ruleset_id"] == RULESET_ID, "required-check ruleset changed")
    parameters = status_rules[0]["parameters"]
    _require(parameters["strict_required_status_checks_policy"] is False, "strict checks policy changed")
    checks = parameters["required_status_checks"]
    expected = {"CI OK": None, "coverage-floor": 15368, "merge-gate": 15368}
    _require(
        set(expected) == set(MANIFEST["required_contexts"])
        and len(checks) == len(expected)
        and {item["context"]: item.get("integration_id") for item in checks} == expected,
        "required check bindings changed",
    )


def _web(archive: zipfile.ZipFile) -> None:
    suites = json.loads(archive.read("web-results.json"))["testResults"]
    path = MANIFEST["jobs"]["web"]["selectors"][0]
    _require(len(suites) == 1 and suites[0]["name"].endswith(f"/web/{path}"), "unexpected Vitest suite")
    cases = suites[0]["assertionResults"]
    _require(
        bool(cases)
        and suites[0]["status"] == "passed"
        and all(case["status"] == "passed" for case in cases)
        and sorted(case["fullName"] for case in cases) == (HERE / "web-collection.txt").read_text().splitlines(),
        "Vitest inventory or outcome differs",
    )


def _cases(archive: zipfile.ZipFile, lane: str) -> None:
    tests = ET.fromstring(archive.read("results.xml")).iter("testcase")  # noqa: S314 - bounded evidence from pinned job
    cases = list(tests)
    expected = (
        (HERE / "python-collection.txt").read_text().splitlines()
        if lane == "python"
        else sorted(MANIFEST["jobs"][lane]["selectors"])
    )
    actual = sorted(f"{case.attrib['classname'].replace('.', '/')}.py::{case.attrib['name']}" for case in cases)
    _require(bool(actual) and actual == expected, f"{lane} case inventory differs")
    _require(
        all(case.find(tag) is None for case in cases for tag in ("skipped", "failure", "error")),
        f"{lane} case did not pass",
    )


def _upload(job: dict[str, Any], artifact: dict[str, Any]) -> None:
    steps = [step for step in job["steps"] if step["name"] == "Upload exact attempt evidence"]
    _require(
        len(steps) == 1 and steps[0]["status"] == "completed" and steps[0]["conclusion"] == "success",
        "missing successful upload step",
    )
    number = steps[0]["number"]
    _require(
        type(number) is int and number > 0 and sum(step["number"] == number for step in job["steps"]) == 1,
        "invalid upload step number",
    )
    log = _api(f"actions/jobs/{job['id']}/steps/{number}/logs", binary=True)
    _require(len(log) < MAX_LOG_BYTES, "oversized upload log")
    pattern = re.compile(
        rf"(?:[0-9T:.Z-]+ )?Artifact {re.escape(artifact['name'])} has been successfully uploaded! "
        rf"Final size is {artifact['size_in_bytes']} bytes\. Artifact ID is {artifact['id']}"
    )
    _require(
        sum(bool(pattern.fullmatch(line)) for line in log.decode("utf-8-sig").splitlines()) == 1,
        "upload step does not bind artifact ID and size",
    )


def _artifact(artifact: dict[str, Any], lane: str, run_id: int, helper: str) -> None:
    metadata = artifact["workflow_run"]
    size = artifact["size_in_bytes"]
    _require(
        not artifact["expired"]
        and metadata["id"] == run_id
        and metadata["repository_id"] == metadata["head_repository_id"] == REPOSITORY_ID
        and metadata["head_sha"] == helper
        and 0 < size < MAX_ARCHIVE_BYTES,
        "artifact provenance or size",
    )
    blob = _api(f"actions/artifacts/{artifact['id']}/zip", binary=True)
    _require(
        len(blob) == size and artifact["digest"] == "sha256:" + hashlib.sha256(blob).hexdigest(), "artifact digest"
    )
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        names = archive.namelist()
        expected = MEMBERS[lane] | {"identity.json"}
        _require(len(names) == len(expected) and set(names) == expected, "unexpected artifact contents")
        _require(all(member.file_size < MAX_MEMBER_BYTES for member in archive.infolist()), "oversized archive member")
        identity = {
            "base": BASE,
            "head": HEAD,
            "tree": TREE,
            "lane": lane,
            "helper": helper,
            "run": str(run_id),
            "attempt": "1",
        }
        _require(json.loads(archive.read("identity.json")) == identity, "artifact identity drift")
        if lane == "web":
            _web(archive)
        if lane in {"python", "python315", "macos"}:
            _cases(archive, lane)
        if lane == "python":
            coverage_evidence.verify(MANIFEST, identity, archive.read)


def _evidence(run_id: int, helper: str) -> None:
    run = _one(f"actions/runs/{run_id}")
    _require(
        run["event"] == "push"
        and run["head_branch"] == BRANCH
        and run["run_attempt"] == 1
        and run["head_sha"] == helper
        and run["path"] == ".github/workflows/focused-683.yml"
        and run["status"] == "completed"
        and run["conclusion"] == "success"
        and run["repository"]["id"] == run["head_repository"]["id"] == REPOSITORY_ID
        and run["actor"]["login"] == run["triggering_actor"]["login"] == REPO.split("/")[0],
        "unexpected evidence workflow, source, attempt or result",
    )
    jobs = _inventory(f"actions/runs/{run_id}/attempts/1/jobs?per_page=100", "jobs")
    artifacts = _inventory(f"actions/runs/{run_id}/artifacts?per_page=100", "artifacts")
    _require(len(jobs) == len(artifacts) == len(MEMBERS), "unexpected jobs or artifacts")
    for lane in MEMBERS:
        matching = [job for job in jobs if job["name"] == f"Focused 683 {lane}"]
        _require(len(matching) == 1, f"missing or duplicate {lane} job")
        job = matching[0]
        _require(
            job["run_id"] == run_id
            and job["run_attempt"] == 1
            and job["head_sha"] == helper
            and job["status"] == "completed"
            and job["conclusion"] == "success",
            f"{lane} job evidence incomplete",
        )
        name = f"focused-683-{lane}-{run_id}-1"
        matching = [item for item in artifacts if item["name"] == name]
        _require(len(matching) == 1, f"missing or duplicate {lane} artifact")
        _upload(job, matching[0])
        _artifact(matching[0], lane, run_id, helper)


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reviewed_helper_sha")
    phase = parser.add_mutually_exclusive_group(required=True)
    phase.add_argument("--evidence", type=int, metavar="RUN_ID")
    phase.add_argument("--before-update", action="store_true")
    phase.add_argument("--after-update", action="store_true")
    parser.add_argument("--wait-test-merge", action="store_true")
    args = parser.parse_args()
    _require(not args.wait_test_merge or args.after_update, "test merge wait is only valid after the single ref update")
    _require(MANIFEST["activation"] == "ready", "admission held pending exact final-base review")
    _require(
        MANIFEST["candidate_parents"] == [BASE, MANIFEST["coverage_test_commit"]],
        "candidate must merge the coverage-tested repair into the protected base",
    )
    review = MANIFEST["source_review"]
    _require(
        review["decision"] == "GO" and (review["base"], review["head"], review["tree"]) == (BASE, HEAD, TREE),
        "source review does not match pin",
    )
    helper = args.reviewed_helper_sha
    checked_out = subprocess.check_output(("git", "-C", str(HERE), "rev-parse", "HEAD"), text=True, timeout=30).strip()
    _require(bool(re.fullmatch("[0-9a-f]{40}", helper)) and checked_out == helper, "unreviewed helper source")
    status = subprocess.check_output(("git", "-C", str(HERE), "status", "--porcelain"), text=True, timeout=30)
    _require(not status, "helper checkout is dirty")
    _require(set(MEMBERS) == set(MANIFEST["jobs"]), "manifest lane inventory drift")
    _rules()
    _source(before_update=args.before_update, wait_test_merge=args.wait_test_merge)
    if args.evidence is not None:
        _require(args.evidence > 0, "invalid evidence run ID")
        _evidence(args.evidence, helper)
        _source()
        _rules()
        print("Exact #683 evidence verified")
    else:
        print("Pinned #683 source and required rules verified")


if __name__ == "__main__":
    _main()
