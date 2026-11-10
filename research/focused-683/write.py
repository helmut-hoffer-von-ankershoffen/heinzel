# The writer uses fixed repository paths and argument vectors.
# ruff: noqa: INP001, S603, S607, T201
"""Request the reviewed one-shot ref update or publish independently verified #683 statuses."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
MANIFEST = json.loads((HERE / "manifest.json").read_text())
REPO = MANIFEST["repository"]
HEAD = MANIFEST["candidate"]
ACTIONS_BOT = 41898282
CONTEXTS = ("CI OK", "merge-gate", "coverage-floor")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _api(path: str, *args: str) -> Any:  # noqa: ANN401 - status reads are arrays and writes are objects
    command = ["gh", "api", "-H", "X-GitHub-Api-Version: 2026-03-10", f"repos/{REPO}/{path}", *args]
    return json.loads(subprocess.run(command, check=True, capture_output=True, timeout=30).stdout)


def _verify(helper: str, *phase: str) -> None:
    subprocess.run(("python3", "-I", str(HERE / "verify.py"), helper, *phase), check=True, timeout=360)


def _update(helper: str) -> None:
    _verify(helper, "--before-update")
    target = MANIFEST["pr_ref"].removeprefix("refs/")
    original = MANIFEST["current_pr_head"]
    # This API has no expected-old precondition. External writer serialization is required.
    _require(_api(f"git/ref/{target}")["object"]["sha"] == original, "PR ref moved before update")
    base = MANIFEST["base_ref"].removeprefix("refs/")
    _require(_api(f"git/ref/{base}")["object"]["sha"] == MANIFEST["base"], "main moved before update")
    response = _api(f"git/refs/{target}", "--method", "PATCH", "-f", f"sha={HEAD}", "-F", "force=false")
    _require(response["object"]["sha"] == HEAD, "GitHub did not confirm the target ref")
    _verify(helper, "--after-update")
    print(f"Observed fast-forward {original} -> {HEAD}; protected base and test merge verified")


def _status(context: str, state: str, url: str) -> None:
    description = {
        "CI OK": "Exact reviewed #683 focused evidence",
        "merge-gate": "Exact #683 Sonar and security evidence",
        "coverage-floor": "Measured Python and TypeScript module coverage >=80%",
    }[context]
    response = _api(
        f"statuses/{HEAD}",
        "--method",
        "POST",
        "-f",
        f"context={context}",
        "-f",
        f"state={state}",
        "-f",
        f"target_url={url}",
        "-f",
        f"description={description}",
    )
    _require(
        response["context"] == context
        and response["state"] == state
        and response["target_url"] == url
        and type(response["id"]) is int
        and response["id"] > 0,
        f"{context} write was not confirmed",
    )
    pages = _api(f"commits/{HEAD}/statuses?per_page=100", "--paginate", "--slurp")
    matching = [item for page in pages for item in page if item["context"] == context]
    _require(bool(matching), f"{context} status missing after write")
    latest = matching[0]
    _require(
        latest["id"] == response["id"]
        and latest["state"] == state
        and latest["target_url"] == url
        and latest["creator"]["id"] == ACTIONS_BOT
        and latest["creator"]["login"] == "github-actions[bot]",
        f"{context} status readback drift",
    )


def _publish(helper: str) -> None:
    run = os.environ.get("FOCUSED_EVIDENCE_RUN_ID", "")
    _require(bool(re.fullmatch(r"[1-9][0-9]{0,19}", run)), "missing pinned focused evidence run")
    _verify(helper, "--evidence", run)
    url = f"https://github.com/{REPO}/actions/runs/{run}"
    attempted = []
    try:
        for context in CONTEXTS:
            attempted.append(context)
            _status(context, "pending", url)
        # Verify the current source and rules between writes. The evidence run itself is already terminal.
        for context in ("coverage-floor", "merge-gate", "CI OK"):
            _verify(helper, "--after-update")
            _status(context, "success", url)
        _verify(helper, "--after-update")
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
        for context in attempted:
            try:
                _status(context, "failure", url)
            except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
                print(f"Could not invalidate {context}. Inspect GitHub status: {error}")
        raise
    print(f"Observed all three exact #683 status writes for {HEAD}")


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("update", "publish"))
    mode = parser.parse_args().mode
    _require(MANIFEST["activation"] == "ready", "admission held pending exact final-base review")
    helper = os.environ.get("REVIEWED_HELPER_SHA", "")
    _require(
        bool(re.fullmatch(r"[0-9a-f]{40}", helper))
        and os.environ.get("GITHUB_REPOSITORY") == REPO
        and os.environ.get("GITHUB_ACTOR") == REPO.split("/")[0]
        and os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("GITHUB_EVENT_NAME") == "push"
        and os.environ.get("GITHUB_REF") == f"refs/heads/bootstrap/one-shot-683-{mode}"
        and os.environ.get("GITHUB_RUN_ATTEMPT") == "1"
        and os.environ.get("GITHUB_SHA") == os.environ.get("GITHUB_WORKFLOW_SHA") == helper,
        "unreviewed helper or unexpected execution context",
    )
    if mode == "update":
        _update(helper)
    else:
        _publish(helper)


if __name__ == "__main__":
    try:
        _main()
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
        message = f"Stopped. Inspect actual GitHub state. {error}"
        raise SystemExit(message) from error
