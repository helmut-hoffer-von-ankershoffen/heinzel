# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Print what failed in the latest CI run of the current branch, waiting for it if it is still running.

One line for a green run; otherwise per failed job and step the error lines with a little context, without colors or
timestamps, at most 60 lines each. When the run is green but the branch's pull request is blocked, the unresolved
review threads that hold it (main requires them resolved). `--json` prints the same as a list of `{job, step, lines}`.

Usage: uv run --script scripts/ci_logs.py [--json] [--branch NAME]
"""

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from typing import Any

ANSI = re.compile(r"(?:\x1b|\^\[)\[[0-9;?]*[A-Za-z]")  # GitHub's logs spell ESC as "^["
TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ?")
ERROR = re.compile(r"^\s*E |FAILED|\berror\b:|Error:|##\[error\]|✗|Process completed with exit code")
CONTEXT, LIMIT = 1, 60
WAIT = 1800  # seconds to wait for a running build
THREADS = """query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) { pullRequest(number: $number) { reviewThreads(first: 100) {
    nodes { isResolved path line comments(first: 1) { nodes { author { login } body } } } } } } }"""


def gh(*args: str) -> str:
    """Run `gh` and return its output."""
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True, timeout=120).stdout  # noqa: S603, S607


def excerpt(lines: list[str]) -> list[str]:
    """Error lines with a little context, or the tail when nothing looks like an error."""
    keep = sorted(
        {j for i, line in enumerate(lines) if ERROR.search(line) for j in range(i - CONTEXT, i + CONTEXT + 1)}
    )
    chosen = [lines[i] for i in keep if 0 <= i < len(lines)] or lines[-LIMIT:]
    return chosen[-LIMIT:]


def unresolved_threads(response: dict[str, Any]) -> list[dict[str, Any]]:
    """The unresolved review threads of a `reviewThreads` response as report entries: path, line, author, first line."""
    report = []
    for thread in response["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]:
        if thread["isResolved"]:
            continue
        comment = (thread["comments"]["nodes"] or [{"author": None, "body": ""}])[0]
        author = (comment["author"] or {}).get("login", "ghost")
        first = next((line.lstrip("# ").strip() for line in comment["body"].splitlines() if line.strip()), "")
        where = f"{thread['path']}:{thread['line']}" if thread["line"] else thread["path"]
        report.append({"job": "review thread", "step": f"{where} ({author})", "lines": [first]})
    return report


def blocking_threads(branch: str) -> list[dict[str, Any]]:
    """The unresolved review threads of the branch's pull request when GitHub reports it blocked, else none."""
    view = ["gh", "pr", "view", branch, "--json", "number,mergeStateStatus"]
    found = subprocess.run(view, capture_output=True, text=True, check=False, timeout=120)  # noqa: S603
    if found.returncode != 0:
        return []  # no pull request for this branch
    pull = json.loads(found.stdout)
    if pull["mergeStateStatus"] != "BLOCKED":
        return []
    fields = ["-F", "owner={owner}", "-F", "repo={repo}", "-F", f"number={pull['number']}"]
    return unresolved_threads(json.loads(gh("api", "graphql", "-f", f"query={THREADS}", *fields)))


def main() -> int:
    """Find, wait for and summarize the run."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--branch")
    args = parser.parse_args()
    branch = (
        args.branch
        or subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout.strip()
    )
    runs = json.loads(
        gh("run", "list", "--branch", branch, "--workflow", "ci.yml", "--limit", "1", "--json", "databaseId,status,url")
    )
    if not runs:
        print(f"No CI run for {branch}.")
        return 1
    run_id, url = str(runs[0]["databaseId"]), runs[0]["url"]
    if runs[0]["status"] != "completed":
        print(f"Waiting for {url} …", file=sys.stderr)
        watch = ["gh", "run", "watch", run_id, "--interval", "20"]
        subprocess.run(watch, capture_output=True, check=False, timeout=WAIT)  # noqa: S603
    conclusion = json.loads(gh("run", "view", run_id, "--json", "conclusion"))["conclusion"]
    if conclusion == "success":
        threads = blocking_threads(branch)
        if args.json:
            print(json.dumps(threads, indent=2))
        else:
            print(f"✓ CI {conclusion} {url}")
            if threads:
                print(f"✗ blocked by {len(threads)} unresolved review thread(s); resolve them on the pull request")
            for entry in threads:
                print(f"  {entry['step']}: {entry['lines'][0]}")
        return 1 if threads else 0

    steps: dict[tuple[str, str], list[str]] = defaultdict(list)
    for raw in gh("run", "view", run_id, "--log-failed").splitlines():
        job, _, rest = raw.partition("\t")
        step, _, text = rest.partition("\t")
        steps[job, step].append(TIMESTAMP.sub("", ANSI.sub("", text)).rstrip())
    report = [{"job": job, "step": step, "lines": excerpt(lines)} for (job, step), lines in steps.items()]
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"✗ CI {conclusion} {url}")
        for entry in report:
            print(f"\n{entry['job']} > {entry['step']}")
            print("\n".join(f"  {line}" for line in entry["lines"]))
    return 1


if __name__ == "__main__":
    sys.exit(main())
