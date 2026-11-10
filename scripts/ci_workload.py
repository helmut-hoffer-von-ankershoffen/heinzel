# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Plan CI workloads from exact reviewed scope without running jobs or publishing checks."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from scripts.release_candidate import REPOSITORY, CandidateError, expected_jobs, release_version, require, valid_sha

CHANGE_EVENTS = frozenset({"pull_request", "push"})
BASELINE_EVENTS = frozenset({"workflow_dispatch", "schedule"})


@dataclass(frozen=True, slots=True)
class WorkloadSource:
    """The independently resolved Actions context and immutable Git diff."""

    event: str
    repository: str
    ref: str
    payload: dict[str, Any]
    base: str
    head: str
    raw_diff: bytes


@dataclass(frozen=True, slots=True)
class FocusedPlan:
    """Exact source scope and job names supplied by independently authenticated policy."""

    event: str
    ref: str
    base: str
    head: str
    diff_sha256: str
    jobs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CandidatePlan:
    """Full candidate mode with the verifier's fixed job inventory and coverage floor."""

    version: str
    expected_jobs: tuple[tuple[str, str], ...]  # The verifier checks the conditional quarantine lane separately.
    coverage_floor: int = 80


def plan_workload(
    source: WorkloadSource,
    *,
    focused: FocusedPlan | None = None,
    dispatch_pr: dict[str, Any] | None = None,
) -> FocusedPlan | CandidatePlan:
    """Select a canonical candidate or an exact reviewed workload for the current source.

    The caller supplies the authenticated Actions context with the full ref,
    independently resolved Git objects and detached policy. The exact selector
    uses `git diff-tree --no-commit-id -r --raw --no-abbrev --no-renames -z BASE HEAD`.
    Dispatch has no PR payload, so a candidate dispatch requires the
    independently resolved open release PR for this exact source.
    A plan is scheduling input. Required checks and candidate verification still
    consume their own evidence, including the independently approved source pins.

    Raises:
        CandidateError: The event, exact source or reviewed workload is unavailable.
    """
    event, repository, ref = source.event, source.repository, source.ref
    base, head, raw_diff = source.base, source.head, source.raw_diff
    require(
        repository == REPOSITORY and event in CHANGE_EVENTS | BASELINE_EVENTS and ref.startswith("refs/"),
        "unsupported CI context",
    )
    require(event not in {"push", "schedule"} or ref == "refs/heads/main", "CI event must be on main")
    require(
        all(valid_sha(sha) and sha != "0" * 40 for sha in (base, head)),
        "CI requires exact source commits",
    )
    if event == "pull_request":
        pr = source.payload.get("pull_request")
        require(isinstance(pr, dict), "missing pull request event metadata")
        number = pr.get("number")
        pr_base, pr_head = pr.get("base"), pr.get("head")
        require(
            type(number) is int
            and number > 0
            and ref == f"refs/pull/{number}/merge"
            and isinstance(pr_base, dict)
            and isinstance(pr_head, dict)
            and pr_base.get("sha") == base
            and pr_head.get("sha") == head,
            "pull request event differs from the exact ref and source",
        )
    version = release_version(event, repository, ref.removeprefix("refs/heads/"), source.payload)
    if version:
        pr = source.payload["pull_request"] if event == "pull_request" else dispatch_pr or {}
        require(
            base != head
            and pr.get("state") == "open"
            and pr.get("draft") is False
            and pr.get("head", {}).get("sha") == head
            and pr.get("base", {}).get("sha") == base
            and release_version("pull_request", repository, "", {"pull_request": pr}) == version,
            "candidate requires the canonical open release PR at the exact source",
        )
        return CandidatePlan(version, tuple(sorted(expected_jobs(event).items())))

    require(
        (base != head and bool(raw_diff)) or (event in BASELINE_EVENTS and base == head and not raw_diff),
        "focused CI requires an exact change or a reviewed dispatch or schedule baseline",
    )
    if focused is None or (focused.event, focused.ref, focused.base, focused.head, focused.diff_sha256) != (
        event,
        ref,
        base,
        head,
        hashlib.sha256(raw_diff).hexdigest(),
    ):
        message = "no reviewed focused workload for this exact event, ref and diff"
        raise CandidateError(message)
    require(
        focused.jobs
        and all(job and job == job.strip() and job.isprintable() for job in focused.jobs)
        and len(set(focused.jobs)) == len(focused.jobs),
        "reviewed focused jobs must be nonempty and unique",
    )
    require(
        all(job != "Coverage" and not job.startswith("Test (") for job in focused.jobs),
        "the complete Python matrix and coverage jobs are reserved for release candidates",
    )
    return focused
