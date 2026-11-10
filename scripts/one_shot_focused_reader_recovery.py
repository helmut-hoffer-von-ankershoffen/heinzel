# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Rejudge only exact focused evidence from approved recovery source."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = "helmut-hoffer-von-ankershoffen/heinzel"
BASE = "85f8f31cebd9456430b62f4be7c96f1305905b72"
CURRENT_MAIN = "01205988a97da9c5362a20c5723485d191696208"
READER = "d8cbd3240b535c7e42853ea331274f220b2cf21d"
READER_TREE = "88f7dcd4f5a0e8c34db963dce6f668ca61662f47"
REF = "refs/heads/bootstrap/one-shot-focused-reader-recovery"
WORKFLOW = ".github/workflows/one-shot-focused-reader-recovery.yml"
RULESET = 25762292
CI_WORKFLOW_ID = 394704163
ACTIONS_APP_ID = 15368
ORPHAN_695 = 129856829308
ORPHAN_PR = 695
type Target = tuple[int, str, str, str, str, int, int, int, int, int]
TARGETS = (
    (
        695,
        "3a16bd23c094323fa5889981b99dc8a9a3808f57",
        "fix/installer-504-api-fallback",
        "e22169d67e55789d4b11b2719d8d4175d7bcc62e",
        "b338be1e31812dddd6ce3d48db6ba7d4d27efb1a",
        42813791157,
        115947973571,
        115947973571,
        129823251303,
        129823243721,
    ),
    (
        696,
        "be0163fe8dd5a2e9249004bf31e5d32ff9aeef1d",
        "fix/notebook-git-health-admission",
        "8f71152c38cafe78bd2d13e937e7d74ef484a861",
        "b19fdb0d477bb25d70711e439b1be1aa5eae3bb9",
        42815836573,
        115953479871,
        115953479517,
        129833545233,
        129833540335,
    ),
)
PLAN_DIGESTS = {
    "3a16bd23c094323fa5889981b99dc8a9a3808f57": "7e544f8af53ded87c40c301eabbdbbfa2c79e87514842137852158d30abc17a1",
    "be0163fe8dd5a2e9249004bf31e5d32ff9aeef1d": "b2a79400f21ef66cb0b28dcae2cb94dd786e6a3df244eb788e0ab34570bcffb8",
}
CONTEXTS = {"CI OK": None, "merge-gate": 15368, "coverage-floor": 15368}


def require(condition: bool, message: str) -> None:
    """Fail closed if an exact identity differs.

    Raises:
        ValueError: An identity differs.
    """
    if not condition:
        raise ValueError(message)


def run(args: list[str], **kwargs: Any) -> str:  # noqa: ANN401
    """Run a fixed reviewed command and capture its output."""
    result = subprocess.run(args, check=True, capture_output=True, text=True, timeout=1800, **kwargs)  # noqa: S603
    return result.stdout.strip()


def api(path: str) -> Any:  # noqa: ANN401
    """Read the fixed repository through the GitHub API."""
    return json.loads(run(["gh", "api", f"repos/{REPO}/{path}"]))


def root_commit(path: Path, revision: str, tree: str | None = None) -> None:
    """Require the trusted checkout to match its immutable source."""
    require(run(["git", "-C", str(path), "rev-parse", "HEAD"]) == revision, "checkout commit mismatch")
    if tree:
        require(run(["git", "-C", str(path), "rev-parse", "HEAD^{tree}"]) == tree, "checkout tree mismatch")
    require(not run(["git", "-C", str(path), "status", "--porcelain"]), "dirty trusted checkout")


def only_recovery_run() -> None:
    """Refuse a second fresh run for the same immutable activation commit."""
    source = os.environ["RECOVERY_SHA"]
    page = api(f"actions/workflows/{Path(WORKFLOW).name}/runs?head_sha={source}&event=push&per_page=100")
    runs = page["workflow_runs"]
    require(
        page["total_count"] == 1
        and len(runs) == 1
        and runs[0]["id"] == int(os.environ["GITHUB_RUN_ID"])
        and runs[0]["run_attempt"] == 1,
        "recovery activation is not unique",
    )


