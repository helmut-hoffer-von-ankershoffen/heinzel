# /// script
# requires-python = ">=3.12"
# dependencies = ["coverage==7.17.0"]
# ///
"""Produce and independently check typed, branch-aware focused coverage evidence."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING, Any

from scripts import focused_job
from scripts.focused_plan import ADVISORY_LANES, DESKTOP_ALL, LANES, WHOLE_SUITES, Scope, current_scope, git
from scripts.release_candidate import CandidateError, require, unique_object

if TYPE_CHECKING:
    from collections.abc import Callable

COVERAGE_VERSION = "7.17.0"
THRESHOLD = 90
FLOOR = 80
TYPE = "pr-affected-production-coverage/v2"
TEST_TYPE = "focused-tests/v1"
FULL_TYPE = "full-set/v1"
ADVISORY_TYPE = "advisory-tests/v1"  # an advisory lane reports its failures without failing the run
FULL_LANE = "coverage"  # the full set's scoped coverage evidence, from the Coverage job
PLUGINS_URL = "https://github.com/helmut-hoffer-von-ankershoffen/heinzel-plugins.git"
PLUGIN_CONTRACT = "core:plugin-api@1"
CONSUMERS_BUDGET = 20 * 60  # seconds for every plugin together, inside the focused job's 30 minutes
MEASUREMENT = "coverage.json"
REPORT = "evidence.json"
JUNIT = "junit.xml"
MAX_FILE = 2 * 1024 * 1024
ARC_LENGTH = 2
ENV_KEYS = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "UV_PYTHON", "PLAYWRIGHT_BROWSERS_PATH", "SYSTEMROOT")

# Coverage() disables automatic subprocess measurement in its own process.
# Run analysis in a child so an already-measured caller keeps its coverage data.
_COVERAGE_ANALYSIS = r"""
import json
import sys
from coverage import Coverage

request = json.load(sys.stdin)
coverage = Coverage(config_file=request["config"], data_file=request.get("datafile"))
if request["combine"]:
    coverage.combine(strict=True)
data = coverage.get_data()
try:
    if request["combine"] and not data.has_arcs():
        raise ValueError("focused run has no branch measurement")
    data.add_arcs({name: [] for name in set(request["paths"]) - data.measured_files()})
    coverage.json_report(morfs=request["paths"], outfile=request["output"])
finally:
    data.erase()
"""


def _analyse(
    config: Path, paths: list[str], output: Path, *, datafile: str | None = None, combine: bool | None = None
) -> None:
    """Analyse coverage in an isolated interpreter without disturbing a measured caller."""
    request = {
        "config": str(config),
        "paths": paths,
        "output": str(output),
        "datafile": datafile,
        "combine": datafile is not None if combine is None else combine,
    }
    subprocess.run(  # noqa: S603 - fixed interpreter and analysis code; data is JSON input
        [sys.executable, "-I", "-c", _COVERAGE_ANALYSIS],
        input=canonical(request),
        check=True,
        timeout=120,
    )


def canonical(value: object) -> bytes:
    """Serialize one typed evidence value for transport and comparison."""
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def parse(data: bytes) -> Any:  # noqa: ANN401 - the JSON input is validated at the evidence boundary
    """Load bounded JSON with no duplicate members."""
    require(0 < len(data) <= MAX_FILE, "missing or oversized focused JSON")
    return json.loads(data, object_pairs_hook=unique_object)


def cases_digest(data: bytes) -> str:
    """Require a nonempty exact case inventory with no failed or skipped cases."""
    require(0 < len(data) <= MAX_FILE, "missing or oversized focused test report")
    require(b"<!DOCTYPE" not in data.upper() and b"<!ENTITY" not in data.upper(), "unexpected XML declaration")
    root = ET.fromstring(data)  # noqa: S314 - bounded report with no DTD or entity declaration
    require(root.tag in {"testsuite", "testsuites"}, "invalid test report")
    cases = list(root.iter("testcase"))
    names = [(case.get("classname", ""), case.get("name", "")) for case in cases]
    require(
        cases
        and len(set(names)) == len(cases)
        and all(name and group for group, name in names)
        and not any(child.tag in {"failure", "error", "skipped"} for case in cases for child in case),
        "focused selection skipped, failed, duplicated, or collected no tests",
    )
    return hashlib.sha256(canonical(sorted(names))).hexdigest()


def _owner_of(kind: str, test: str) -> tuple[str, str]:
    """The JUnit attribute and value that name a selected test file's cases, per runner."""
    path = test.split("::", 1)[0]
    if kind in {"pytest", "e2e"}:
        return "classname", path.removesuffix(".py").replace("/", ".")
    if kind == "vitest":
        return "classname", path.removeprefix("web/")
    return "file", path  # node --test names the absolute file


