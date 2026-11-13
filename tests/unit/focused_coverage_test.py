"""Focused coverage preserves independent branch opportunities and both numeric gates."""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
PRODUCTION = "src/heinzel/new.py"
ALREADY_USED = "src/heinzel/existing.py"
CONFIG = (Path(__file__).resolve().parents[2] / "pyproject.toml").read_bytes()
REPO = Path(__file__).resolve().parents[2]


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


def test_analysis_preserves_coverage_of_instrumented_xdist_workers(tmp_path):
    """Both real analysis paths must leave an outer measured worker's data intact."""
    marker = tmp_path / "worker_marker.py"
    marker.write_text(
        "def before_opportunities():\n    return 11\n\n"
        "def after_opportunities():\n    return 12\n\n"
        "def before_instrument():\n    return 13\n\n"
        "def after_instrument():\n    return 14\n"
    )
    config = tmp_path / "pyproject.toml"
    config.write_text(
        f"[tool.coverage.run]\nsource = [{json.dumps(str(tmp_path))}]\n"
        'branch = true\nparallel = true\npatch = ["subprocess"]\n'
    )
    probe = tmp_path / "test_outer_worker_probe.py"
    probe.write_text(
        textwrap.dedent(f"""
        import os
        import subprocess
        import sys
        from pathlib import Path

        import worker_marker
        from scripts import focused_coverage as focused
        from scripts.ci_workload import FocusedPlan
        from scripts.focused_plan import Scope

        CONFIG = Path({str(REPO / "pyproject.toml")!r}).read_bytes()
        PATH = "src/heinzel/example.py"
        SOURCE = b"def value():\\n    return 42\\n"
        WORKLOAD = FocusedPlan("pull_request", "refs/pull/1/merge", "a", "b", "c", ("Focused tests (python)",))
        SCOPE = Scope(WORKLOAD, "d", "e", {{}}, {{PATH: "source"}}, "config", {{}}, {{"python": ()}}, {{}}, "d")

        def test_opportunities(monkeypatch):
            assert worker_marker.before_opportunities() == 11
            monkeypatch.setattr(focused, "git", lambda *_args: SOURCE)
            rows = focused.source_opportunities(SCOPE, CONFIG)
            assert rows[PATH]["missing_lines"] == [1, 2]
            assert worker_marker.after_opportunities() == 12

        def test_instrument(monkeypatch, tmp_path):
            assert worker_marker.before_instrument() == 13
            monkeypatch.chdir(tmp_path)
            module = tmp_path / PATH
            module.parent.mkdir(parents=True)
            module.write_bytes(SOURCE)
            (module.parent / "__init__.py").touch()
            sample = tmp_path / "sample_test.py"
            sample.write_text("def test_sample():\\n    from heinzel.example import value\\n"
                              "    assert value() == 42\\n")
            monkeypatch.setattr(focused, "git", lambda *_args: CONFIG)
            def execute(args, env):
                subprocess.run([sys.executable, "-m", *args[3:]], check=True, timeout=30,
                               env={{**os.environ, **env, "PYTHONPATH": str(tmp_path / "src"),
                                     "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}}, capture_output=True)
            monkeypatch.setattr(focused, "_execute", execute)
            focused._instrument(SCOPE, ["uv", "run", "--no-sync", "pytest", str(sample)], tmp_path, {{}})
            assert (tmp_path / focused.MEASUREMENT).is_file()
            assert worker_marker.after_instrument() == 14
    """)
    )
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join((str(REPO), str(tmp_path))),
        "COVERAGE_FILE": str(tmp_path / ".coverage"),
        "PYTEST_ADDOPTS": "",
        # The disposable probe needs real xdist, not unrelated installed pytest plugins.
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    run = [sys.executable, "-m", "coverage"]
    subprocess.run(
        [*run, "run", f"--rcfile={config}", "-m", "pytest", "-p", "xdist.plugin", "-n", "2", "-q", str(probe)],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        timeout=90,
    )
    subprocess.run(
        [*run, "combine", "--rcfile", str(config)], cwd=tmp_path, env=env, check=True, capture_output=True, timeout=30
    )
    report = tmp_path / "outer-coverage.json"
    subprocess.run(
        [*run, "json", "--rcfile", str(config), "-o", str(report)],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        timeout=30,
    )
    files = json.loads(report.read_text())["files"]
    measured = next(row for name, row in files.items() if Path(name).name == marker.name)
    assert {2, 5, 8, 11} <= set(measured["executed_lines"])