def current(target: Target, owned_check_id: int | None = None, *, allow_pending_orphan: bool = False) -> None:
    """Recheck the PR, original evidence, current main and ruleset."""
    only_recovery_run()
    number, head, branch, merge, tree, run_id, suite, protected_suite, failed_check, green_check = target
    pr = api(f"pulls/{number}")
    require(
        pr["number"] == number
        and pr["state"] == "open"
        and pr["draft"] is False
        and pr["head"]["sha"] == head
        and pr["head"]["ref"] == branch
        and pr["base"]["ref"] == "main"
        and pr["base"]["sha"] == BASE
        and pr["head"]["repo"]["full_name"] == pr["base"]["repo"]["full_name"] == REPO,
        "PR moved",
    )
    for check_id, name, conclusion in (
        (failed_check, "coverage-floor", "failure"),
        (green_check, "merge-gate", "success"),
    ):
        check = api(f"check-runs/{check_id}")
        require(
            check["id"] == check_id
            and check["name"] == name
            and check["head_sha"] == head
            and check["app"]["id"] == ACTIONS_APP_ID
            and check["check_suite"]["id"] == protected_suite
            and check["status"] == "completed"
            and check["conclusion"] == conclusion,
            "original protected check changed",
        )
    latest = api(f"commits/{head}/check-runs?check_name=coverage-floor&filter=latest&per_page=100")
    required = [
        item
        for item in latest["check_runs"]
        if item.get("name") == "coverage-floor" and item.get("app", {}).get("id") == ACTIONS_APP_ID
    ]
    expected = owned_check_id if owned_check_id is not None else failed_check
    # Accept the historical orphan only after its exact identity has been invalidated.
    if owned_check_id is None and number == ORPHAN_PR and len(required) == 1 and required[0].get("id") == ORPHAN_695:
        orphan = api(f"check-runs/{ORPHAN_695}")
        if allow_pending_orphan and orphan.get("status") == "in_progress":
            orphan_identity(orphan, target, "in_progress", None)
        else:
            orphan_identity(orphan, target, "completed", "failure")
        expected = ORPHAN_695
    require(
        latest["total_count"] == len(latest["check_runs"])
        and len(required) == 1
        and required[0]["id"] == expected
        and (
            owned_check_id is not None
            or required[0]["conclusion"] == "failure"
            or (allow_pending_orphan and expected == ORPHAN_695 and required[0]["conclusion"] is None)
        ),
        "latest required coverage check changed",
    )
    require(api("git/ref/heads/main")["object"]["sha"] == CURRENT_MAIN, "main moved")
    require(
        api("git/ref/heads/bootstrap/one-shot-focused-reader-recovery")["object"]["sha"] == os.environ["RECOVERY_SHA"],
        "recovery ref moved",
    )
    require(api(f"git/ref/heads/{branch}")["object"]["sha"] == head, "head ref moved")
    commit = api(f"git/commits/{merge}")
    require(
        commit["sha"] == merge
        and commit["tree"]["sha"] == tree
        and [p["sha"] for p in commit["parents"]] == [BASE, head],
        "test merge changed",
    )
    ci = api(f"actions/runs/{run_id}")
    require(
        ci["id"] == run_id
        and ci["run_attempt"] == 1
        and ci["check_suite_id"] == suite
        and ci["head_sha"] == head
        and ci["head_branch"] == branch
        and ci["path"] == ".github/workflows/ci.yml"
        and ci["workflow_id"] == CI_WORKFLOW_ID
        and ci["event"] == "pull_request"
        and ci["status"] == "completed"
        and ci["conclusion"] == "success"
        and ci["repository"]["full_name"] == ci["head_repository"]["full_name"] == REPO
        and ci["display_title"] == f"CI {merge}",
        "original CI changed",
    )
    newer = api(f"actions/workflows/394704163/runs?head_sha={head}&event=pull_request&per_page=1")["workflow_runs"]
    require(len(newer) == 1 and newer[0]["id"] == run_id, "CI superseded")
    rules = [r for r in api("rules/branches/main") if r["type"] == "required_status_checks"]
    require(
        len(rules) == 1 and rules[0]["ruleset_id"] == RULESET and rules[0]["ruleset_source"] == REPO, "ruleset changed"
    )
    params = rules[0]["parameters"]
    rows = params["required_status_checks"]
    require(
        len(rows) == len(CONTEXTS)
        and {r["context"]: r.get("integration_id") for r in rows} == CONTEXTS
        and params["strict_required_status_checks_policy"] is False,
        "required contexts changed",
    )


