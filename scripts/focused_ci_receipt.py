"""Offline focused CI evidence validation, with no GitHub writes or activation.

The caller supplies the protected policy, current PR and default-branch data, and
the expected run identity resolved independently of the receipt. Job results must
come from the authenticated GitHub API for that exact run attempt.
"""

import hashlib
import re
from dataclasses import dataclass

GIT_SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class Change:
    """A reviewed exact diff and its required job names from protected policy."""

    base: str
    head: str
    diff_sha256: str
    selector_sha256: str
    jobs: tuple[str, ...]


@dataclass(frozen=True)
class Run:
    """Identity of the PR, tested merge tree, trusted source, and run attempt."""

    repository_id: int
    pr: int
    base: str
    head: str
    merge: str
    tree: str
    workflow_id: int
    workflow_sha256: str
    selector_sha256: str
    run_id: int
    attempt: int
    event: str


@dataclass(frozen=True)
class Receipt:
    """Results from the job listing of one run attempt, with no deduplication."""

    run: Run
    jobs: tuple[tuple[str, str], ...]


def verify(change: Change, *, current: Run, raw_diff: bytes, receipt: Receipt) -> None:
    """Verify evidence against the reviewed policy and current run.

    Raises:
        ValueError: The run, change, receipt or jobs cannot be verified.
    """
    commits = (current.base, current.head, current.merge)
    source_digests = (current.workflow_sha256, current.selector_sha256)
    if (
        not all(GIT_SHA.fullmatch(sha) and int(sha, 16) for sha in (*commits, current.tree))
        or len(set(commits)) != len(commits)
        or not all(DIGEST.fullmatch(sha) and int(sha, 16) for sha in source_digests)
        or min(current.repository_id, current.pr, current.workflow_id, current.run_id, current.attempt) < 1
        or current.event != "pull_request"
    ):
        message = "Invalid focused PR run identity"
        raise ValueError(message)

    if (change.base, change.head) != (current.base, current.head) or not raw_diff:
        message = "No reviewed change for the current base and head"
        raise ValueError(message)
    if hashlib.sha256(raw_diff).hexdigest() != change.diff_sha256:
        message = "The exact Git diff does not match the reviewed change"
        raise ValueError(message)
    if current.selector_sha256 != change.selector_sha256:
        message = "The focused run did not use the reviewed selector"
        raise ValueError(message)
    if receipt.run != current:
        message = "The focused receipt is stale or belongs to different source or a different run"
        raise ValueError(message)

    names = [name for name, _ in receipt.jobs]
    if not change.jobs or "" in change.jobs or len(set(change.jobs)) != len(change.jobs):
        message = "The reviewed job list is empty or ambiguous"
        raise ValueError(message)
    if sorted(names) != sorted(change.jobs):
        message = "The focused receipt has missing, duplicate, or unexpected jobs"
        raise ValueError(message)
    if any(conclusion != "success" for _, conclusion in receipt.jobs):
        message = "The focused receipt contains a job that did not succeed"
        raise ValueError(message)