def test_desktop_runner_installs_the_locked_test_dependencies_and_runs_only_the_selection(coverage, tmp_path):
    commands = coverage._desktop_commands(("desktop/test/shutdown.test.js",), tmp_path)
    assert commands[0] == ["make", "desktop-unit-setup"]
    assert commands[-1][-6:] == [
        "exec",
        "node",
        "--test",
        "--test-reporter=junit",
        f"--test-reporter-destination={tmp_path / coverage.JUNIT}",
        "test/shutdown.test.js",
    ]
    assert commands[-1][:8] == [
        "uv",
        "run",
        "--quiet",
        "--script",
        "scripts/web_build.py",
        "pnpm",
        "--dir",
        "../desktop",
    ]


def test_desktop_runner_expands_the_whole_suite_from_the_checkout(coverage, tmp_path):
    (tmp_path / "desktop/test").mkdir(parents=True)
    for name in ("b.test.js", "a.test.js", "helper.js"):
        (tmp_path / "desktop/test" / name).write_text("")
    commands = coverage._desktop_commands(("desktop/ALL",), tmp_path / "out", tmp_path)
    assert commands[-1][-2:] == ["test/a.test.js", "test/b.test.js"]
    from scripts.release_candidate import CandidateError

    with pytest.raises(CandidateError, match="no test files"):
        coverage._desktop_commands(("desktop/ALL",), tmp_path, tmp_path / "empty")


def test_the_desktop_lane_runs_the_desktop_runner(coverage, scope, monkeypatch, tmp_path):
    from dataclasses import replace

    scope = replace(scope, selection={"desktop": ("desktop/test/window.test.js",)})
    calls = []
    monkeypatch.setattr(coverage, "_execute", lambda command, _env: calls.append(command))
    monkeypatch.setattr(coverage, "evidence", lambda *_args: {})
    coverage.run_lane(scope, "desktop", tmp_path / "out", 1, 1)
    assert calls[0] == ["make", "desktop-unit-setup"]
    assert calls[-1][-1] == "test/window.test.js"


@pytest.mark.parametrize("case", [("python", 32), ("python312", 10), ("macos", 10)])
def test_only_python_lane_receives_the_expanded_execution_limit(coverage, scope, monkeypatch, tmp_path, case):
    from dataclasses import replace

    lane, expected = case
    scope = replace(scope, selection={lane: ("tests/unit/ci_test.py",)})

    class ObservedError(Exception):
        pass

    def plan(kind, selector, out, *, max_ids):
        assert kind == "pytest"
        assert selector == "tests/unit/ci_test.py"
        assert max_ids == expected
        raise ObservedError

    monkeypatch.setattr(coverage.focused_job, "plan", plan)
    with pytest.raises(ObservedError):
        coverage.run_lane(scope, lane, tmp_path / "evidence", 123, 1)


def test_python315_bootstraps_pyyaml_before_the_selected_tests(coverage, scope, monkeypatch, tmp_path):
    from dataclasses import replace

    junit = b'<testsuite><testcase classname="tests.unit.focused_coverage_test" name="test_case"/></testsuite>'
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


def test_web_lane_runs_the_whole_vitest_suite_for_the_sentinel(coverage, scope, monkeypatch, tmp_path):
    from dataclasses import replace

    junit = (
        b'<testsuites><testsuite><testcase classname="src/a.test.ts" name="a"/>'
        b'<testcase classname="e2e/b.test.mjs" name="b"/></testsuite></testsuites>'
    )
    sentinel = coverage.focused_job.VITEST_ALL
    scope = replace(scope, selection={"web": (sentinel,)}, case_digests={"web": coverage.cases_digest(junit)})
    out = tmp_path / "evidence"
    calls = []

    def execute(args, _env):
        calls.append(args)
        (out / coverage.JUNIT).write_bytes(junit)

    monkeypatch.setattr(coverage, "_execute", execute)
    coverage.run_lane(scope, "web", out, 123, 1)
    (command,) = calls
    assert command[-4:] == [
        "--maxWorkers=2",
        "--reporter=default",
        "--reporter=junit",
        f"--outputFile.junit={out.resolve() / coverage.JUNIT}",
    ]
    assert sentinel not in command
    assert json.loads((out / coverage.REPORT).read_bytes())["cases_sha256"] == coverage.cases_digest(junit)


