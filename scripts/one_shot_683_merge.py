# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""One-time normal merge, bound to the reviewed pull request and source.

External coordination must hold the merge queue and freeze competing base writes. GitHub's merge API binds the head,
not the base: even the final base read below cannot make that check atomic with the merge request.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = "helmut-hoffer-von-ankershoffen/heinzel"
HEAD = "7d983e9662f58fd3af2c779c3ca1a9ccf8a70576"
BASE = "6443f4b8cde047954cdd4a6eb190ce6471ce123e"
TREE = "0722838ec82ed417118ee8d388ffeec0335c330e"
PR_NUMBER = 683
PUBLISHER_RUN_ID = 42801566747
PUBLISHER_JOBS = {"publish": 129780118307, "update": 129780120657}
RECOVERY_RUN_ID = 42801046287
RECOVERY_SHA = "ad3b8961cd44596f0bc0b5a9e1a965f5772e6589"
REPOSITORY_ID = 1431672693
RULESET_ID = 25762292
ACTIONS_BOT_ID = 41898282
PR_SNAPSHOT_BASE = "5fe18ec552e50bd4487ebf54ad1292b015f4bd3b"
UNSTABLE_SUITES = {
    115906268240: (42798140322, ".github/workflows/claude-review.yml"),
    115906268320: (42798140344, ".github/workflows/ci.yml"),
    115906269005: (42798140654, ".github/workflows/codeql.yml"),
}
CONTEXTS = {"CI OK": None, "merge-gate": 15368, "coverage-floor": 15368}
STATUS_IDS = {"CI OK": 58515146064, "merge-gate": 58515138753, "coverage-floor": 58515132055}


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
        or os.environ.get("GITHUB_REF") != "refs/heads/bootstrap/one-shot-683-merge"
    ):
        msg = "unexpected repository or workflow ref"
        raise ValueError(msg)
    if not os.environ.get("REVIEWED_HELPER_SHA") or os.environ.get("GITHUB_SHA") != os.environ["REVIEWED_HELPER_SHA"]:
        msg = "helper SHA does not match the separately configured review"
        raise ValueError(msg)
    pull = one("pulls/683")
    if not (
        pull.get("state") == "open"
        and pull.get("draft") is False
        and pull.get("head", {}).get("sha") == HEAD
        and pull.get("base", {}).get("ref") == "main"
        and pull.get("mergeable") is True
        and pull.get("mergeable_state") in {"clean", "unstable"}
    ):
        msg = "pull request is not the expected open, non-draft, mergeable head and base"
        raise ValueError(msg)
    if one("commits/main").get("sha") != BASE:
        msg = "main moved"
        raise ValueError(msg)
    if one(f"git/commits/{HEAD}").get("tree", {}).get("sha") != TREE:
        msg_0 = "candidate tree changed"
        raise ValueError(msg_0)
    merge_ref = one("git/ref/pull/683/merge")
    merge = one(f"git/commits/{merge_ref['object']['sha']}")
    if [p.get("sha") for p in merge.get("parents", [])] != [BASE, HEAD] or merge.get("tree", {}).get("sha") != TREE:
        msg_0 = "test merge changed"
        raise ValueError(msg_0)
    validate_evidence()
    validate_unstable()


def exact_pull_association(item: dict[str, Any]) -> bool:
    """Match the observed PR, source head and base association."""
    return any(
        pr.get("number") == PR_NUMBER
        and (pr.get("head") or {}).get("sha") == HEAD
        and (pr.get("base") or {}).get("sha") == PR_SNAPSHOT_BASE
        for pr in item.get("pull_requests", [])
    )


def validate_unstable() -> None:
    """Allow only the observed PR-associated action-required suites with no jobs.

    Raises:
        ValueError: A suite or its run differs from the observed condition.
    """
    pages = api(f"commits/{HEAD}/check-suites?per_page=100")
    suites = [suite for page in pages for suite in page["check_suites"]]
    if not pages or not all(page["total_count"] == len(suites) for page in pages):
        msg = "incomplete check suite inventory"
        raise ValueError(msg)
    relevant = [
        s
        for s in suites
        if any(pr.get("number") == PR_NUMBER for pr in s.get("pull_requests", []))
        and (s.get("status") != "completed" or s.get("conclusion") != "success")
    ]
    if len(relevant) != len(UNSTABLE_SUITES) or {s["id"] for s in relevant} != set(UNSTABLE_SUITES):
        msg = "unexpected PR-associated abnormal suite"
        raise ValueError(msg)
    for suite in relevant:
        run_id, workflow = UNSTABLE_SUITES[suite["id"]]
        if not (
            suite.get("head_sha") == HEAD
            and suite.get("status") == "completed"
            and suite.get("conclusion") == "action_required"
            and (suite.get("app") or {}).get("slug") == "github-actions"
            and suite.get("latest_check_runs_count") == 0
            and exact_pull_association(suite)
        ):
            msg = "unexpected action-required suite identity"
            raise ValueError(msg)
        run = one(f"actions/runs/{run_id}")
        if not (
            run.get("id") == run_id
            and run.get("check_suite_id") == suite["id"]
            and run.get("head_sha") == HEAD
            and run.get("event") == "pull_request"
            and run.get("path") == workflow
            and run.get("run_attempt") == 1
            and run.get("status") == "completed"
            and run.get("conclusion") == "action_required"
            and (run.get("repository") or {}).get("id") == REPOSITORY_ID
            and (run.get("head_repository") or {}).get("id") == REPOSITORY_ID
            and exact_pull_association(run)
            and (run.get("actor") or {}).get("id") == ACTIONS_BOT_ID
            and (run.get("triggering_actor") or {}).get("id") == ACTIONS_BOT_ID
        ):
            msg = "unexpected action-required run identity"
            raise ValueError(msg)
        jobs = api(f"actions/runs/{run_id}/attempts/1/jobs?per_page=100")
        if not jobs or any(page.get("total_count") != 0 or page.get("jobs") != [] for page in jobs):
            msg = "action-required run acquired jobs"
            raise ValueError(msg)