def require_cases_cover(kind: str, tests: tuple[str, ...], data: bytes) -> None:
    """Every selected test file must have run at least one case: a selected file that collects none is refused.

    Raises:
        CandidateError: A selected file has no case in the report.
    """
    if any(test in WHOLE_SUITES.values() for test in tests):
        return  # a whole suite names no file
    root = ET.fromstring(data)  # noqa: S314 - bounded report with no DTD or entity declaration, checked by the caller
    cases = list(root.iter("testcase"))
    missing = []
    for test in tests:
        attribute, owner = _owner_of(kind, test)
        if not any(
            (value := case.get(attribute, "")) == owner
            or value.startswith(owner + ".")
            or (attribute == "file" and value.endswith("/" + owner))
            for case in cases
        ):
            missing.append(test)
    require(not missing, "selected tests ran no case: " + ", ".join(missing))


def advisory_cases(data: bytes) -> tuple[str, list[str]]:
    """The case inventory of an advisory lane and the names of its failed cases, which it reports, not enforces."""
    require(0 < len(data) <= MAX_FILE, "missing or oversized advisory test report")
    require(b"<!DOCTYPE" not in data.upper() and b"<!ENTITY" not in data.upper(), "unexpected XML declaration")
    root = ET.fromstring(data)  # noqa: S314 - bounded report with no DTD or entity declaration
    cases = list(root.iter("testcase"))
    names = [(case.get("classname", ""), case.get("name", "")) for case in cases]
    require(cases and len(set(names)) == len(cases), "advisory lane collected no or duplicate cases")
    failed = sorted(case.get("name", "") for case in cases if any(child.tag in {"failure", "error"} for child in case))
    return hashlib.sha256(canonical(sorted(names))).hexdigest(), failed


def config_policy(content: bytes) -> dict[str, Any]:
    """Read the existing coverage config and reject loss of branch or subprocess measurement."""
    config = tomllib.loads(content.decode())["tool"]["coverage"]
    run, report = config["run"], config["report"]
    require(
        run.get("branch") is True
        and run.get("parallel") is True
        and run.get("relative_files") is True
        and "subprocess" in run.get("patch", [])
        and run.get("source") == ["heinzel"]
        and report.get("fail_under") == THRESHOLD,
        "coverage configuration does not preserve the reviewed project policy",
    )
    return config


def source_opportunities(scope: Scope, config: bytes) -> dict[str, Any]:
    """Independently derive complete executable opportunities from exact Git blobs.

    The scratch checkout contains source text only. Coverage's static parser reads
    it without importing the candidate, including files absent from the test data.
    """
    config_policy(config)
    with tempfile.TemporaryDirectory(prefix="focused-source-") as scratch:
        root = Path(scratch)
        rc = root / "pyproject.toml"
        rc.write_bytes(config)
        paths = {}
        for path, blob in scope.production.items():
            dest = root / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(git("cat-file", "blob", blob))
            paths[str(dest.resolve())] = path
        output = root / "opportunities.json"
        _analyse(rc, list(paths), output)
        reports = parse(output.read_bytes())
        require(reports["meta"]["branch_coverage"] is True, "source analysis lost branch opportunities")
        inventory = {}
        for name, row in reports["files"].items():
            path = paths[str(Path(name).resolve())]
            require(not row["executed_lines"] and not row["executed_branches"], "source analysis ran candidate code")
            inventory[path] = row
        require(set(inventory) == set(scope.production), "source analysis omitted an affected production file")
        return inventory