JUNIT_OK = b'<testsuite><testcase classname="c" name="a"/><testcase classname="c" name="b"/></testsuite>'
JUNIT_RED = (
    b'<testsuite><testcase classname="c" name="a"/><testcase classname="c" name="b"><failure/></testcase></testsuite>'
)


def test_merge_tier_evidence_records_a_later_attempt(coverage, scope):
    from dataclasses import replace

    scope = replace(scope, production={}, selection={"web": ("web/src/a.test.ts",)})
    ok = b'<testsuite><testcase classname="src/a.test.ts" name="a"/></testsuite>'
    from scripts.release_candidate import CandidateError

    with pytest.raises(CandidateError, match="run attempt"):
        coverage.evidence(scope, "web", 1, 0, {coverage.JUNIT: ok})
    assert coverage.evidence(scope, "web", 1, 3, {coverage.JUNIT: ok})["attempt"] == 3


def test_an_advisory_lane_reports_failures_without_failing(coverage, scope):
    from dataclasses import replace

    from scripts.release_candidate import CandidateError

    scope = replace(scope, production={}, selection={"consumers": ("plugins/main",)})
    report = coverage.evidence(scope, "consumers", 1, 1, {coverage.JUNIT: JUNIT_RED})
    assert (report["type"], report["failed"]) == (coverage.ADVISORY_TYPE, ["b"])
    assert coverage.evidence(scope, "consumers", 1, 1, {coverage.JUNIT: JUNIT_OK})["failed"] == []
    with pytest.raises(CandidateError, match="no or duplicate cases"):
        coverage.evidence(scope, "consumers", 1, 1, {coverage.JUNIT: b"<testsuite/>"})
    gated = replace(scope, selection={"web": ("web/src/a.test.ts",)})
    with pytest.raises(CandidateError, match="failed"):
        coverage.evidence(gated, "web", 1, 1, {coverage.JUNIT: JUNIT_RED})


def test_the_full_set_records_its_source_and_the_scoped_coverage(coverage, scope, tmp_path, monkeypatch):
    from dataclasses import replace

    from scripts.release_candidate import CandidateError

    full = replace(scope, production={}, selection={}, full=True)
    assert coverage.measure_full(full, tmp_path / "a", 5, 2, tmp_path / ".coverage") is False
    report = json.loads((tmp_path / "a" / coverage.REPORT).read_bytes())
    assert (report["type"], report["lane"], report["attempt"]) == (coverage.FULL_TYPE, coverage.FULL_LANE, 2)
    with pytest.raises(CandidateError, match="full merge-tier run"):
        coverage.measure_full(scope, tmp_path / "b", 5, 1, tmp_path / ".coverage")
    calls = []

    def analyse(_rc, paths, output, **kwargs):
        calls.append((paths, kwargs))
        message = "measured"
        raise RuntimeError(message)

    monkeypatch.setattr(coverage, "_analyse", analyse)
    monkeypatch.setattr(coverage, "git", lambda *_args: CONFIG)
    with pytest.raises(RuntimeError, match="measured"):
        coverage.measure_full(replace(scope, full=True), tmp_path / "c", 5, 1, tmp_path / ".coverage")
    assert calls == [([PRODUCTION, ALREADY_USED], {"datafile": str(tmp_path / ".coverage"), "combine": False})]


def test_a_whole_browser_suite_runs_its_makefile_files_with_a_report(coverage, tmp_path):
    root = Path(__file__).resolve().parents[2]
    commands = coverage._e2e_suite_commands("web-2", tmp_path, root)
    assert commands[0] == ["make", "e2e-prepare"]
    assert f"--junitxml={tmp_path / coverage.JUNIT}" in commands[1]
    files = [arg for arg in commands[1] if arg.startswith("tests/e2e/")]
    assert len(files) > 10
    assert "tests/e2e/web_ui_test.py" not in files
    assert (
        coverage._lane_commands("e2e", "e2e-web-1", ("tests/e2e/web-1",), tmp_path)[1][-1] == "tests/e2e/web_ui_test.py"
    )