def external_id(target: Target) -> str:
    """Identify this exact run and target without relying on a mutable details URL."""
    return (
        f"focused-recovery:{os.environ['GITHUB_RUN_ID']}:{os.environ['GITHUB_RUN_ATTEMPT']}:"
        f"{target[0]}:{os.environ['RECOVERY_SHA']}"
    )


def valid_url(url: str, actual_id: int, expected_url: str) -> bool:
    """Accept only the run URL or GitHub's canonical URL for this exact check."""
    return url in {expected_url, f"https://github.com/{REPO}/runs/{actual_id}"}


def orphan_identity(check: dict[str, Any], target: Target, status: str, conclusion: str | None) -> None:
    """Authenticate the one historical pre-external-ID orphan before any mutation."""
    require(
        target[0] == ORPHAN_PR
        and check.get("id") == ORPHAN_695
        and check.get("name") == "coverage-floor"
        and check.get("head_sha") == target[1]
        and check.get("app", {}).get("id") == ACTIONS_APP_ID
        and check.get("check_suite", {}).get("id") == target[7]
        and check.get("status") == status
        and check.get("conclusion") == conclusion
        and check.get("external_id") == ""
        and check.get("details_url") == f"https://github.com/{REPO}/runs/{ORPHAN_695}",
        "historical orphan identity differs",
    )


def invalidate_historical_orphan(target: Target) -> None:
    """Invalidate the uniquely pinned orphan; never touch original checks."""
    old = api(f"check-runs/{ORPHAN_695}")
    if old.get("status") == "completed" and old.get("conclusion") == "failure":
        orphan_identity(old, target, "completed", "failure")
        return
    orphan_identity(old, target, "in_progress", None)
    response = json.loads(
        run(
            [
                "gh",
                "api",
                "--method",
                "PATCH",
                f"repos/{REPO}/check-runs/{ORPHAN_695}",
                "-f",
                "status=completed",
                "-f",
                "conclusion=failure",
                "-f",
                "output[title]=Recovery invalidated",
                "-f",
                "output[summary]=Prior recovery did not complete",
            ]
        )
    )
    orphan_identity(response, target, "completed", "failure")
    orphan_identity(api(f"check-runs/{ORPHAN_695}"), target, "completed", "failure")


def cleanup_historical_orphan() -> None:
    """Perform the separately requested fixed-ID repair under the approved host and hold."""
    host()
    target = selected_target()
    require(target[0] == ORPHAN_PR, "historical orphan applies only to PR 695")
    current(target, allow_pending_orphan=True)
    require(api("git/ref/heads/main")["object"]["sha"] == CURRENT_MAIN, "main moved")
    require(api(f"git/ref/heads/{target[2]}")["object"]["sha"] == target[1], "head moved")
    original = api(f"check-runs/{target[8]}")
    require(
        original.get("id") == target[8]
        and original.get("head_sha") == target[1]
        and original.get("name") == "coverage-floor"
        and original.get("app", {}).get("id") == ACTIONS_APP_ID
        and original.get("check_suite", {}).get("id") == target[7]
        and original.get("status") == "completed"
        and original.get("conclusion") == "failure",
        "original protected check changed",
    )
    invalidate_historical_orphan(target)
    require(api("git/ref/heads/main")["object"]["sha"] == CURRENT_MAIN, "main moved")
    require(api(f"git/ref/heads/{target[2]}")["object"]["sha"] == target[1], "head moved")


