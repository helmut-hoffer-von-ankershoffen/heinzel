# Pinned checkout tools use argument vectors; stdout records the evidence scope.
# ruff: noqa: INP001, S603, S607, T201
"""Collect read-only, exact-head #683 evidence on a disposable Actions runner."""

import argparse
import json
import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = json.loads((HERE / "manifest.json").read_text())
WEB = ("uv", "run", "--no-build", "--frozen", "--quiet", "--script", "scripts/web_build.py")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def _git(*args: str) -> str:
    return subprocess.check_output(("git", *args), text=True).rstrip("\n")


def _run(*args: str, env: dict[str, str] | None = None) -> None:
    subprocess.run(args, check=True, env=env)


def _source() -> None:
    base, head, tree = (MANIFEST[key] for key in ("base", "candidate", "tree"))
    _require(_git("rev-parse", "HEAD", "HEAD^{tree}").splitlines() == [head, tree], "candidate identity mismatch")
    _require(_git("show", "-s", "--format=%P", head).split() == MANIFEST["candidate_parents"], "parent drift")
    subprocess.run(("git", "merge-base", "--is-ancestor", MANIFEST["current_pr_head"], head), check=True)
    changed = _git("diff", "--no-renames", "--name-status", base, head).splitlines()
    _require(changed == [f"{kind}\t{path}" for path, kind in MANIFEST["changed_paths"].items()], "scope drift")
    _require(not _git("status", "--porcelain"), "candidate checkout is dirty")


def _test_python(out: Path) -> None:
    job = MANIFEST["jobs"]["python"]
    files = MANIFEST["coverage"]["python_files"]
    include = "--include=" + ",".join(files)
    report = out / "python-coverage.json"
    junit = out / "results.xml"
    env = {**os.environ, "COVERAGE_FILE": str(out.parent / "coverage-data")}
    coverage = ("uv", "run", "--no-sync", "coverage")
    _run("uv", "sync", "--python", job["version"], "--frozen", "--no-build", "--no-dev", "--group", "test")
    _run(
        *coverage,
        "run",
        "-m",
        "pytest",
        "-n",
        "2",
        "-q",
        "-p",
        "no:cacheprovider",
        f"--junitxml={junit}",
        *job["selectors"],
        *(f"--deselect={case}" for case in job["deselect"]),
        env=env,
    )
    cases = list(ET.parse(junit).iter("testcase"))  # noqa: S314 - local pytest output from pinned source
    actual = sorted(f"{case.attrib['classname'].replace('.', '/')}.py::{case.attrib['name']}" for case in cases)
    _require(actual == (HERE / "python-collection.txt").read_text().splitlines(), "unexpected Python case inventory")
    _require(
        all(case.find(tag) is None for case in cases for tag in ("skipped", "failure", "error")),
        "Python cases contain skips, failures or errors",
    )
    _run(*coverage, "combine", env=env)
    _run(*coverage, "json", "--fail-under=0", include, "-o", str(report), env=env)
    data = json.loads(report.read_text())
    _require(data["meta"]["branch_coverage"] and set(data["files"]) == set(files), "unexpected Python coverage scope")
    for path, stats in data["files"].items():
        _require(stats["summary"]["num_statements"] > 0, f"empty Python coverage scope: {path}")
    summary = data["totals"]
    covered = summary["covered_lines"] + summary["covered_branches"]
    total = summary["num_statements"] + summary["num_branches"]
    _threshold(covered, total, "Python statements and branches")


def _test_python315(out: Path) -> None:
    job = MANIFEST["jobs"]["python315"]
    junit = out / "results.xml"
    _run("uv", "sync", "--python", job["version"], "--frozen", "--no-build", "--no-dev", "--group", "test")
    _run(
        "uv",
        "run",
        "--no-sync",
        "python",
        "-c",
        "import sys; assert sys.platform == 'linux' and sys.implementation.name == 'cpython' "
        "and tuple(sys.version_info) == (3, 15, 2, 'final', 0)",
    )
    _run(
        "uv",
        "run",
        "--no-sync",
        "pytest",
        "-n",
        "0",
        "-q",
        "-p",
        "no:cacheprovider",
        f"--junitxml={junit}",
        *job["selectors"],
    )
    cases = list(ET.parse(junit).iter("testcase"))  # noqa: S314 - local pytest output from pinned source
    actual = sorted(f"{case.attrib['classname'].replace('.', '/')}.py::{case.attrib['name']}" for case in cases)
    _require(actual == sorted(job["selectors"]), "unexpected Python 3.15 case inventory")
    _require(
        all(case.find(tag) is None for case in cases for tag in ("skipped", "failure", "error")),
        "Python 3.15 cases contain skips, failures or errors",
    )


def _threshold(covered: int, total: int, scope: str) -> None:
    _require(total > 0 and 0 <= covered <= total, f"{scope}: invalid coverage counters")
    print(f"{scope}: {covered}/{total} = {100 * covered / total:.2f}%")
    _require(100 * covered >= MANIFEST["coverage"]["minimum_percent"] * total, f"{scope}: coverage below 80%")


