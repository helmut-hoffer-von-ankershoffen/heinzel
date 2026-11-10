# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Draft verifier for the reviewed 650 run. Publication remains hard-held pending review."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

REPO = "helmut-hoffer-von-ankershoffen/heinzel"
BASE = "b234067b172f33db59f98c9ea38808b3d5ab9bc5"
HEAD = "37ea58445ac9ac73f9b698de3bd852a2821e8501"
TREE = "0f14c4c296ab267b89eef6eec59e28644d01d68b"
BRANCH = "bootstrap/one-shot-650"
ROOT = Path(__file__).resolve().parents[1]
MAX_ARCHIVE_BYTES = 2_000_000
MAX_MEMBER_BYTES = 1_000_000
MAX_LOG_BYTES = 10_000_000
EXPECTED_MEMBERS = {"identity.txt", "results.xml"}
CONTEXTS = ("merge-gate", "coverage-floor", "CI OK")
GITHUB_ACTIONS_APP = 15368
EXPECTED_JOBS = {
    *(
        f"Focused 650 {suite} ({os_name})"
        for suite in ("python", "browser")
        for os_name in ("ubuntu-24.04", "macos-latest")
    ),
    "Focused 650 structural",
    "Focused 650 security",
    "Focused 650 Sonar",
}


def api(path: str, *, binary: bool = False) -> Any:  # noqa: ANN401
    """Fetch from GitHub with pagination; binary is only for an artifact archive."""
    command = ["gh", "api", f"repos/{REPO}/{path}"]
    if not binary:
        command.extend(["--paginate", "--slurp"])
    result = subprocess.run(command, check=True, capture_output=True)  # noqa: S603
    return result.stdout if binary else json.loads(result.stdout)


def one(path: str) -> dict[str, Any]:
    """Read a single GitHub object.

    Raises:
        ValueError: GitHub returned an unexpected response.
    """
    result = api(path)
    if len(result) != 1 or not isinstance(result[0], dict):
        msg = "unexpected GitHub response"
        raise ValueError(msg)
    return result[0]


def require(condition: bool, message: str) -> None:
    """Reject an unmet identity or evidence constraint.

    Raises:
        ValueError: A required constraint failed.
    """
    if not condition:
        raise ValueError(message)


def source() -> None:
    """Recheck main, pull request and current test-merge identity."""
    require(one("git/ref/heads/main")["object"]["sha"] == BASE, "main drift")
    require(one("git/ref/heads/fix/five-flakes")["object"]["sha"] == HEAD, "PR branch ref drift")
    pull = one("pulls/650")
    require(pull["state"] == "open" and not pull["draft"] and pull["base"]["ref"] == "main", "PR state drift")
    require(pull["head"]["sha"] == HEAD and pull["head"]["ref"] == "fix/five-flakes", "PR head drift")
    require(pull["head"]["repo"]["full_name"] == REPO and pull["base"]["repo"]["full_name"] == REPO, "repo drift")
    require(one(f"git/commits/{HEAD}")["tree"]["sha"] == TREE, "candidate tree drift")
    comparison = one(f"compare/{BASE}...{HEAD}?per_page=100")
    expected = {
        "scripts/related_tests.py",
        "tests/unit/scoped_checks_test.py",
        "web/e2e/chat-ready.mjs",
        "web/e2e/device-reopen.mjs",
        "web/e2e/generic-agent-chat.mjs",
    }
    require({item["filename"] for item in comparison["files"]} == expected, "changed path scope drift")
    require(all(item["status"] == "modified" for item in comparison["files"]), "changed path status drift")

    merge = one(f"git/commits/{pull['merge_commit_sha']}")
    require([parent["sha"] for parent in merge["parents"]] == [BASE, HEAD], "test merge parents drift")
    require(merge["tree"]["sha"] == TREE, "test merge tree drift")


def source_review() -> None:
    """Check the review receipt carried by the independently pinned bootstrap source."""
    receipt = json.loads((ROOT / "research/focused-650/source-review.json").read_text())
    require(
        receipt
        == {
            "schema": 1,
            "head": HEAD,
            "base": BASE,
            "tree": TREE,
            "reviewer": "independent-policy-review",
            "decision": "GO",
            "scope": (
                "candidate source and import-selection repair; no workflow, status publisher or hosted verification"
            ),
        },
        "source review receipt mismatch",
    )


