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
import tempfile
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from coverage import Coverage

from scripts import focused_job
from scripts.focused_plan import LANES, Scope, current_scope, git
from scripts.release_candidate import require, unique_object

COVERAGE_VERSION = "7.17.0"
THRESHOLD = 90
FLOOR = 80
TYPE = "pr-affected-production-coverage/v2"
TEST_TYPE = "focused-tests/v1"
MEASUREMENT = "coverage.json"
REPORT = "evidence.json"
JUNIT = "junit.xml"
MAX_FILE = 2 * 1024 * 1024
ARC_LENGTH = 2
ENV_KEYS = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "UV_PYTHON", "PLAYWRIGHT_BROWSERS_PATH", "SYSTEMROOT")


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
        coverage = Coverage(config_file=str(rc), data_file=None)
        data = coverage.get_data()
        try:
            data.add_arcs({name: [] for name in paths})
            output = root / "opportunities.json"
            coverage.json_report(morfs=list(paths), outfile=str(output))
            reports = parse(output.read_bytes())
        finally:
            data.erase()
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
    """Recompute the typed evidence from exact reviewed source and raw measurements."""
    require(lane in scope.selection and run > 0 and attempt == 1, "unexpected focused lane or run attempt")
    covered = bool(scope.production and lane == "python")
    require(set(files) == ({JUNIT, MEASUREMENT} if covered else {JUNIT}), "wrong focused evidence inventory")
    digest = cases_digest(files[JUNIT])
    require(digest == scope.case_digests[lane], f"{lane} test cases differ from reviewed selection")
    result = {
        "schema": 1,
        "type": TYPE if covered else TEST_TYPE,
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
        coverage = Coverage(config_file=str(rc), data_file=datafile)
        coverage.combine(strict=True)
        data = coverage.get_data()
        try:
            require(data.has_arcs(), "focused run has no branch measurement")
            data.add_arcs({name: [] for name in set(scope.production) - data.measured_files()})
            coverage.json_report(morfs=list(scope.production), outfile=str(out / MEASUREMENT))
        finally:
            data.erase()


def _managed_commands(tests: tuple[str, ...], out: Path) -> list[list[str]]:
    tool = ["uv", "run", "--quiet", "--script", "scripts/web_build.py", "pnpm", "--dir", "../desktop/managed"]
    paths = [f"build/{Path(path).relative_to('desktop/managed').with_suffix('.js')}" for path in tests]
    return [
        ["make", "managed-backend-setup"],
        [*tool, "exec", "tsc", "--project", "."],
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


def run_lane(scope: Scope, lane: str, out: Path, run: int, attempt: int) -> None:
    """Execute the owning focused-job selection and prepare one exact-attempt artifact."""
    require(lane in scope.selection and run > 0 and attempt == 1, "unexpected focused lane or run attempt")
    out.mkdir(parents=True, exist_ok=False)
    kind = LANES[lane][2]
    selected = scope.selection[lane]
    commands = (
        _managed_commands(selected, out) if kind == "managed" else focused_job.plan(kind, " ".join(selected), out)
    )
    env = {key: os.environ[key] for key in ENV_KEYS if key in os.environ}
    env.update(UV_FROZEN="1", HZ_SCREENSHOTS=str(out.parent / "screenshots"))
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


def main() -> None:
    """Run the focused lane of the canonical CI workflow."""
    scope = current_scope()
    require(isinstance(scope, Scope), "full release candidates do not execute focused workloads")
    run_lane(
        scope,
        os.environ["FOCUSED_LANE"],
        Path(os.environ["RUNNER_TEMP"]) / "focused-evidence",
        int(os.environ["GITHUB_RUN_ID"]),
        int(os.environ["GITHUB_RUN_ATTEMPT"]),
    )


if __name__ == "__main__":
    main()
