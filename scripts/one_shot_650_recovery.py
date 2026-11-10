# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Recover #650 statuses from the original seven successful first-attempt jobs.

The caller must hold queue/train and all competing base writers. GitHub does not
provide an expected-old compare-and-swap for the preceding ref update or merge.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import one_shot_650_publish as guard

EVIDENCE_RUN = 42784687184
EVIDENCE_SOURCE = "acdc3aa5320091e7273f0f2791e999fc3e188fd6"
RECOVERY_REF = "refs/heads/bootstrap/one-shot-650-publisher-recovery"
EXPECTED_JOB_IDS = {
    "Focused 650 python (ubuntu-24.04)": 129722868991,
    "Focused 650 structural": 129722869185,
    "Focused 650 browser (ubuntu-24.04)": 129722869251,
    "Focused 650 security": 129722869269,
    "Focused 650 Sonar": 129722869290,
    "Focused 650 browser (macos-latest)": 129722869516,
    "Focused 650 python (macos-latest)": 129722870060,
    "Focused 650 protected publisher": 129727642685,
}
PINNED_FILES = (
    "research/focused-650/source-review.json",
    "research/focused-650/python-collection.txt",
    "research/focused-650/browser-collection.txt",
    "research/focused-650/run.sh",
    ".github/workflows/focused-650.yml",
)


def git_blob(revision: str, path: str) -> str:
    """Read the object identity without executing candidate code."""
    command = ["git", "rev-parse", "--verify", f"{revision}:{path}"]
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout.strip()  # noqa: S603


def original_evidence() -> None:
    """Check the immutable original run and its complete, first-attempt job identities."""
    run = guard.one(f"actions/runs/{EVIDENCE_RUN}")
    guard.require(
        run["id"] == EVIDENCE_RUN
        and run["status"] == "completed"
        and run["conclusion"] == "failure"
        and run["event"] == "push"
        and run["run_attempt"] == 1
        and run["head_sha"] == EVIDENCE_SOURCE
        and run["head_branch"] == "bootstrap/one-shot-650"
        and run["path"] == ".github/workflows/focused-650.yml"
        and run["repository"]["id"] == guard.REPOSITORY_ID
        and run["actor"]["login"] == "helmut-hoffer-von-ankershoffen",
        "original evidence run identity drift",
    )
    pages = guard.api(f"actions/runs/{EVIDENCE_RUN}/attempts/1/jobs?per_page=100")
    jobs: list[dict[str, Any]] = [job for page in pages for job in page["jobs"]]
    guard.require(all(page["total_count"] == len(jobs) for page in pages), "incomplete original jobs")
    guard.require(len(jobs) == len(EXPECTED_JOB_IDS), "unexpected original job count")
    guard.require({job["name"]: job["id"] for job in jobs} == EXPECTED_JOB_IDS, "original job identity drift")
    for job in jobs:
        expected = "failure" if job["name"] == "Focused 650 protected publisher" else "success"
        guard.require(
            job["run_id"] == EVIDENCE_RUN
            and job["run_attempt"] == 1
            and job["head_sha"] == EVIDENCE_SOURCE
            and job["status"] == "completed"
            and job["conclusion"] == expected,
            "original job verdict or identity drift",
        )
    for path in PINNED_FILES:
        guard.require(git_blob("HEAD", path) == git_blob(EVIDENCE_SOURCE, path), f"original selection drift: {path}")
    guard.evidence(EVIDENCE_RUN, EVIDENCE_SOURCE)


def main() -> None:
    """Recheck source and evidence before publishing, without executing candidate code."""
    guard.require(os.environ.get("GITHUB_REPOSITORY") == guard.REPO, "repository drift")
    guard.require(os.environ.get("GITHUB_REF") == RECOVERY_REF, "recovery ref drift")
    guard.require(os.environ.get("GITHUB_EVENT_NAME") == "push", "recovery event drift")
    guard.require(os.environ.get("GITHUB_ACTOR") == "helmut-hoffer-von-ankershoffen", "recovery actor drift")
    guard.require(os.environ.get("GITHUB_RUN_ATTEMPT") == "1", "recovery attempt drift")
    recovery_sha = os.environ.get("REVIEWED_RECOVERY_SHA")
    guard.require(bool(recovery_sha) and os.environ.get("GITHUB_SHA") == recovery_sha, "recovery source is not pinned")
    guard.require(os.environ.get("GITHUB_WORKFLOW_SHA") == recovery_sha, "recovery workflow source drift")
    recovery_run = int(os.environ["GITHUB_RUN_ID"])
    guard.require(recovery_run != EVIDENCE_RUN, "recovery must have a distinct run identity")
    guard.source_review()
    guard.source()
    original_evidence()
    guard.source()
    guard.publish_statuses(EVIDENCE_RUN, recovery_run, EVIDENCE_SOURCE)


if __name__ == "__main__":
    try:
        main()
    except (KeyError, OSError, ValueError, TypeError, subprocess.CalledProcessError) as exc:
        print(f"Stopped; inspect PR and status state: {exc}")
        raise SystemExit(1) from exc
