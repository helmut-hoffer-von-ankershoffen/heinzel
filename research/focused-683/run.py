# Pinned checkout tools use argument vectors; stdout records the evidence scope.
# ruff: noqa: INP001, S603, S607, T201
"""Collect read-only, exact-head #683 evidence on a disposable Actions runner."""

import argparse
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import coverage_evidence  # noqa: E402 - only the reviewed helper directory is added under -I

MANIFEST = json.loads((HERE / "manifest.json").read_text())
WEB = ("uv", "run", "--no-sync", "--no-build", "--frozen", "--quiet", "python", "scripts/web_build.py")


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
    test_commit = MANIFEST["coverage_test_commit"]
    repair = MANIFEST["reviewed_repair"]
    _require(_git("show", "-s", "--format=%P", test_commit) == repair, "coverage test commit parent drift")
    _require(
        _git("diff", "--no-renames", "--name-status", repair, test_commit).splitlines()
        == [f"M\t{path}" for path in MANIFEST["coverage_test_paths"]],
        "coverage test commit changed the reviewed repair outside its tests",
    )
    subprocess.run(("git", "merge-base", "--is-ancestor", MANIFEST["current_pr_head"], head), check=True)
    changed = _git("diff", "--no-renames", "--name-status", base, head).splitlines()
    _require(changed == [f"{kind}\t{path}" for path, kind in MANIFEST["changed_paths"].items()], "scope drift")
    for files in coverage_evidence.scope(MANIFEST).values():
        for path in files:
            _git("cat-file", "-e", f"{head}:{path}")
    _require(not _git("status", "--porcelain"), "candidate checkout is dirty")


def _test_python(out: Path) -> None:
    job = MANIFEST["jobs"]["python"]
    files = coverage_evidence.scope(MANIFEST)["python"]
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
    _require(
        bool(actual) and actual == (HERE / "python-collection.txt").read_text().splitlines(),
        "unexpected Python case inventory",
    )
    _require(
        all(case.find(tag) is None for case in cases for tag in ("skipped", "failure", "error")),
        "Python cases contain skips, failures or errors",
    )
    _run(*coverage, "combine", env=env)
    _run(*coverage, "json", "--fail-under=0", "-o", str(report), *files, env=env)
    _run(
        "uv",
        "run",
        "--no-sync",
        "python",
        str(HERE / "coverage_source.py"),
        str(out / "python-opportunities.json"),
        *files,
        env=env,
    )


def _test_platform(out: Path, lane: str) -> None:
    job = MANIFEST["jobs"][lane]
    junit = out / "results.xml"
    _run("uv", "sync", "--python", job["version"], "--frozen", "--no-build", "--no-dev", "--group", "test")
    platform = "darwin" if lane == "macos" else "linux"
    _run(
        "uv",
        "run",
        "--no-sync",
        "python",
        "-c",
        "import sys; assert sys.platform == sys.argv[1] and sys.implementation.name == 'cpython' "
        "and '.'.join(map(str, sys.version_info[:3])) == sys.argv[2] and sys.version_info.releaselevel == 'final'",
        platform,
        job["version"],
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
    _require(actual == sorted(job["selectors"]), f"unexpected {lane} case inventory")
    _require(
        all(case.find(tag) is None for case in cases for tag in ("skipped", "failure", "error")),
        f"{lane} cases contain skips, failures or errors",
    )


def _sonar_refs() -> None:
    base_ref, pr_ref = (MANIFEST[key] for key in ("base_ref", "pr_ref"))
    refs = _git("ls-remote", f"https://github.com/{MANIFEST['repository']}.git", base_ref, pr_ref).splitlines()
    _require(
        set(refs) == {f"{MANIFEST['base']}\t{base_ref}", f"{MANIFEST['candidate']}\t{pr_ref}"},
        "protected base or actual PR ref drifted during Sonar evidence",
    )


def _test_web(out: Path) -> None:
    provider = out.parent / "coverage-provider"
    provider.mkdir()
    for name in ("package.json", "pnpm-lock.yaml"):
        (provider / name).write_bytes((HERE / "coverage-provider" / name).read_bytes())
    _run("uv", "sync", "--frozen", "--no-build", "--only-group", "build")
    _run(*WEB, "prepare")
    _run(*WEB, "pnpm", "--dir", str(provider), "install", "--frozen-lockfile", "--ignore-scripts")
    link = Path("web/node_modules/@vitest/coverage-v8")
    link.parent.mkdir(exist_ok=True)
    link.symlink_to(provider / "node_modules/@vitest/coverage-v8")
    web_root = Path("web").resolve()
    web_paths = [Path(path).relative_to("web") for path in coverage_evidence.scope(MANIFEST)["web"]]
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
        *(f"--coverage.include={path}" for path in web_paths),
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
        bool(cases)
        and sorted(case["fullName"] for case in cases) == (HERE / "web-collection.txt").read_text().splitlines()
        and all(case["status"] == "passed" for case in cases),
        "unexpected web case inventory or status",
    )


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lane", choices=("check", *MANIFEST["jobs"]))
    args = parser.parse_args()
    _source()
    if args.lane == "check":
        print("Pinned source, tree, parents and scope match")
        return
    _require(MANIFEST["activation"] == "ready", "admission held pending exact final-base review")
    review = MANIFEST["source_review"]
    _require(
        review["decision"] == "GO"
        and (review["base"], review["head"], review["tree"])
        == (MANIFEST["base"], MANIFEST["candidate"], MANIFEST["tree"]),
        "admission requires independent review on the exact source pin",
    )
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
    identity = {
        "base": MANIFEST["base"],
        "head": MANIFEST["candidate"],
        "tree": MANIFEST["tree"],
        "helper": helper,
        "lane": args.lane,
        "run": os.environ["GITHUB_RUN_ID"],
        "attempt": "1",
    }
    (out / "identity.json").write_text(json.dumps(identity) + "\n")
    if args.lane == "python":
        _test_python(out)
    elif args.lane in ("python315", "macos"):
        _test_platform(out, args.lane)
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
            str(HERE.parents[1] / "scripts/check_confidentiality.py"),
            "--require",
        )
        env = {key: value for key, value in os.environ.items() if key != "CONFIDENTIAL_TERMS"}
        _run("make", "lint", env=env)
        _run("make", "managed-backend-setup", "typecheck", "quarantine-check", env=env)
    elif args.lane == "security":
        _run("make", "secrets", "audit")
    elif args.lane == "sonar":
        trusted = Path(os.environ["GITHUB_WORKSPACE"]) / "trusted-base"
        base = subprocess.check_output(("git", "-C", str(trusted), "rev-parse", "HEAD"), text=True).strip()
        _require(base == MANIFEST["base"], "Sonar gate is not from the pinned protected base")
        _sonar_refs()
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
        _sonar_refs()
    if args.lane in coverage_evidence.RAW_FILES:
        evidence = coverage_evidence.evidence(MANIFEST, identity, lambda name: (out / name).read_bytes())
        (out / "pr-coverage.json").write_text(json.dumps(evidence, sort_keys=True) + "\n")
    _source()


if __name__ == "__main__":
    _main()