def _test_web(out: Path) -> None:
    provider = out.parent / "coverage-provider"
    provider.mkdir()
    for name in ("package.json", "pnpm-lock.yaml"):
        (provider / name).write_bytes((HERE / "coverage-provider" / name).read_bytes())
    _run(*WEB, "prepare")
    _run(*WEB, "pnpm", "--dir", str(provider), "install", "--frozen-lockfile", "--ignore-scripts")
    link = Path("web/node_modules/@vitest/coverage-v8")
    link.parent.mkdir(exist_ok=True)
    link.symlink_to(provider / "node_modules/@vitest/coverage-v8")
    web_root = Path("web").resolve()
    web_path = Path(MANIFEST["coverage"]["web_file"])
    report = out / "web-results.json"
    coverage = out / "web-coverage"
    selectors = MANIFEST["jobs"]["web"]["selectors"]
    _run(
        *WEB,
        "exec",
        "vitest",
        "run",
        *selectors,
        "--maxWorkers=2",
        "--reporter=json",
        f"--outputFile={report}",
        "--coverage.enabled",
        "--coverage.provider=v8",
        f"--coverage.include={web_path.relative_to('web')}",
        "--coverage.reporter=json-summary",
        "--coverage.reporter=json",
        f"--coverage.reportsDirectory={coverage}",
    )
    data = json.loads(report.read_text())
    suites = data["testResults"]
    _require(
        len(suites) == 1 and Path(suites[0]["name"]) == web_root / selectors[0] and suites[0]["status"] == "passed",
        "unexpected web test file or status",
    )
    cases = suites[0]["assertionResults"]
    _require(
        sorted(case["fullName"] for case in cases) == (HERE / "web-collection.txt").read_text().splitlines()
        and all(case["status"] == "passed" for case in cases),
        "unexpected web case inventory or status",
    )
    counts = json.loads((coverage / "coverage-summary.json").read_text())
    key = str(web_path.resolve())
    _require(set(counts) == {"total", key}, "unexpected web coverage scope")
    raw = json.loads((coverage / "coverage-final.json").read_text())
    _require(set(raw) == {key} and raw[key]["path"] == key, "raw web coverage source mismatch")
    mapped = raw[key]
    statements = mapped["s"]
    branches = [hits for hit_counts in mapped["b"].values() for hits in hit_counts]
    _require(set(statements) == set(mapped["statementMap"]), "missing mapped web statements")
    _require(set(mapped["b"]) == set(mapped["branchMap"]), "missing mapped web branches")
    covered_statements = sum(hits > 0 for hits in statements.values())
    covered_branches = sum(hits > 0 for hits in branches)
    _require(
        counts[key]["statements"]["covered"] == covered_statements
        and counts[key]["statements"]["total"] == len(statements)
        and counts[key]["branches"]["covered"] == covered_branches
        and counts[key]["branches"]["total"] == len(branches),
        "web coverage summary differs from source-mapped counters",
    )
    lines = counts[key]["lines"]
    _threshold(lines["covered"], lines["total"], "Web source lines")
    _threshold(covered_statements + covered_branches, len(statements) + len(branches), "Web statements and branches")


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lane", choices=("check", "python", "python315", "web", "structural", "security", "sonar"))
    args = parser.parse_args()
    _source()
    if args.lane == "check":
        print("Pinned source, tree, parents and scope match")
        return
    _require(MANIFEST["source_review"]["decision"] == "GO", "admission requires new independent source review")
    helper = os.environ.get("REVIEWED_HELPER_SHA")
    _require(
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("GITHUB_EVENT_NAME") == "push"
        and os.environ.get("GITHUB_REF") == "refs/heads/bootstrap/one-shot-683"
        and os.environ.get("GITHUB_REPOSITORY") == MANIFEST["repository"]
        and os.environ.get("GITHUB_RUN_ATTEMPT") == "1"
        and helper == os.environ["GITHUB_SHA"] == os.environ["GITHUB_WORKFLOW_SHA"],
        "expected an independently pinned first-attempt Actions job",
    )
    out = Path(os.environ["RUNNER_TEMP"]) / "focused-683"
    out.mkdir()
    (out / "identity.json").write_text(
        json.dumps(
            {
                "base": MANIFEST["base"],
                "head": MANIFEST["candidate"],
                "tree": MANIFEST["tree"],
                "helper": helper,
                "lane": args.lane,
                "run": os.environ["GITHUB_RUN_ID"],
                "attempt": "1",
            }
        )
        + "\n"
    )
    if args.lane == "python":
        _test_python(out)
    elif args.lane == "python315":
        _test_python315(out)
    elif args.lane == "web":
        _test_web(out)
    elif args.lane == "structural":
        _run(
            "uv",
            "run",
            "--no-project",
            "--frozen",
            "--no-build",
            "--python",
            "3.14",
            "python",
            "scripts/check_confidentiality.py",
            "--require",
            "--text",
            "",
        )
        _run("make", "lint")
        _run("make", "managed-backend-setup", "typecheck", "quarantine-check")
    elif args.lane == "security":
        _run("make", "secrets", "audit")
    elif args.lane == "sonar":
        trusted = Path(os.environ["GITHUB_WORKSPACE"]) / "trusted-base"
        base = subprocess.check_output(("git", "-C", str(trusted), "rev-parse", "HEAD"), text=True).strip()
        _require(base == MANIFEST["base"], "Sonar gate is not from the pinned protected base")
        subprocess.run(
            ("uv", "run", "--no-build", "--frozen", "--script", "scripts/merge_gate.py", "sonar"),
            cwd=trusted,
            check=True,
            env={
                **os.environ,
                "PR_NUMBER": str(MANIFEST["pull_request"]),
                "PR_HEAD_SHA": MANIFEST["candidate"],
            },
        )
    _source()


if __name__ == "__main__":
    _main()
