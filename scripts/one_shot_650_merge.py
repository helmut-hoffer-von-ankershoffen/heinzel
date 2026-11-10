# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""One-time normal merge, bound to the reviewed pull request and source.

External coordination must hold the merge queue and freeze competing base writes. GitHub's merge API binds the head,
not the base: even the final base read below cannot make that check atomic with the merge request.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

REPO = "helmut-hoffer-von-ankershoffen/heinzel"
HEAD = "9f26091bbf84ef7141fea0b0454008a6344b84d9"
BASE = "de5ebbb5b471a477c2c875e4e1effc501d021642"
TREE = "d7aca1ead0f18deab1c836d2cc0aaf124cc473c6"
RUN = 42784687184
RUN_SHA = "acdc3aa5320091e7273f0f2791e999fc3e188fd6"
RECOVERY_SHA = "1b858cb31a115ce5d7402dfa23a6cd7aee7e2321"
REPOSITORY_ID = 1431672693
PUBLISHER = "Focused 650 protected publisher"
RULESET_ID = 25762292
ACTIONS_BOT_ID = 41898282
CONTEXTS = {"CI OK": None, "merge-gate": 15368, "coverage-floor": 15368}
JOBS = {
    *(f"Focused 650 {suite} ({os})" for suite in ("python", "browser") for os in ("ubuntu-24.04", "macos-latest")),
    "Focused 650 structural",
    "Focused 650 security",
    "Focused 650 Sonar",
    PUBLISHER,
}


def api(path: str, *args: str) -> Any:  # noqa: ANN401
    """Read paginated GitHub data using fixed application-supplied paths."""
    command = ["gh", "api", "--paginate", "--slurp", f"repos/{REPO}/{path}", *args]
    return json.loads(subprocess.run(command, check=True, capture_output=True, text=True).stdout)  # noqa: S603


def one(path: str) -> dict[str, Any]:
    """Read a single response.

    Raises:
        ValueError: The API response is not a single object.
    """
    pages = api(path)
    if len(pages) != 1 or not isinstance(pages[0], dict):
        msg = f"unexpected response: {path}"
        raise ValueError(msg)
    return pages[0]


def validate() -> None:
    """Check source, PR state, base and actual associated required check runs.

    Raises:
        ValueError: Any binding or evidence is invalid.
    """
    if (
        os.environ.get("GITHUB_REPOSITORY") != REPO
        or os.environ.get("GITHUB_REF") != "refs/heads/bootstrap/one-shot-650-merge"
    ):
        msg = "unexpected repository or workflow ref"
        raise ValueError(msg)
    if not os.environ.get("REVIEWED_HELPER_SHA") or os.environ.get("GITHUB_SHA") != os.environ["REVIEWED_HELPER_SHA"]:
        msg = "helper SHA does not match the separately configured review"
        raise ValueError(msg)
    pull = one("pulls/650")
    if not (
        pull.get("state") == "open"
        and pull.get("draft") is False
        and pull.get("head", {}).get("sha") == HEAD
        and pull.get("base", {}).get("sha") == BASE
        and pull.get("base", {}).get("ref") == "main"
        and pull.get("mergeable") is True
        and pull.get("mergeable_state") == "clean"
    ):
        msg = "pull request is not the expected open, non-draft, clean head and base"
        raise ValueError(msg)
    if one("commits/main").get("sha") != BASE:
        msg = "main moved"
        raise ValueError(msg)
    if one(f"git/commits/{HEAD}").get("tree", {}).get("sha") != TREE:
        msg_0 = "candidate tree changed"
        raise ValueError(msg_0)
    merge = one(f"git/commits/{pull['merge_commit_sha']}")
    if [p.get("sha") for p in merge.get("parents", [])] != [BASE, HEAD] or merge.get("tree", {}).get("sha") != TREE:
        msg_0 = "test merge changed"
        raise ValueError(msg_0)
    recovery_id = os.environ.get("RECOVERY_RUN_ID", "")
    if not recovery_id.isascii() or not recovery_id.isdecimal() or int(recovery_id) <= 0 or int(recovery_id) == RUN:
        msg = "invalid externally pinned recovery run ID"
        raise ValueError(msg)
    validate_evidence(int(recovery_id))


