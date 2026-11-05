# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Decide the one next step of promoting to stable (ADR 0114); the workflow gathers the facts and takes the step.

Each scheduled run advances one step, so a failed or interrupted run just tries again from the facts:
  accept_edge -> open_pr -> (merged by auto-merge) -> tag -> accept -> finalize -> upload -> (composer) -> verify

Usage: uv run --script scripts/release_promotion.py < facts.json
"""

import json
import sys
from dataclasses import dataclass
from typing import Literal

PYPI_LIMIT = 10 * 1024**3
PYPI_BUDGET = 0.8
RELEASE_SIZE = 12 * 1024**2
MIN_HOURS = 6.0

type Acceptance = Literal["none", "running", "passed", "failed"]


@dataclass(frozen=True)
class Facts:
    """What is true now, read from GitHub and PyPI by the workflow."""

    main_version: str
    """The version in pyproject.toml on main's tip."""
    tag_exists: bool
    """`v<main_version>` exists."""
    release_final: bool
    """Its GitHub release exists and is final; held as a pre-release counts as False."""
    release_held: bool
    """Its GitHub release exists as a pre-release."""
    on_pypi: bool
    """`<main_version>` has files on PyPI."""
    acceptance: Acceptance
    """Acceptance of the held final bytes of `v<main_version>`."""
    public_acceptance: Acceptance
    """The literal public commands after the upload."""
    release_pr_open: bool
    """A `chore(release):` pull request is open."""
    shipping_commits: int
    """Commits since `v<main_version>` that reach users."""
    edge_acceptance: Acceptance
    """Acceptance of the newest edge release on hosted macOS and Linux."""
    app_composed: bool
    """`app-v<main_version>` exists, so acceptance and the public commands have its profile."""
    edge_app_composed: bool
    """The composer has made the newest edge release's app, so acceptance has its profile."""
    hours_since_stable: float
    pypi_bytes: int
    """What the project already uses on PyPI."""


def next_step(facts: Facts) -> tuple[str, str]:  # noqa: PLR0911 - one line per state
    """The next action and why; `wait` and `stop` change nothing, and only `upload` is irreversible."""
    if not facts.tag_exists:
        return "tag", "main carries a release commit without its tag"
    if not facts.on_pypi:
        return _before_upload(facts)
    if facts.public_acceptance == "failed":
        return "stop", "the public commands failed after the upload; fix forward with the next version"
    if not facts.app_composed:
        return "wait", "the composer has not made this version's standard app yet"
    if facts.public_acceptance == "none":
        return "verify", "the upload has not been checked through the public commands"
    if facts.public_acceptance == "running":
        return "wait", "the public commands are being checked"
    return _start(facts)


def _before_upload(facts: Facts) -> tuple[str, str]:
    if facts.release_held:
        return {
            "passed": ("finalize", "the held bytes passed acceptance"),
            "failed": ("stop", "acceptance rejected the held release, so it is not promoted; fix forward"),
            "running": ("wait", "acceptance of the held release is running"),
            "none": ("accept", "the held release has no acceptance result")
            if facts.app_composed
            else ("wait", "the composer has not made the held release's app yet"),
        }[facts.acceptance]
    if not facts.release_final:
        return "wait", "the tag has no release yet"
    if facts.acceptance != "passed":
        return "stop", "a final release without passing acceptance must not reach PyPI; a person decides"
    return "upload", "the final release passed acceptance and is not on PyPI"


def _start(facts: Facts) -> tuple[str, str]:  # noqa: PLR0911 - one line per condition
    if facts.release_pr_open:
        return "wait", "a release pull request is open"
    if facts.shipping_commits < 1:
        return "wait", "nothing new reaches users"
    if facts.edge_acceptance == "none":
        if not facts.edge_app_composed:
            return "wait", "the composer has not made the newest edge release's app yet"
        return "accept_edge", "the newest edge release has no acceptance result"
    if facts.edge_acceptance != "passed":
        return "wait", f"acceptance of the newest edge release: {facts.edge_acceptance}"
    if facts.hours_since_stable < MIN_HOURS:
        return "wait", f"at most one stable per {MIN_HOURS:g} hours"
    if facts.pypi_bytes + RELEASE_SIZE > PYPI_LIMIT * PYPI_BUDGET:
        return "stop", "PyPI budget used up: trim the source distribution or ask PyPI for more"
    return "open_pr", f"{facts.shipping_commits} commits reach users, and edge passed acceptance"


def main() -> int:
    """Read the facts on standard input; print `step=` and `reason=` lines for GITHUB_OUTPUT."""
    step, reason = next_step(Facts(**json.load(sys.stdin)))
    print(f"step={step}")
    print(f"reason={reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
