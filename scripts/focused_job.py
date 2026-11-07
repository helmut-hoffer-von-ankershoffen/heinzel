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
    FOCUSED_REPEAT    1-60: run the selection that many times in this one job (default 1). pytest and e2e ids repeat
                      inside ONE pytest process, the whole selection per round in its own order, so a test that only
                      fails on its second execution in a process shows; vitest files repeat as separate runs.
    FOCUSED_STRESS    true: keep every CPU busy with burner processes while the selection runs (timing flakes)
    FOCUSED_OUT       where the JUnit report, screenshots and tally.json go (default focused-artifacts), uploaded

Prints `FOCUSED TALLY k/n green` (rounds without a failure); stops starting rounds near the job's deadline.

Usage: uv run --no-build --quiet --script scripts/focused_job.py [--check]
    --check   validate the inputs and print the plan, run nothing

This file is also the pytest plugin that does the in-process repeat (`-p focused_job`, `scripts` on PYTHONPATH).
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KINDS = ("e2e", "pytest", "vitest")
LANES = ("cli", "supervised", "web-1", "web-2")
MAX_IDS = 10
MAX_REPEAT = 60
DEADLINE = 1020  # seconds into the run: start no new round after this, so the tally and artifacts still upload
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


def repeat_count(kind: str, value: str) -> int:
    """FOCUSED_REPEAT as a number of rounds.

    Raises:
        SelectorError: not a whole number from 1 to MAX_REPEAT.
    """
    value = (value or "1").strip()
    if not value.isdigit() or not 1 <= int(value) <= MAX_REPEAT:
        problem = f"repeat must be a whole number from 1 to {MAX_REPEAT}"
        raise SelectorError(kind or "(none)", problem)
    return int(value)


def plan(kind: str, selector: str, out: Path, repeat: int = 1) -> list[list[str]]:
    """The commands that run the selection, in order (the last one is the selection itself).

    Raises:
        SelectorError: the inputs are not a selection this workflow runs.
    """
    selector = selector.strip()
    junit = f"--junitxml={out / 'junit.xml'}"
    plugin = ["-p", "focused_job"] if repeat > 1 else []
    if kind in ("e2e", "pytest") and repeat > 1 and ("[" in selector or selector in LANES):
        problem = "repeat takes test ids without a [param] part (a lane or a parametrized case runs once)"
        raise SelectorError(kind, problem)
    if kind == "e2e":
        if selector in LANES:
            return [["make", "test-e2e", f"SUITE={selector}"]]
        ids = _paths(selector, "tests/e2e/", kind)
        pytest = ["uv", "run", "pytest", "-m", "e2e", *SERIAL, *plugin]
        return [["make", "e2e-prepare"], [*pytest, junit, *ids]]
    if kind == "pytest":
        ids = _paths(selector, "tests/", kind)
        sync = ["uv", "sync", "--frozen", "--no-build", "--no-dev", "--group", "test"]
        pytest = ["uv", "run", "--no-sync", "pytest", *SERIAL, *plugin]
        return [sync, [*pytest, junit, *ids]]
    if kind == "vitest":
        ids = _paths(" ".join(f"web/{token.removeprefix('web/')}" for token in selector.split()), "web/", kind)
        web = ["uv", "run", "--quiet", "--script", "scripts/web_build.py", "exec", "vitest", "run", "--maxWorkers=2"]
        report = ["--reporter=default", "--reporter=junit", f"--outputFile.junit={out.resolve() / 'junit.xml'}"]
        return [[*web, *report, *(token.removeprefix("web/") for token in ids)]]
    problem = f"kind must be one of {', '.join(KINDS)}"
    raise SelectorError(kind or "(none)", problem)


# The pytest plugin (`-p focused_job`): every selected test runs FOCUSED_REPEAT times in this one process, round by
# round in collection order, and each round's outcome goes to tally.json.
_ROUNDS: dict[int, bool] = {}
_START = time.monotonic()


def _rounds() -> int:
    return int(os.environ.get("FOCUSED_REPEAT") or "1")


def pytest_generate_tests(metafunc: object) -> None:
    """Run every selected test once per round."""
    if _rounds() > 1:
        metafunc.fixturenames.append("focused_round")  # type: ignore[attr-defined]
        metafunc.parametrize("focused_round", range(_rounds()), ids=lambda r: f"round{r + 1}")  # type: ignore[attr-defined]


def pytest_collection_modifyitems(items: list[object]) -> None:
    """Order by round, keeping collection order within a round: the selection runs whole, N times."""
    items.sort(key=lambda item: getattr(getattr(item, "callspec", None), "params", {}).get("focused_round", 0))