def validate_evidence(recovery_id: int) -> None:
    """Verify the completed focused run, rules and statuses.

    Raises:
        ValueError: Required evidence differs or is incomplete.
    """
    run = one(f"actions/runs/{RUN}")
    if not (
        run.get("id") == RUN
        and (run.get("repository") or {}).get("id") == REPOSITORY_ID
        and (run.get("actor") or {}).get("login") == "helmut-hoffer-von-ankershoffen"
        and run.get("head_sha") == RUN_SHA
        and run.get("head_branch") == "bootstrap/one-shot-650"
        and run.get("event") == "push"
        and run.get("path") == ".github/workflows/focused-650.yml"
        and run.get("run_attempt") == 1
        and run.get("status") == "completed"
        and run.get("conclusion") == "failure"
    ):
        msg = "focused run not successful"
        raise ValueError(msg)
    pages = api(f"actions/runs/{RUN}/attempts/1/jobs?per_page=100")
    jobs = [job for page in pages for job in page["jobs"]]
    if not (
        all(page["total_count"] == len(jobs) for page in pages)
        and len(jobs) == len(JOBS)
        and {j["name"] for j in jobs} == JOBS
        and all(
            j["status"] == "completed"
            and j["conclusion"] == ("failure" if j["name"] == PUBLISHER else "success")
            and j["run_id"] == RUN
            and j["run_attempt"] == 1
            and j["head_sha"] == RUN_SHA
            for j in jobs
        )
    ):
        msg = "focused jobs not all successful"
        raise ValueError(msg)
    recovery = one(f"actions/runs/{recovery_id}")
    if not (
        recovery.get("id") == recovery_id
        and recovery.get("head_sha") == RECOVERY_SHA
        and recovery.get("head_branch") == "bootstrap/one-shot-650-publisher-recovery"
        and recovery.get("event") == "push"
        and recovery.get("path") == ".github/workflows/one-shot-650-publisher-recovery.yml"
        and recovery.get("run_attempt") == 1
        and recovery.get("status") == "completed"
        and recovery.get("conclusion") == "success"
        and (recovery.get("repository") or {}).get("id") == REPOSITORY_ID
        and (recovery.get("actor") or {}).get("login") == "helmut-hoffer-von-ankershoffen"
    ):
        msg = "recovery run identity or verdict changed"
        raise ValueError(msg)
    pages = api(f"actions/runs/{recovery_id}/attempts/1/jobs?per_page=100")
    jobs = [job for page in pages for job in page["jobs"]]
    if not (
        len(jobs) == 1
        and all(page["total_count"] == len(jobs) for page in pages)
        and jobs[0]["name"] == "recover"
        and jobs[0]["status"] == "completed"
        and jobs[0]["conclusion"] == "success"
        and jobs[0]["run_id"] == recovery_id
        and jobs[0]["run_attempt"] == 1
        and jobs[0]["head_sha"] == RECOVERY_SHA
    ):
        msg = "recovery job inventory or verdict changed"
        raise ValueError(msg)
    rules = api("rules/branches/main?per_page=100")
    required = [r for page in rules for r in page if r.get("type") == "required_status_checks"]
    if len(required) != 1 or required[0].get("ruleset_id") != RULESET_ID:
        msg = "required rules changed"
        raise ValueError(msg)
    params = required[0]["parameters"]
    checks = params["required_status_checks"]
    if (
        params.get("strict_required_status_checks_policy") is not False
        or len(checks) != len(CONTEXTS)
        or {c["context"]: c.get("integration_id") for c in checks} != CONTEXTS
    ):
        msg = "required contexts changed"
        raise ValueError(msg)
    pages = api(f"commits/{HEAD}/statuses?per_page=100")
    statuses = [status for page in pages for status in page]
    for name in CONTEXTS:
        matches = [status for status in statuses if status.get("context") == name]
        if (
            not matches
            or matches[0].get("state") != "success"
            or matches[0].get("target_url") != f"https://github.com/{REPO}/actions/runs/{recovery_id}"
            or (matches[0].get("creator") or {}).get("id") != ACTIONS_BOT_ID
        ):
            msg = f"missing or untrusted status: {name}"
            raise ValueError(msg)


def main() -> None:
    """Validate and optionally request a head-bound merge.

    Raises:
        ValueError: A precondition or merge response is invalid.
    """
    if sys.argv[1:] not in (["check"], ["merge"]):
        msg = "expected check or merge"
        raise ValueError(msg)
    validate()
    if sys.argv[1] == "merge":
        # This last read narrows, but cannot eliminate, the base movement race.
        if one("commits/main").get("sha") != BASE:
            msg = "main moved before merge"
            raise ValueError(msg)
        result = one_merge()
        if result.get("merged") is not True or not result.get("sha"):
            msg = "GitHub did not confirm a merge"
            raise ValueError(msg)
        print(f"GitHub confirmed merge: {result['sha']}")
        merged = one(f"commits/{result['sha']}")
        if [parent.get("sha") for parent in merged.get("parents", [])] != [BASE]:
            msg = "merge occurred but its parent was not the expected base; stop and investigate"
            raise ValueError(msg)
        if merged.get("commit", {}).get("tree", {}).get("sha") != TREE:
            msg_0 = "merged tree differs from reviewed tree"
            raise ValueError(msg_0)
        if one("commits/main").get("sha") != result["sha"]:
            msg = "merge occurred but main moved afterward; stop and investigate"
            raise ValueError(msg)
        print(f"Observed main and squash parent: {result['sha']} {BASE}")
    else:
        print("Exact PR and genuine required check runs validated")


def one_merge() -> dict[str, Any]:
    """Request the normal squash merge using a fixed command and fixed head."""
    result = subprocess.run(  # noqa: S603
        [  # noqa: S607
            "gh",
            "api",
            "--method",
            "PUT",
            f"repos/{REPO}/pulls/650/merge",
            "-f",
            f"sha={HEAD}",
            "-f",
            "merge_method=squash",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