def _locations(values: list[Any], *, branches: bool) -> set[int | tuple[int, ...]]:
    if branches:
        require(
            all(
                isinstance(arc, list)
                and len(arc) == ARC_LENGTH
                and all(type(line) is int and line != 0 for line in arc)
                for arc in values
            ),
            "invalid branch arc",
        )
        locations: set[int | tuple[int, ...]] = {tuple(arc) for arc in values}
    else:
        require(all(type(line) is int and line > 0 for line in values), "invalid executable line")
        locations = set(values)
    require(len(locations) == len(values), "duplicate coverage opportunity")
    return locations


def _counts(row: dict[str, Any], expected: dict[str, Any], kind: str) -> tuple[int, int]:
    branches = kind == "branches"
    hit = _locations(row[f"executed_{kind}"], branches=branches)
    miss = _locations(row[f"missing_{kind}"], branches=branches)
    complete = _locations(expected[f"missing_{kind}"], branches=branches)
    require(not hit & miss and hit | miss == complete, f"incomplete or overlapping {kind} coverage")
    return len(hit), len(complete)


def coverage_counts(
    scope: Scope,
    config: bytes,
    data: bytes,
    *,
    enforce: bool = True,
) -> dict[str, Any]:
    """Verify all file/branch denominators, then apply exact unrounded aggregate gates."""
    report = parse(data)
    files = report["files"]
    require(
        report["meta"]["branch_coverage"] is True
        and report["meta"]["version"] == COVERAGE_VERSION
        and set(files) == set(scope.production),
        "incomplete branch scope or unexpected coverage version",
    )
    expected = source_opportunities(scope, config)
    counts = {
        path: {
            "statements": _counts(row, expected[path], "lines"),
            "branches": _counts(row, expected[path], "branches"),
        }
        for path, row in files.items()
    }
    covered = sum(count for row in counts.values() for count, _ in row.values())
    total = sum(count for row in counts.values() for _, count in row.values())
    require(total > 0, "scoped production inventory has no executable opportunities")
    if enforce:
        require(100 * covered >= FLOOR * total, f"PR scoped coverage below the {FLOOR}% floor")
        require(100 * covered >= THRESHOLD * total, f"PR scoped coverage below the {THRESHOLD}% report threshold")
    return {"files": counts, "covered": covered, "total": total, "threshold": THRESHOLD, "floor": FLOOR}