def publish(target: Target, check_id: int | None, state: str, url: str, created: list[int] | None = None) -> int:
    """Publish the genuine coverage judge outcome as an Actions check run."""
    head = target[1]
    require(state in {"pending", "success", "failure"}, "unexpected check state")
    if check_id is None:
        require(state == "pending", "a new check must begin in progress")
        args = [
            "gh",
            "api",
            "--method",
            "POST",
            f"repos/{REPO}/check-runs",
            "-f",
            "name=coverage-floor",
            "-f",
            f"head_sha={head}",
            "-f",
            "status=in_progress",
            "-f",
            f"details_url={url}",
            "-f",
            f"external_id={external_id(target)}",
        ]
    else:
        require(
            check_id not in {target[8], target[9]} and check_id > 0,
            "refusing to modify original checks",
        )
        existing = api(f"check-runs/{check_id}")
        require(
            existing.get("id") == check_id
            and existing.get("name") == "coverage-floor"
            and existing.get("head_sha") == head
            and existing.get("app", {}).get("id") == ACTIONS_APP_ID
            and existing.get("external_id") == external_id(target)
            and type(existing.get("check_suite", {}).get("id")) is int
            and valid_url(existing.get("details_url", ""), check_id, url),
            "check ownership differs",
        )
        require(state != "pending", "an existing check cannot be recreated")
        args = [
            "gh",
            "api",
            "--method",
            "PATCH",
            f"repos/{REPO}/check-runs/{check_id}",
            "-f",
            "status=completed",
            "-f",
            f"conclusion={state}",
            "-f",
            f"output[title]={state}",
            "-f",
            f"output[summary]=Reviewed coverage judge: {state}",
        ]
    response = json.loads(run(args))
    actual_id = response["id"]
    if (
        created is not None
        and check_id is None
        and type(actual_id) is int
        and actual_id > 0
        and actual_id not in {target[8], target[9]}
    ):
        created.append(actual_id)
    require(
        type(actual_id) is int
        and actual_id > 0
        and actual_id not in {target[8], target[9]}
        and (check_id is None or actual_id == check_id),
        "check identity differs",
    )
    expected_status = "in_progress" if state == "pending" else "completed"
    expected_conclusion = None if state == "pending" else state
    require(
        response["name"] == "coverage-floor"
        and response["head_sha"] == head
        and response["app"]["id"] == ACTIONS_APP_ID
        and type(response["check_suite"]["id"]) is int
        and response["status"] == expected_status
        and response["conclusion"] == expected_conclusion
        and response.get("external_id") == external_id(target)
        and valid_url(response["details_url"], actual_id, url),
        "check publication identity differs",
    )
    suite = api(f"check-suites/{response['check_suite']['id']}")
    require(
        suite["id"] == response["check_suite"]["id"]
        and suite["head_sha"] == head
        and suite["app"]["id"] == ACTIONS_APP_ID,
        "published check suite differs",
    )
    latest = api(f"check-runs/{actual_id}")
    require(
        latest["id"] == actual_id
        and latest["name"] == "coverage-floor"
        and latest["head_sha"] == head
        and latest["app"]["id"] == ACTIONS_APP_ID
        and latest["check_suite"]["id"] == response["check_suite"]["id"]
        and latest["status"] == expected_status
        and latest["conclusion"] == expected_conclusion
        and latest.get("external_id") == external_id(target)
        and valid_url(latest["details_url"], actual_id, url),
        "check readback differs",
    )
    if state == "success":
        checks = api(f"commits/{head}/check-runs?check_name=coverage-floor&filter=latest&per_page=100")
        rows = [
            item
            for item in checks["check_runs"]
            if item["name"] == "coverage-floor" and item["app"]["id"] == ACTIONS_APP_ID
        ]
        require(
            checks["total_count"] == len(checks["check_runs"])
            and len(rows) == 1
            and rows[0]["id"] == actual_id
            and rows[0]["conclusion"] == "success",
            "latest required check differs",
        )
    return actual_id


def check_plan(head: str, raw: str) -> None:
    """Require the exact independently reviewed focused plan."""
    registry = json.loads(raw)
    plan = registry["pull_request:" + head]
    digest = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    require(digest == PLAN_DIGESTS[head], "reviewed focused plan changed")