def pytest_runtest_setup(item: object) -> None:
    """Start no new round after the deadline; what ran is tallied."""
    rnd = getattr(getattr(item, "callspec", None), "params", {}).get("focused_round", 0)
    if rnd not in _ROUNDS and time.monotonic() - _START > DEADLINE:
        import pytest  # noqa: PLC0415 - only the plugin side needs pytest

        pytest.exit(f"focused repeat stopped at the deadline after {len(_ROUNDS)} rounds", returncode=1)
    _ROUNDS.setdefault(rnd, True)


def pytest_runtest_logreport(report: object) -> None:
    """A failed setup, call or teardown marks its round red."""
    if getattr(report, "failed", False):
        _ROUNDS[_round_of(report)] = False


def _round_of(report: object) -> int:
    match = re.search(r"[\[-]round(\d+)[\]-]", getattr(report, "nodeid", ""))
    return int(match[1]) - 1 if match else 0


def pytest_sessionfinish(session: object) -> None:
    """Write the tally next to the JUnit report."""
    del session
    out = Path(os.environ.get("FOCUSED_OUT") or "focused-artifacts")
    out.mkdir(parents=True, exist_ok=True)
    green = sum(_ROUNDS.values())
    (out / "tally.json").write_text(json.dumps({"rounds": len(_ROUNDS), "green": green, "asked": _rounds()}))


def _summary(lines: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with Path(path).open("a", encoding="utf-8") as summary:
            summary.write("\n".join(lines) + "\n")


def _run(command: list[str], env: dict[str, str], timeout: float) -> int:
    # validated argument lists from plan(), never a shell
    try:
        return subprocess.run(command, cwd=ROOT, env=env, check=False, timeout=max(timeout, 1)).returncode  # noqa: S603
    except subprocess.TimeoutExpired:
        print(f"::error::timed out: {' '.join(command)}")
        return 124


def _burners(on: bool) -> list[subprocess.Popen[bytes]]:
    """One busy-looping process per CPU, to make timing flakes show."""
    if not on:
        return []
    burn = [sys.executable, "-c", "while True: pass"]
    return [subprocess.Popen(burn) for _ in range(os.cpu_count() or 2)]  # noqa: S603 - fixed argument list


def _tally(out: Path, kind: str, repeat: int, counted: tuple[int, int], code: int) -> tuple[int, int]:
    """(rounds, green): vitest counted here, repeated pytest by the plugin, a single pytest run by its exit code."""
    tally = out / "tally.json"
    if kind == "vitest":
        return counted
    if repeat > 1 and tally.exists():
        written = json.loads(tally.read_text())
        return written["rounds"], written["green"]
    return 1, int(code == 0)


def main(argv: list[str]) -> int:
    """Validate the inputs, then run the plan (or only print it with `--check`); the exit code is the selection's."""
    kind = os.environ.get("FOCUSED_KIND", "")
    selector = os.environ.get("FOCUSED_SELECTOR", "")
    stress = os.environ.get("FOCUSED_STRESS", "false") == "true"
    out = Path(os.environ.get("FOCUSED_OUT") or "focused-artifacts")
    try:
        repeat = repeat_count(kind, os.environ.get("FOCUSED_REPEAT", "1"))
        commands = plan(kind, selector, out, repeat)
    except SelectorError as error:
        print(f"::error::{error}")
        return 2
    for command in commands:
        print("plan: " + " ".join(command))
    print(f"plan: {repeat} round(s){', CPU stress on' if stress else ''}")
    if argv[1:] == ["--check"]:
        return 0
    out.mkdir(parents=True, exist_ok=True)
    (out / "tally.json").unlink(missing_ok=True)
    env = dict(os.environ, HZ_SCREENSHOTS=str((out / "screenshots").resolve()), VERBOSE="1", FOCUSED_REPEAT=str(repeat))
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT / "scripts"), env.get("PYTHONPATH")]))
    start = time.monotonic()
    code = 0
    for command in commands[:-1]:
        if code := _run(command, env, TIMEOUT - (time.monotonic() - start)):
            break
    rounds = green = 0
    burners = _burners(stress) if not code else []
    try:
        # pytest repeats inside its one process (the plugin); vitest files repeat as separate runs in this job
        loops = 0 if code else repeat if kind == "vitest" else 1
        while rounds < loops and time.monotonic() - start < DEADLINE:
            rounds += 1
            result = _run(commands[-1], env, TIMEOUT - (time.monotonic() - start))
            green += result == 0
            if loops == 1:
                code = result
    finally:
        for burner in burners:
            burner.kill()
    rounds, green = _tally(out, kind, repeat, (rounds, green), code)
    (out / "tally.json").write_text(json.dumps({"rounds": rounds, "green": green, "asked": repeat, "stress": stress}))
    code = code or (0 if green == rounds == repeat else 1)
    line = f"FOCUSED TALLY {green}/{rounds} green" + (f" (asked {repeat})" if rounds != repeat else "")
    _summary([f"### Focused {kind} on {os.environ.get('RUNNER_OS', '?')}: {line}", "", f"Selector: `{selector}`"])
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