def evidence(
    scope: Scope,
    lane: str,
    run: int,
    attempt: int,
    files: dict[str, bytes],
) -> dict[str, Any]:
    """Recompute the typed evidence from the exact selected source and raw measurements.

    Merge tier: any attempt of the run may produce it; the attempt is part of the evidence. In full mode the one
    lane is FULL_LANE, the scoped coverage of the touched production files measured by the whole-suite Coverage job.
    """
    full = scope.full and lane == FULL_LANE
    require((full or lane in scope.selection) and run > 0 and attempt >= 1, "unexpected focused lane or run attempt")
    covered = bool(scope.production and lane in {"python", FULL_LANE})
    failed: list[str] = []
    if full:
        require(set(files) == ({MEASUREMENT} if covered else set()), "wrong full-set coverage evidence inventory")
        digest = hashlib.sha256(files.get(MEASUREMENT, b"")).hexdigest()
    elif lane in ADVISORY_LANES:
        require(set(files) == {JUNIT}, "wrong advisory evidence inventory")
        digest, failed = advisory_cases(files[JUNIT])
    else:
        require(set(files) == ({JUNIT, MEASUREMENT} if covered else {JUNIT}), "wrong focused evidence inventory")
        digest = cases_digest(files[JUNIT])
        require_cases_cover(LANES[lane][2], scope.selection[lane], files[JUNIT])
    require(lane not in scope.case_digests or digest == scope.case_digests[lane], f"{lane} test cases differ")
    result = {
        "schema": 1,
        "type": TYPE if covered else FULL_TYPE if full else ADVISORY_TYPE if lane in ADVISORY_LANES else TEST_TYPE,
        "mode": scope.mode,
        "event": scope.workload.event,
        "base": scope.workload.base,
        "head": scope.workload.head,
        "commit": scope.commit,
        "tree": scope.tree,
        "manifest_sha256": scope.manifest_digest,
        "config_blob": scope.config_blob,
        "lane": lane,
        "run": run,
        "attempt": attempt,
        "cases_sha256": digest,
        "raw_sha256": {name: hashlib.sha256(value).hexdigest() for name, value in files.items()},
    }
    if lane in ADVISORY_LANES:
        result["failed"] = failed
    if covered:
        config = git("cat-file", "blob", scope.config_blob)
        result["coverage"] = coverage_counts(scope, config, files[MEASUREMENT])
    return result


def _execute(args: list[str], env: dict[str, str]) -> None:
    subprocess.run(args, env=env, check=True, timeout=focused_job.TIMEOUT)  # noqa: S603 - fixed runner commands


def _instrument(
    scope: Scope,
    command: list[str],
    out: Path,
    env: dict[str, str],
) -> None:
    """Measure selected tests with fresh data and the exact branch-aware configuration."""
    with tempfile.TemporaryDirectory(prefix="focused-measurement-") as scratch:
        work = Path(scratch)
        rc = work / "pyproject.toml"
        config = git("cat-file", "blob", scope.config_blob)
        config_policy(config)
        rc.write_bytes(config)
        datafile = str(work / ".coverage")
        env = {**env, "COVERAGE_FILE": datafile}
        _execute([*command[:3], "coverage", "run", f"--rcfile={rc}", "-m", *command[3:]], env)
        _analyse(rc, list(scope.production), out / MEASUREMENT, datafile=datafile)


def _desktop_commands(tests: tuple[str, ...], out: Path, root: Path = Path()) -> list[list[str]]:
    """Install the desktop app's locked dependencies without its Electron download, then run its Node unit tests."""
    tool = ["uv", "run", "--quiet", "--script", "scripts/web_build.py", "pnpm", "--dir", "../desktop"]
    if tests == (DESKTOP_ALL,):
        tests = tuple(sorted(path.relative_to(root).as_posix() for path in root.glob("desktop/test/*.test.js")))
    require(bool(tests), "the desktop unit suite has no test files")
    paths = [test.removeprefix("desktop/") for test in tests]
    return [
        ["make", "desktop-unit-setup"],
        [
            *tool,
            "exec",
            "node",
            "--test",
            "--test-reporter=junit",
            f"--test-reporter-destination={out / JUNIT}",
            *paths,
        ],
    ]


def _consumer_names(env: dict[str, str], work: Path) -> tuple[str, list[str], list[str]]:
    """Build this tree's wheel and clone plugins main; return the wheel, the make prefix and the planned plugins."""
    _execute(["make", "build"], env)
    wheels = sorted(Path("dist").glob("heinzel-*.whl"))
    require(len(wheels) == 1, "expected exactly one Core wheel in dist/")
    wheel = str(wheels[0].resolve())
    plugins = work / "heinzel-plugins"
    _execute(["git", "clone", "--depth=1", PLUGINS_URL, str(plugins)], env)
    make = ["make", "--no-print-directory", "-C", str(plugins)]
    plan = subprocess.run(  # noqa: S603 - fixed make target in the plugins checkout
        [*make, "plan", f"CONTRACT={PLUGIN_CONTRACT}", f"CORE={wheel}"],
        env=env,
        check=True,
        capture_output=True,
        timeout=focused_job.TIMEOUT,
    )
    names = [row["name"] for row in json.loads(plan.stdout)["include"]]
    require(bool(names), "the plugins plan selects no consumer")
    return wheel, make, names