def host() -> str:
    """Require the uniquely activated source, runner and coordinator hold."""
    env = os.environ
    source = env.get("RECOVERY_SHA", "")
    require(
        re.fullmatch(r"[0-9a-f]{40}", source) is not None
        and source == env.get("GITHUB_SHA") == env.get("GITHUB_WORKFLOW_SHA")
        and env.get("GITHUB_ACTIONS") == "true"
        and env.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and env.get("GITHUB_REPOSITORY") == REPO
        and env.get("GITHUB_EVENT_NAME") == "push"
        and env.get("GITHUB_REF") == REF
        and env.get("GITHUB_WORKFLOW_REF") == f"{REPO}/{WORKFLOW}@{REF}"
        and env.get("GITHUB_ACTOR") == REPO.partition("/")[0]
        and env.get("GITHUB_RUN_ATTEMPT") == "1"
        and env.get("RECOVERY_WRITER_HOLD") == CURRENT_MAIN,
        "unapproved recovery host",
    )
    root_commit(Path.cwd(), source)
    only_recovery_run()
    return f"https://github.com/{REPO}/actions/runs/{env['GITHUB_RUN_ID']}"


def selected_target() -> Target:
    """Require a reviewed fixed target for this one-shot run."""
    number = os.environ.get("RECOVERY_TARGET", "")
    matches = [target for target in TARGETS if str(target[0]) == number]
    require(len(matches) == 1, "unapproved recovery target")
    return matches[0]


def cleanup() -> None:
    """Invalidate only a uniquely owned recovery check after an unsuccessful job."""
    target_url = host()
    target = selected_target()
    head = target[1]
    page = api(f"commits/{head}/check-runs?check_name=coverage-floor&filter=all&per_page=100")
    require(page["total_count"] == len(page["check_runs"]), "incomplete cleanup check inventory")
    rows = [row for row in page["check_runs"] if row.get("external_id") == external_id(target)]
    require(len(rows) <= 1, "ambiguous recovery-owned checks")
    if not rows:
        print("No recovery-owned check exists", flush=True)
        return
    row = rows[0]
    require(
        row["name"] == "coverage-floor"
        and row["head_sha"] == head
        and row["app"]["id"] == ACTIONS_APP_ID
        and type(row["check_suite"]["id"]) is int
        and row["id"] not in {target[8], target[9]},
        "cleanup cannot establish ownership",
    )
    publish(target, row["id"], "failure", target_url)
    print(f"Invalidated recovery-owned check {row['id']}", flush=True)


def main(protected: Path, reader: Path) -> None:
    """Run the one-shot recovery with independently pinned judges.

    Raises:
        RuntimeError: An attempted status could not be invalidated.
    """
    env = os.environ
    target_url = host()
    root_commit(protected, BASE)
    root_commit(reader, READER, READER_TREE)
    target = selected_target()
    check_plan(target[1], env["FOCUSED_CI_PLANS"])
    if target[0] == ORPHAN_PR:
        cleanup_historical_orphan()
    current(target)
    number, head, _, _, _, ci_run, _, _, _, _ = target
    current(target)
    check_id = None
    created: list[int] = []
    try:
        # Sonar is rechecked, but its existing passing check is preserved.
        judge_env = {
            **env,
            "PR_NUMBER": str(number),
            "PR_HEAD_SHA": head,
            "CI_RUN_ID": str(ci_run),
            "PYTHONPATH": str(reader),
        }
        with tempfile.TemporaryDirectory(prefix="focused-recovery-") as scratch:
            judge_env["GITHUB_OUTPUT"] = str(Path(scratch) / "out")
            run(["python3", "-I", "scripts/merge_gate.py", "sonar"], cwd=protected, env=judge_env)
            verdict = run(
                ["uv", "run", "--no-build", "--frozen", "--script", "scripts/focused_gate.py"],
                cwd=reader,
                env=judge_env,
            )
            print(f"coverage-floor: {verdict}", flush=True)
            current(target)
            check_id = publish(target, None, "pending", target_url, created)
            current(target, check_id)
            publish(target, check_id, "success", target_url)
            current(target, check_id)
    except Exception as original:
        if check_id is None and created:
            check_id = created[0]
        if check_id is not None:
            try:
                publish(target, check_id, "failure", target_url)
            except Exception as error:  # noqa: BLE001 - report the exact failed invalidation
                message = f"Check invalidation failed: {type(error).__name__}; check {check_id}"
                print(message, file=sys.stderr, flush=True)
                raise RuntimeError(message) from original
        raise


if __name__ == "__main__":
    if sys.argv[1:] == ["--cleanup"]:
        cleanup()
    elif sys.argv[1:] == ["--cleanup-orphan-695"]:
        cleanup_historical_orphan()
    else:
        main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
