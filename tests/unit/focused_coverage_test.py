"""Focused coverage preserves independent branch opportunities and both numeric gates."""

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
PRODUCTION = "src/heinzel/new.py"
ALREADY_USED = "src/heinzel/existing.py"
CONFIG = (Path(__file__).resolve().parents[2] / "pyproject.toml").read_bytes()


def _row(lines, missing, branches=(), missing_branches=()):
    return {
        "executed_lines": lines,
        "missing_lines": missing,
        "executed_branches": branches,
        "missing_branches": missing_branches,
    }


@pytest.fixture
def coverage(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    from scripts import focused_coverage

    return focused_coverage


@pytest.fixture
def scope(coverage, monkeypatch):
    from scripts.ci_workload import FocusedPlan
    from scripts.focused_plan import Scope

    source = {
        "a": b"def choose(flag):\n    if flag:\n        return 1\n    return 0\n",
        "b": b"answer = 42\n",
    }
    monkeypatch.setattr(coverage, "git", lambda _command, _kind, blob: source[blob])
    workload = FocusedPlan("pull_request", "refs/pull/1/merge", "a", "b", "c", ("Focused tests (python)",))
    return Scope(workload, "d", "e", {}, {PRODUCTION: "a", ALREADY_USED: "b"}, "c", {}, {"python": ()}, {}, "d")


def test_never_imported_new_source_retains_every_statement_and_branch(coverage, scope):
    measured = {
        PRODUCTION: _row([], [1, 2, 3, 4], missing_branches=[[2, 3], [2, 4]]),
        ALREADY_USED: _row([1], []),
    }
    report = json.dumps(
        {"meta": {"branch_coverage": True, "version": coverage.COVERAGE_VERSION}, "files": measured}
    ).encode()
    counts = coverage.coverage_counts(scope, CONFIG, report, enforce=False)
    assert counts["files"][PRODUCTION] == {"statements": (0, 4), "branches": (0, 2)}
    assert (counts["covered"], counts["total"]) == (1, 7)
    with pytest.raises(ValueError, match="80% floor"):
        coverage.coverage_counts(scope, CONFIG, report)


def test_missing_branch_opportunities_and_missing_files_cannot_pass(coverage, scope):
    incomplete = {
        PRODUCTION: _row([1, 2, 3, 4], [], branches=[[2, 3]]),
        ALREADY_USED: _row([1], []),
    }
    report = {"meta": {"branch_coverage": True, "version": coverage.COVERAGE_VERSION}, "files": incomplete}
    with pytest.raises(ValueError, match="incomplete or overlapping branches"):
        coverage.coverage_counts(scope, CONFIG, coverage.canonical(report))
    del incomplete[PRODUCTION]
    with pytest.raises(ValueError, match="incomplete branch scope"):
        coverage.coverage_counts(scope, CONFIG, coverage.canonical(report))


@pytest.mark.parametrize(("covered", "passes"), [(8, False), (8999, False), (9000, True)])
def test_coverage_uses_unrounded_aggregate_and_retains_90_percent(coverage, scope, monkeypatch, covered, passes):
    all_lines = list(range(1, 10001))
    rows = {PRODUCTION: _row([], all_lines), ALREADY_USED: _row([], [])}
    monkeypatch.setattr(coverage, "source_opportunities", lambda *_args: rows)
    files = {PRODUCTION: _row(all_lines[:covered], all_lines[covered:]), ALREADY_USED: _row([], [])}
    data = coverage.canonical({"meta": {"branch_coverage": True, "version": coverage.COVERAGE_VERSION}, "files": files})
    if passes:
        assert coverage.coverage_counts(scope, CONFIG, data)["covered"] == covered
    else:
        threshold = "80% floor" if covered == 8 else "90% report threshold"
        with pytest.raises(ValueError, match=threshold):
            coverage.coverage_counts(scope, CONFIG, data)


def test_producer_reports_unimported_module_from_real_branch_data(coverage, scope, monkeypatch, tmp_path):
    import subprocess
    import sys

    root = tmp_path.resolve()
    monkeypatch.chdir(root)
    monkeypatch.setenv("PYTHONPATH", str(root / "src"))
    for path, blob in scope.production.items():
        file = root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(coverage.git("cat-file", "blob", blob))
        (file.parent / "__init__.py").touch()
    test = root / "import_test.py"
    test.write_text("def test_import():\n    from heinzel.existing import answer\n    assert answer == 42\n")

    def git(*args):
        assert args == ("cat-file", "blob", scope.config_blob)
        return CONFIG

    def execute(args, env):
        subprocess.run(
            [sys.executable, "-m", *args[3:]],
            env={**env, "PYTHONPATH": str(root / "src"), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
            check=True,
            capture_output=True,
            timeout=15,
        )

    monkeypatch.setattr(coverage, "git", git)
    monkeypatch.setattr(coverage, "_execute", execute)
    coverage._instrument(scope, ["uv", "run", "--no-sync", "pytest", str(test)], root, {})
    rows = json.loads((root / coverage.MEASUREMENT).read_bytes())["files"]
    assert rows[PRODUCTION]["executed_lines"] == []
    assert rows[PRODUCTION]["missing_lines"] == [1, 2, 3, 4]
    assert rows[PRODUCTION]["missing_branches"] == [[2, 3], [2, 4]]
    assert rows[ALREADY_USED]["executed_lines"] == [1]


def test_managed_runner_compiles_but_executes_only_the_selected_test(coverage, tmp_path):
    commands = coverage._managed_commands(("desktop/managed/test/backend.test.ts",), tmp_path)
    assert commands[0] == ["make", "managed-backend-setup"]
    assert commands[1][-4:] == ["exec", "tsc", "--project", "."]
    assert commands[-1][-5:] == [
        "node",
        "--test",
        "--test-reporter=junit",
        f"--test-reporter-destination={tmp_path / coverage.JUNIT}",
        "build/test/backend.test.js",
    ]


def test_python315_bootstraps_pyyaml_before_the_selected_tests(coverage, scope, monkeypatch, tmp_path):
    from dataclasses import replace

    junit = b'<testsuite><testcase classname="test_unit" name="test_case"/></testsuite>'
    scope = replace(
        scope,
        selection={"python315": ("tests/unit/focused_coverage_test.py",)},
        case_digests={"python315": coverage.cases_digest(junit)},
    )
    out = tmp_path / "evidence"
    calls = []

    def execute(args, env):
        calls.append((args, env["UV_PYTHON"]))
        if "pytest" in args:
            (out / coverage.JUNIT).write_bytes(junit)

    monkeypatch.setenv("UV_PYTHON", "3.14")
    monkeypatch.setattr(coverage, "_execute", execute)
    coverage.run_lane(scope, "python315", out, 123, 1)
    sync, bootstrap, tests = [command for command, _python in calls]
    assert sync[:2] == ["uv", "sync"]
    assert "--no-build" in sync
    assert "--no-install-package=pyyaml" in sync
    assert bootstrap == ["bash", "scripts/bootstrap-pyyaml.sh"]
    assert tests[-1] == "tests/unit/focused_coverage_test.py"
    assert {python for _, python in calls} == {"3.15"}
