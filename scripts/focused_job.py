# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Run exactly one focused test selection on a hosted runner (`.github/workflows/focused-job.yml`).

A failure that only shows on a hosted runner (macOS end-to-end, say) used to need a full CI cycle to check a fix. The
focused-job workflow runs one selection on the requested runner instead. Its inputs reach this script through the
environment only, are validated here, and become argument lists: nothing is passed through a shell.

    FOCUSED_KIND      e2e | pytest | vitest
    FOCUSED_SELECTOR  e2e: a lane (cli, supervised, web-1, web-2) or test ids under tests/e2e/;
                      pytest: test ids under tests/; vitest: test files under web/ (with or without the `web/` prefix).
                      Up to 10 space-separated ids; an id is a path with an optional `::name[param]`.
    FOCUSED_OUT       where the JUnit report and screenshots go (default focused-artifacts), uploaded by the workflow

Usage: uv run --quiet --script scripts/focused_job.py [--check]
    --check   validate the inputs and print the plan, run nothing
"""

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KINDS = ("e2e", "pytest", "vitest")
LANES = ("cli", "supervised", "web-1", "web-2")
MAX_IDS = 10
# A path, then optionally `::test_name` and a parametrize id; never a leading `-` (no option injection), never `..`.
TOKEN = re.compile(r"\w[\w./]{0,199}(::\w{1,120}(\[[\w.,=+-]{1,80}\])?){0,2}", re.ASCII)
SERIAL = ("-n", "0", "-q", "-p", "no:cacheprovider", "--session-timeout=900")  # one selection, no worker noise
TIMEOUT = 1080  # under the job's 20 minutes, so the report and screenshots are still written and uploaded


class SelectorError(ValueError):
    """The inputs are not a selection this workflow runs."""

    def __init__(self, kind: str, problem: str) -> None:
        """Name the kind of selection and what is wrong with it."""
        super().__init__(f"{kind}: {problem}")


def _paths(selector: str, prefix: str, kind: str) -> list[str]:
    ids = selector.split()
    if not ids:
        problem = "the selector is empty"
        raise SelectorError(kind, problem)
    if len(ids) > MAX_IDS:
        problem = f"at most {MAX_IDS} ids, got {len(ids)}"
        raise SelectorError(kind, problem)
    for token in ids:
        if not TOKEN.fullmatch(token) or ".." in token:
            problem = f"{token!r} is not a test id (path[::name[param]])"
            raise SelectorError(kind, problem)
        path = token.split("::", 1)[0]
        if not path.startswith(prefix):
            problem = f"{token!r} must be under {prefix}"
            raise SelectorError(kind, problem)
        if not (ROOT / path).exists():
            problem = f"{path} does not exist on this ref"
            raise SelectorError(kind, problem)
    return ids


def plan(kind: str, selector: str, out: Path) -> list[list[str]]:
    """The commands that run the selection, in order.

    Raises:
        SelectorError: the inputs are not a selection this workflow runs.
    """
    selector = selector.strip()
    junit = f"--junitxml={out / 'junit.xml'}"
    if kind == "e2e":
        if selector in LANES:
            return [["make", "test-e2e", f"SUITE={selector}"]]
        ids = _paths(selector, "tests/e2e/", kind)
        pytest = ["uv", "run", "pytest", "-m", "e2e", *SERIAL]
        return [["make", "e2e-prepare"], [*pytest, junit, *ids]]
    if kind == "pytest":
        ids = _paths(selector, "tests/", kind)
        sync = ["uv", "sync", "--frozen", "--no-build", "--no-dev", "--group", "test"]
        pytest = ["uv", "run", "--no-sync", "pytest", *SERIAL]
        return [sync, [*pytest, junit, *ids]]
    if kind == "vitest":
        ids = _paths(" ".join(f"web/{token.removeprefix('web/')}" for token in selector.split()), "web/", kind)
        web = ["uv", "run", "--quiet", "--script", "scripts/web_build.py", "exec", "vitest", "run", "--maxWorkers=2"]
        report = ["--reporter=default", "--reporter=junit", f"--outputFile.junit={out.resolve() / 'junit.xml'}"]
        return [[*web, *report, *(token.removeprefix("web/") for token in ids)]]
    problem = f"kind must be one of {', '.join(KINDS)}"
    raise SelectorError(kind or "(none)", problem)


def _summary(lines: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with Path(path).open("a", encoding="utf-8") as summary:
            summary.write("\n".join(lines) + "\n")


def main(argv: list[str]) -> int:
    """Validate the inputs, then run the plan (or only print it with `--check`); the exit code is the selection's."""
    kind = os.environ.get("FOCUSED_KIND", "")
    selector = os.environ.get("FOCUSED_SELECTOR", "")
    out = Path(os.environ.get("FOCUSED_OUT") or "focused-artifacts")
    try:
        commands = plan(kind, selector, out)
    except SelectorError as error:
        print(f"::error::{error}")
        return 2
    for command in commands:
        print("plan: " + " ".join(command))
    if argv[1:] == ["--check"]:
        return 0
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, HZ_SCREENSHOTS=str((out / "screenshots").resolve()), VERBOSE="1")
    code = 0
    for command in commands:
        # validated argument lists from plan(), never a shell
        try:
            code = subprocess.run(command, cwd=ROOT, env=env, check=False, timeout=TIMEOUT).returncode  # noqa: S603
        except subprocess.TimeoutExpired:
            print(f"::error::timed out after {TIMEOUT} s: {' '.join(command)}")
            code = 124
        if code:
            break
    verdict = "passed" if code == 0 else f"failed (exit {code})"
    _summary([f"### Focused {kind} on {os.environ.get('RUNNER_OS', '?')}: {verdict}", "", f"Selector: `{selector}`"])
    print(f"focused {kind} {selector}: {verdict}")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