def test_the_consumers_lane_checks_every_plugin_serially(coverage, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "heinzel-1.0-py3-none-any.whl").write_bytes(b"")
    executed, runs = [], []
    monkeypatch.setattr(coverage, "_execute", lambda args, _env: executed.append(args))

    def run(args, **_kwargs):
        runs.append(args)
        if "plan" in args:
            return subprocess.CompletedProcess(
                args, 0, json.dumps({"include": [{"name": "x"}, {"name": "y"}]}).encode()
            )
        return subprocess.CompletedProcess(args, 1 if "NAME=y" in args else 0)

    monkeypatch.setattr(coverage.subprocess, "run", run)
    out = tmp_path / "out"
    out.mkdir()
    coverage._consumers(out, {}, tmp_path / "work")
    assert executed[0] == ["make", "build"]
    assert executed[1][:3] == ["git", "clone", "--depth=1"]
    assert [args[-2] for args in runs[1:]] == ["NAME=x", "NAME=y"]
    digest, failed = coverage.advisory_cases((out / coverage.JUNIT).read_bytes())
    assert failed == ["y"]
    assert len(digest) == 64


def test_the_consumers_lane_stays_advisory_when_it_cannot_start(coverage, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def broken(args, _env):
        raise subprocess.CalledProcessError(2, args)

    monkeypatch.setattr(coverage, "_execute", broken)
    out = tmp_path / "out"
    out.mkdir()
    coverage._consumers(out, {}, tmp_path / "work")
    _digest, failed = coverage.advisory_cases((out / coverage.JUNIT).read_bytes())
    assert failed == ["lane did not run"]


def test_the_consumers_lane_stops_at_its_time_budget(coverage, tmp_path, monkeypatch):
    monkeypatch.setattr(coverage, "_consumer_names", lambda _env, _work: ("w.whl", ["make"], ["a", "b", "c"]))
    ticks = iter([0, 0, coverage.CONSUMERS_BUDGET + 1, coverage.CONSUMERS_BUDGET + 2])
    runs = []
    monkeypatch.setattr(
        coverage.subprocess, "run", lambda args, **_kw: runs.append(args) or subprocess.CompletedProcess(args, 0)
    )
    out = tmp_path / "out"
    out.mkdir()
    coverage._consumers(out, {}, tmp_path, clock=lambda: next(ticks))
    _digest, failed = coverage.advisory_cases((out / coverage.JUNIT).read_bytes())
    assert (len(runs), failed) == (1, ["b", "c"])


@pytest.mark.parametrize(
    ("kind", "tests", "case", "ok"),
    [
        ("pytest", ("tests/unit/a_test.py::test_x",), 'classname="tests.unit.a_test"', True),
        ("pytest", ("tests/unit/a_test.py",), 'classname="tests.unit.a_test.TestGroup"', True),
        ("pytest", ("tests/unit/a_test.py", "tests/unit/b_test.py"), 'classname="tests.unit.a_test"', False),
        ("vitest", ("web/src/a.test.ts",), 'classname="src/a.test.ts"', True),
        ("vitest", ("web/src/a.test.ts",), 'classname="src/other.test.ts"', False),
        ("node", ("desktop/test/a.test.js",), 'classname="test" file="/runner/work/desktop/test/a.test.js"', True),
        ("node", ("desktop/test/a.test.js",), 'classname="test" file="/runner/work/desktop/test/b.test.js"', False),
        ("vitest", ("web/ALL",), 'classname="src/anything.test.ts"', True),
    ],
)
def test_every_selected_file_must_run_a_case(coverage, kind, tests, case, ok):
    from scripts.release_candidate import CandidateError

    report = f'<testsuite><testcase {case} name="n"/></testsuite>'.encode()
    if ok:
        coverage.require_cases_cover(kind, tests, report)
    else:
        with pytest.raises(CandidateError, match="ran no case"):
            coverage.require_cases_cover(kind, tests, report)
