"""Collect exact focused PR evidence from GitHub without writing a check or executing PR code."""

from __future__ import annotations

import hashlib
import re
import subprocess
import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from focused_ci_receipt import DIGEST, GIT_SHA, Change, Receipt, Run, verify

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

PAGE_SIZE = 100
MAX_RESULTS = 1000  # GitHub can truncate filtered run listings at this boundary.
POLICY = "scripts/focused_ci_transition.toml"
SELECTOR = "scripts/focused_ci_receipt.py"
WORKFLOW = re.compile(r"\.github/workflows/[\w-]+\.ya?ml", re.ASCII)


def require(condition: bool, message: str) -> None:
    """Reject incomplete or inconsistent evidence.

    Raises:
        ValueError: The supplied assertion failed.
    """
    if not condition:
        raise ValueError(message)


def positive(value: object) -> bool:
    """True for a positive GitHub integer identity."""
    return type(value) is int and value > 0


def sha(value: object) -> bool:
    """True for a nonzero SHA-1 Git object ID."""
    return isinstance(value, str) and bool(GIT_SHA.fullmatch(value)) and int(value, 16) != 0


@dataclass(frozen=True)
class Source:
    """Pins from protected policy. The app publishes workload check suites, not the required verdict."""

    repository_id: int
    workflow_id: int
    workflow_path: str
    workflow_sha256: str
    selector_sha256: str
    workload_app_id: int

    def __post_init__(self) -> None:
        """Validate the protected source pins before making any requests."""
        require(
            all(positive(n) for n in (self.repository_id, self.workflow_id, self.workload_app_id))
            and bool(WORKFLOW.fullmatch(self.workflow_path))
            and all(
                DIGEST.fullmatch(value) and int(value, 16) for value in (self.workflow_sha256, self.selector_sha256)
            ),
            "Invalid protected workflow or publisher pins",
        )


@dataclass(frozen=True)
class Trigger:
    """An exact run attempt from the trusted caller's workflow event."""

    pr: int
    head: str
    run_id: int
    attempt: int


def git_bytes(*args: str) -> bytes:
    """Read Git objects without checking out the PR, following replacement objects or running a diff driver."""
    return subprocess.check_output(  # noqa: S603 - Git object operations with validated identities only
        ["git", "--no-replace-objects", *args],  # noqa: S607 - installed Git
        timeout=30,
    )