def validate_evidence() -> None:
    """Verify both focused attempts, the publisher, rules and latest statuses.

    Raises:
        ValueError: The evidence differs from the reviewed pins.
    """
    evidence_dir = Path(__file__).resolve().parents[1] / "research" / "focused-683"
    sys.path.insert(0, str(evidence_dir))
    spec = importlib.util.spec_from_file_location("focused_683_verify", evidence_dir / "verify.py")
    if spec is None or spec.loader is None:
        msg = "missing focused verifier"
        raise ValueError(msg)
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    verifier._rules()  # noqa: SLF001
    verifier._source()  # noqa: SLF001
    verifier._evidence(RECOVERY_RUN_ID, RECOVERY_SHA)  # noqa: SLF001
    validate_publisher()


def validate_publisher() -> None:
    """Check the exact successful publisher and its latest statuses.

    Raises:
        ValueError: Publisher evidence differs.
    """
    publisher_id = PUBLISHER_RUN_ID
    run = one(f"actions/runs/{publisher_id}")
    if not (
        run.get("id") == publisher_id
        and run.get("head_sha") == RECOVERY_SHA
        and run.get("head_branch") == "bootstrap/one-shot-683-publish"
        and run.get("event") == "push"
        and run.get("path") == ".github/workflows/one-shot-683-write.yml"
        and run.get("run_attempt") == 1
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
        and (run.get("repository") or {}).get("id") == REPOSITORY_ID
        and (run.get("head_repository") or {}).get("id") == REPOSITORY_ID
        and (run.get("actor") or {}).get("login") == REPO.split("/", maxsplit=1)[0]
        and (run.get("triggering_actor") or {}).get("login") == REPO.split("/", maxsplit=1)[0]
    ):
        msg = "publisher run identity or outcome differs"
        raise ValueError(msg)
    pages = api(f"actions/runs/{publisher_id}/attempts/1/jobs?per_page=100")
    jobs = [job for page in pages for job in page["jobs"]]
    if not (
        pages
        and all(page["total_count"] == len(jobs) for page in pages)
        and len(jobs) == len(PUBLISHER_JOBS)
        and {j["name"] for j in jobs} == {"publish", "update"}
        and all(
            j["status"] == "completed"
            and j["run_id"] == publisher_id
            and j["run_attempt"] == 1
            and j["head_sha"] == RECOVERY_SHA
            for j in jobs
        )
        and any(
            j["name"] == "publish"
            and j["id"] == PUBLISHER_JOBS["publish"]
            and j["conclusion"] == "success"
            and (j.get("runner_id") or 0) > 0
            for j in jobs
        )
        and any(
            j["name"] == "update"
            and j["id"] == PUBLISHER_JOBS["update"]
            and j["conclusion"] == "skipped"
            and j.get("runner_id") is None
            and j["steps"] == []
            for j in jobs
        )
    ):
        msg = "publisher jobs differ"
        raise ValueError(msg)
    pages = api(f"commits/{HEAD}/statuses?per_page=100")
    statuses = [status for page in pages for status in page]
    for name in CONTEXTS:
        matches = [status for status in statuses if status.get("context") == name]
        if not (
            matches
            and matches[0].get("id") == STATUS_IDS[name]
            and matches[0].get("state") == "success"
            and matches[0].get("target_url") == f"https://github.com/{REPO}/actions/runs/{publisher_id}"
            and (matches[0].get("creator") or {}).get("id") == ACTIONS_BOT_ID
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
        print(
            "Resulting main still requires focused validation on the observed SHA: "
            "tests/unit/kernel/uv_locate_test.py, tests/integration/update_uv_minimal_path_test.py, "
            "web/src/updates/updateState.test.ts; do not mark complete until observed."
        )
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
            f"repos/{REPO}/pulls/683/merge",
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
    except subprocess.CalledProcessError as exc:
        detail = str(exc.stderr or "")
        for key in ("GH_TOKEN", "GITHUB_TOKEN"):
            token = os.environ.get(key, "")
            if token:
                detail = detail.replace(token, "[redacted]")
        detail = detail[:2000] or f"exit code {exc.returncode}"
        print(f"Refused: GitHub command failed: {detail}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (OSError, ValueError, KeyError) as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