def _consumers(out: Path, env: dict[str, str], work: Path, clock: Callable[[], float] = time.monotonic) -> None:
    """Run every public plugin's checks at plugins main against this tree's wheel, one at a time, within a budget.

    Each plugin is one JUnit case; a failing plugin is a failed case, so the lane's evidence names it. The lane is
    advisory: when it cannot even start (build, clone, plan) or runs out of its time budget, that is one more failed
    case, never a failed job.
    """
    suite = ET.Element("testsuite", name="consumers")
    deadline = clock() + CONSUMERS_BUDGET
    try:
        wheel, make, names = _consumer_names(env, work)
    except (CandidateError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        case = ET.SubElement(suite, "testcase", classname="consumers", name="lane did not run")
        ET.SubElement(case, "failure", message=f"{type(error).__name__}: {error}"[:500])
        names = []
    for name in names:
        case = ET.SubElement(suite, "testcase", classname="consumers", name=name)
        left = deadline - clock()
        if left <= 0:
            ET.SubElement(case, "failure", message="not run: the lane's time budget is spent")
            continue
        try:
            done = subprocess.run(  # noqa: S603 - fixed make target, one plugin at a time
                [*make, "check", f"NAME={name}", f"CORE={wheel}"],
                env=env,
                check=False,
                timeout=min(focused_job.TIMEOUT, left),
            )
            code = done.returncode
        except subprocess.TimeoutExpired:
            code = "timeout"
        if code:
            ET.SubElement(case, "failure", message=f"make check NAME={name} exited {code}")
    suite.set("tests", str(len(suite)))
    (out / JUNIT).write_bytes(ET.tostring(suite, encoding="utf-8", xml_declaration=False))


def _e2e_suite_commands(suite: str, out: Path, root: Path = Path()) -> list[list[str]]:
    """One whole browser e2e suite (web-1 or web-2) on three workers, as the candidate's e2e job runs it."""
    sys.path.insert(0, str(root.resolve() / "scripts"))
    import affected  # noqa: PLC0415 - this checkout's selector names the suite's files from its Makefile

    files = affected.Repo(root.resolve()).lanes[suite]
    require(bool(files), f"the {suite} browser suite has no test files")
    pytest = ["uv", "run", "pytest", "-m", "e2e", "-n", "3", "-q", "-p", "no:cacheprovider", "--session-timeout=900"]
    return [["make", "e2e-prepare"], [*pytest, f"--junitxml={out / JUNIT}", *files]]


def _lane_commands(kind: str, lane: str, selected: tuple[str, ...], out: Path) -> list[list[str]]:
    if kind == "e2e" and lane in WHOLE_SUITES and selected == (WHOLE_SUITES[lane],):
        return _e2e_suite_commands(lane.removeprefix("e2e-"), out)
    if kind == "node":
        return _desktop_commands(selected, out)
    return focused_job.plan(
        kind,
        " ".join(selected),
        out,
        max_ids=focused_job.PYTHON_CI_MAX_IDS if lane == "python" else focused_job.MAX_IDS,
    )


def run_lane(scope: Scope, lane: str, out: Path, run: int, attempt: int) -> None:
    """Execute the owning focused-job selection and prepare one artifact for this run attempt."""
    require(lane in scope.selection and run > 0 and attempt >= 1, "unexpected focused lane or run attempt")
    out.mkdir(parents=True, exist_ok=False)
    kind = LANES[lane][2]
    selected = scope.selection[lane]
    env = {key: os.environ[key] for key in ENV_KEYS if key in os.environ}
    env.update(UV_FROZEN="1", HZ_SCREENSHOTS=str(out.parent / "screenshots"))
    if kind == "consumer":
        with tempfile.TemporaryDirectory(prefix="focused-consumers-") as work:
            _consumers(out, env, Path(work))
        files = {path.name: path.read_bytes() for path in out.iterdir()}
        report = evidence(scope, lane, run, attempt, files)
        (out / REPORT).write_bytes(canonical(report) + b"\n")
        if report["failed"] and (summary := os.environ.get("GITHUB_STEP_SUMMARY")):
            with Path(summary).open("a", encoding="utf-8") as out_summary:
                names = ", ".join(report["failed"])
                out_summary.write(f"### Advisory: plugins failing against this Core at plugins main\n\n{names}\n")
        return
    commands = _lane_commands(kind, lane, selected, out)
    if lane == "python315":
        env["UV_PYTHON"] = LANES[lane][1]
        commands[0].append("--no-install-package=pyyaml")
        commands.insert(1, ["bash", "scripts/bootstrap-pyyaml.sh"])
    if lane == "macos-notifier":
        _execute(["make", "notifier"], env)
    for command in commands[:-1]:
        _execute(command, env)
    if scope.production and lane == "python":
        _instrument(scope, commands[-1], out, env)
    else:
        _execute(commands[-1], env)
    files = {path.name: path.read_bytes() for path in out.iterdir()}
    (out / REPORT).write_bytes(canonical(evidence(scope, lane, run, attempt, files)) + b"\n")


def measure_full(scope: Scope, out: Path, run: int, attempt: int, datafile: Path) -> bool:
    """Apply the scoped coverage gate to the touched production files, from the whole suite's measurement.

    The full set runs no Python lane, so the Coverage job's data stands in for it: the same files, opportunities and
    threshold as a focused PR, with every test as the numerator. With no production file it records only the tested
    source. Returns whether coverage was measured.
    """
    require(scope.full and run > 0 and attempt >= 1, "scoped full-set coverage applies only to a full merge-tier run")
    out.mkdir(parents=True, exist_ok=False)
    if not scope.production:
        (out / REPORT).write_bytes(canonical(evidence(scope, FULL_LANE, run, attempt, {})) + b"\n")
        return False
    with tempfile.TemporaryDirectory(prefix="focused-full-") as scratch:
        rc = Path(scratch) / "pyproject.toml"
        config = git("cat-file", "blob", scope.config_blob)
        config_policy(config)
        rc.write_bytes(config)
        _analyse(rc, list(scope.production), out / MEASUREMENT, datafile=str(datafile), combine=False)
    files = {MEASUREMENT: (out / MEASUREMENT).read_bytes()}
    (out / REPORT).write_bytes(canonical(evidence(scope, FULL_LANE, run, attempt, files)) + b"\n")
    return True


def main() -> None:
    """Run the focused lane of the canonical CI workflow, or (`full DATAFILE`) the full set's scoped coverage."""
    scope = current_scope()
    require(isinstance(scope, Scope), "full release candidates do not execute focused workloads")
    if sys.argv[1:2] == ["full"] and len(sys.argv) == 3:  # noqa: PLR2004 - the subcommand and its data file
        measure_full(
            scope,
            Path(os.environ["RUNNER_TEMP"]) / "focused-evidence",
            int(os.environ["GITHUB_RUN_ID"]),
            int(os.environ["GITHUB_RUN_ATTEMPT"]),
            Path(sys.argv[2]),
        )
        return
    run_lane(
        scope,
        os.environ["FOCUSED_LANE"],
        Path(os.environ["RUNNER_TEMP"]) / "focused-evidence",
        int(os.environ["GITHUB_RUN_ID"]),
        int(os.environ["GITHUB_RUN_ATTEMPT"]),
    )


if __name__ == "__main__":
    main()