def evidence(run_id: int) -> None:
    """Verify the exact attempt's job inventory and bounded artifact contents."""
    run = one(f"actions/runs/{run_id}")
    require(run["event"] == "push" and run["head_branch"] == BRANCH, "workflow event or ref drift")
    require(run["head_sha"] == os.environ["REVIEWED_HELPER_SHA"] and run["run_attempt"] == 1, "workflow source drift")
    require(run["path"] == ".github/workflows/focused-650.yml", "workflow path drift")
    pages = api(f"actions/runs/{run_id}/attempts/1/jobs?per_page=100")
    jobs = [job for page in pages for job in page["jobs"]]
    require(all(page["total_count"] == len(jobs) for page in pages), "incomplete jobs inventory")
    require(
        {job["name"] for job in jobs} == EXPECTED_JOBS | {"Focused 650 protected publisher"},
        "unexpected job inventory",
    )
    for name in EXPECTED_JOBS:
        matching = [job for job in jobs if job["name"] == name]
        require(
            len(matching) == 1 and matching[0]["status"] == "completed" and matching[0]["conclusion"] == "success",
            f"missing successful job: {name}",
        )
        job = matching[0]
        require(job["run_id"] == run_id and job["run_attempt"] == 1, "job run or attempt mismatch")
        require(job["head_sha"] == os.environ["REVIEWED_HELPER_SHA"], "job source mismatch")
        if name.startswith(("Focused 650 python (", "Focused 650 browser (")):
            uploads = [step for step in matching[0]["steps"] if step["name"] == "Upload exact attempt evidence"]
            require(len(uploads) == 1 and uploads[0]["conclusion"] == "success", "missing successful artifact upload")
    pages = api(f"actions/runs/{run_id}/artifacts?per_page=100")
    artifacts = [item for page in pages for item in page["artifacts"]]
    require(all(page["total_count"] == len(artifacts) for page in pages), "incomplete artifacts inventory")
    for suite in ("python", "browser"):
        for os_name in ("ubuntu-24.04", "macos-latest"):
            name = f"focused-650-{suite}-{os_name}-{run_id}-1"
            found = [item for item in artifacts if item["name"] == name and not item["expired"]]
            require(len(found) == 1, f"missing or duplicate artifact: {name}")
            artifact = found[0]
            job_name = f"Focused 650 {suite} ({os_name})"
            job = next(item for item in jobs if item["name"] == job_name)
            log = api(f"actions/jobs/{job['id']}/logs", binary=True)
            require(len(log) < MAX_LOG_BYTES, "oversized upload log")
            text = log.decode("utf-8", errors="replace")
            pattern = (
                rf"{re.escape(name)}.*?successfully uploaded! Final size is "
                rf"{artifact['size_in_bytes']} bytes\. Artifact ID is {artifact['id']}\b"
            )
            require(re.search(pattern, text, re.DOTALL) is not None, "artifact ID and size missing from job log")
            require(
                artifact["workflow_run"]["id"] == run_id
                and artifact["workflow_run"]["repository_id"] == one("")["id"]
                and artifact["workflow_run"]["head_sha"] == os.environ["REVIEWED_HELPER_SHA"]
                and 0 < artifact["size_in_bytes"] < MAX_ARCHIVE_BYTES,
                "artifact provenance or size",
            )
            archive = api(f"actions/artifacts/{artifact['id']}/zip", binary=True)
            require(
                artifact.get("digest") == "sha256:" + hashlib.sha256(archive).hexdigest(), "artifact digest mismatch"
            )
            with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
                names = zipped.namelist()
                require(
                    len(names) == len(EXPECTED_MEMBERS) and set(names) == EXPECTED_MEMBERS, "artifact files mismatch"
                )
                require(
                    all(item.file_size < MAX_MEMBER_BYTES for item in zipped.infolist()), "oversized artifact content"
                )
                identity_lines = [line.split("=", 1) for line in zipped.read("identity.txt").decode().splitlines()]
                identity = dict(identity_lines)
                require(len(identity) == len(identity_lines), "duplicate identity key")
                require(
                    identity
                    == {"base": BASE, "head": HEAD, "tree": TREE, "run": str(run_id), "attempt": "1", "job": "focused"},
                    "artifact identity mismatch",
                )
                cases = list(ET.fromstring(zipped.read("results.xml")).iter("testcase"))  # noqa: S314 - bounded artifact from reviewed runner
            actual = [case.attrib["classname"].replace(".", "/") + ".py::" + case.attrib["name"] for case in cases]
            expected = (ROOT / f"research/focused-650/{suite}-collection.txt").read_text().splitlines()
            require(len(actual) == len(expected) and set(actual) == set(expected), "test collection mismatch")
            require(
                all(not any(case.find(tag) is not None for tag in ("skipped", "failure", "error")) for case in cases),
                "test skipped or failed",
            )