def pages(read: Callable[[str], Any], path: str, key: str) -> list[dict[str, Any]]:
    """Read and reconcile every result page, preserving the original job names."""
    found: list[dict[str, Any]] = []
    count = None
    separator = "&" if "?" in path else "?"
    for page in range(1, MAX_RESULTS // PAGE_SIZE + 1):
        payload = read(f"{path}{separator}per_page={PAGE_SIZE}&page={page}")
        total = payload["total_count"]
        require(type(total) is int and 0 <= total < MAX_RESULTS, "GitHub result count is invalid or may be truncated")
        count = total if count is None else count
        batch = payload[key]
        require(
            total == count and isinstance(batch, list) and len(batch) == min(PAGE_SIZE, count - len(found)),
            "GitHub result pages are incomplete or changed while reading",
        )
        found.extend(batch)
        if len(found) == count:
            break
    ids = [item["id"] for item in found]
    require(all(positive(item) for item in ids) and len(set(ids)) == len(ids), "Duplicate or invalid GitHub result IDs")
    return found


def pull_snapshot(read: Callable[[str], Any], source: Source, pr: int, head: str) -> tuple[str, str]:
    """Bind an open same-repository PR to the current protected base and GitHub's test merge ref."""
    pull = read(f"pulls/{pr}")
    base = read("git/ref/heads/main")["object"]["sha"]
    merge = read(f"git/ref/pull/{pr}/merge")["object"]["sha"]
    require(
        pull["number"] == pr
        and pull["state"] == "open"
        and pull["draft"] is False
        and pull["base"]["ref"] == "main"
        and pull["base"]["repo"]["id"] == pull["head"]["repo"]["id"] == source.repository_id
        and pull["base"]["sha"] == base
        and pull["head"]["sha"] == head
        and pull["merge_commit_sha"] == merge
        and all(sha(item) for item in (base, head, merge)),
        "The open pull request, protected base or merge ref changed",
    )
    return base, merge


def run_identity(run: dict[str, Any], source: Source, pr: int, *, base: str, head: str) -> int:
    """Bind one completed PR workflow run to its repository, workflow and event refs."""
    [association] = run["pull_requests"]
    require(
        run["workflow_id"] == source.workflow_id
        and run["path"] == source.workflow_path
        and run["repository"]["id"] == run["head_repository"]["id"] == source.repository_id
        and run["head_sha"] == head
        and run["event"] == "pull_request"
        and run["status"] == "completed"
        and run["conclusion"] == "success"
        and association["number"] == pr
        and association["base"]["ref"] == "main"
        and association["base"]["repo"]["id"] == association["head"]["repo"]["id"] == source.repository_id
        and association["base"]["sha"] == base
        and association["head"]["sha"] == head
        and positive(run["check_suite_id"]),
        "The workflow identity, event, status or recorded PR refs did not match",
    )
    return run["check_suite_id"]


def job_results(read: Callable[[str], Any], source: Source, run: Run, suite_id: int) -> tuple[tuple[str, str], ...]:
    """Read the exact attempt's jobs and corroborate each with its GitHub Actions check."""
    suite = read(f"check-suites/{suite_id}")
    require(
        suite["id"] == suite_id
        and suite["app"]["id"] == source.workload_app_id
        and suite["head_sha"] == run.head
        and suite["status"] == "completed"
        and suite["conclusion"] == "success",
        "The check suite did not come from the pinned workload publisher",
    )
    jobs = pages(read, f"actions/runs/{run.run_id}/attempts/{run.attempt}/jobs", "jobs")
    for job in jobs:
        check = read(f"check-runs/{job['id']}")
        require(
            job["run_id"] == run.run_id
            and job["run_attempt"] == run.attempt
            and job["head_sha"] == run.head
            and job["status"] == check["status"] == "completed"
            and check["id"] == job["id"]
            and check["check_suite"]["id"] == suite_id
            and check["app"]["id"] == source.workload_app_id
            and check["head_sha"] == run.head
            and job["name"] == check["name"]
            and job["conclusion"] == check["conclusion"],
            "A job is from a different attempt, commit, check suite or publisher",
        )
    return tuple((job["name"], job["conclusion"]) for job in jobs)


def collect(
    read: Callable[[str], Any],
    policy: Path,
    trigger: Trigger,
    *,
    git: Callable[..., bytes] = git_bytes,
) -> Receipt:
    """Verify a protected policy against current GitHub data and immutable local Git objects.

    The checkout must contain the protected base, head and merge objects, with only protected code running.
    The run ID and attempt come from a trusted trigger. This collector produces no merge authorization.
    """
    policy_bytes = policy.read_bytes()
    manifest = tomllib.loads(policy_bytes.decode())
    source = Source(**manifest.pop("source"))
    pr, head, run_id, attempt = trigger.pr, trigger.head, trigger.run_id, trigger.attempt
    require(sha(head) and all(positive(n) for n in (pr, run_id, attempt)), "Invalid focused trigger identity")
    base, merge = pull_snapshot(read, source, pr, head)
    require(policy_bytes == git("cat-file", "blob", f"{base}:{POLICY}"), "The policy is not the current protected copy")
    [entry] = [row for row in manifest.values() if row["base"] == base and row["head"] == head]
    change = Change(**entry, selector_sha256=source.selector_sha256)
    workflow = read(f"actions/workflows/{source.workflow_id}")
    require(
        workflow["id"] == source.workflow_id
        and workflow["path"] == source.workflow_path
        and workflow["state"] == "active",
        "The registered workflow does not match protected policy",
    )
    git("merge-base", "--is-ancestor", base, head)
    parents = git("rev-list", "--parents", "-n", "1", merge).decode().split()
    tree = git("rev-parse", f"{merge}^{{tree}}").decode().strip()
    require(
        parents == [merge, base, head] and tree == git("rev-parse", f"{head}^{{tree}}").decode().strip(),
        "GitHub's merge object does not have the expected parents and tree",
    )
    for path, digest in ((source.workflow_path, source.workflow_sha256), (SELECTOR, source.selector_sha256)):
        require(
            all(
                hashlib.sha256(git("cat-file", "blob", f"{commit}:{path}")).hexdigest() == digest
                for commit in (base, merge)
            ),
            "The protected and tested workflow or selector bytes do not match policy",
        )
    raw_diff = git("diff-tree", "--no-commit-id", "-r", "--raw", "--no-abbrev", "--no-renames", "-z", base, head)

    listing = f"actions/workflows/{source.workflow_id}/runs?head_sha={head}&event=pull_request"
    [latest] = pages(read, listing, "workflow_runs")
    require(latest["id"] == run_id and latest["run_attempt"] == attempt, "A newer run or attempt replaced the trigger")
    exact = read(f"actions/runs/{run_id}/attempts/{attempt}")
    require(exact["id"] == run_id and exact["run_attempt"] == attempt, "GitHub returned a different run attempt")
    suite_id = run_identity(exact, source, pr, base=base, head=head)
    require(run_identity(latest, source, pr, base=base, head=head) == suite_id, "The latest run names another suite")
    current = Run(
        source.repository_id,
        pr,
        base,
        head,
        merge,
        tree,
        source.workflow_id,
        source.workflow_sha256,
        source.selector_sha256,
        run_id,
        attempt,
        "pull_request",
    )
    receipt = Receipt(current, job_results(read, source, current, suite_id))
    verify(change, current=current, raw_diff=raw_diff, receipt=receipt)
    require(
        pages(read, listing, "workflow_runs") == [latest] and pull_snapshot(read, source, pr, head) == (base, merge),
        "The run or pull request changed during collection",
    )
    return receipt