def publish_statuses(run_id: int) -> None:
    """Publish and read back only after all evidence passes; caller must hold all base writers.

    Raises:
        ValueError: A required context or status readback did not match.
        OSError: The GitHub client could not start.
        subprocess.CalledProcessError: GitHub rejected a request.
    """
    rules = api("rules/branches/main?per_page=100")
    checks = [
        check
        for page in rules
        for rule in page
        if rule.get("type") == "required_status_checks"
        for check in rule.get("parameters", {}).get("required_status_checks", [])
    ]
    for context in CONTEXTS:
        matching = [check for check in checks if check.get("context") == context]
        require(bool(matching), f"required context missing: {context}")
        if context != "CI OK":
            require(
                all(item.get("integration_id") == GITHUB_ACTIONS_APP for item in matching),
                "required app binding changed",
            )
    url = f"https://github.com/{REPO}/actions/runs/{run_id}"

    def write(context: str, state: str) -> None:
        command = [
            "gh",
            "api",
            "--method",
            "POST",
            f"repos/{REPO}/statuses/{HEAD}",
            "-f",
            f"state={state}",
            "-f",
            f"context={context}",
            "-f",
            f"target_url={url}",
            "-f",
            "description=Exact reviewed focused 650 evidence",
        ]
        response = json.loads(subprocess.run(command, check=True, capture_output=True).stdout)  # noqa: S603
        require(response.get("context") == context and response.get("state") == state, "status write not confirmed")
        require(response.get("target_url") == url, "status write URL mismatch")
        statuses = one(f"commits/{HEAD}/status?per_page=100")["statuses"]
        matching = [status for status in statuses if status.get("context") == context]
        require(bool(matching) and matching[0].get("state") == state, "status readback mismatch")
        require(matching[0].get("target_url") == url, "status readback URL mismatch")
        require(matching[0].get("creator", {}).get("login") == "github-actions[bot]", "status creator mismatch")

    for context in ("CI OK", "merge-gate", "coverage-floor"):
        write(context, "pending")
    try:
        for context in CONTEXTS:
            source()
            evidence(run_id)
            write(context, "success")
        source()
    except (ValueError, OSError, subprocess.CalledProcessError):
        for context in CONTEXTS:
            write(context, "failure")
        raise


def main() -> None:
    """Check exact source and attempt; intentionally do not publish statuses.

    Raises:
        ValueError: Publication is held pending review.
    """
    require(
        os.environ.get("GITHUB_REPOSITORY") == REPO and os.environ.get("GITHUB_REF") == f"refs/heads/{BRANCH}",
        "repository or ref drift",
    )
    require(
        os.environ.get("GITHUB_ACTOR") == "helmut-hoffer-von-ankershoffen"
        and os.environ.get("GITHUB_RUN_ATTEMPT") == "1",
        "actor or attempt drift",
    )
    require(
        bool(os.environ.get("REVIEWED_HELPER_SHA"))
        and os.environ.get("GITHUB_SHA") == os.environ["REVIEWED_HELPER_SHA"],
        "unreviewed helper",
    )
    run_id = int(os.environ["GITHUB_RUN_ID"])
    source_review()
    source()
    evidence(run_id)
    source()
    msg = "publication held: verifier and external serialization require independent review"
    raise ValueError(msg)


if __name__ == "__main__":
    try:
        main()
    except (KeyError, OSError, ValueError, subprocess.CalledProcessError, zipfile.BadZipFile) as exc:
        print(f"Stopped without publishing: {exc}")
        raise SystemExit(1) from exc
