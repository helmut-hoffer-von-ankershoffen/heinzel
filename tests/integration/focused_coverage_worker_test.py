"""Focused coverage analysis leaves a measured worker's coverage intact (#707).

The probe starts three interpreters (a measured process and its two analysis children), so it is an integration test
by the suite's taxonomy, with that level's time limit.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from coverage import CoverageData

pytestmark = pytest.mark.integration
REPO = Path(__file__).resolve().parents[2]


def test_analysis_preserves_coverage_of_instrumented_xdist_workers(tmp_path):
    """Both real analysis paths must leave a measured worker's data intact (#707).

    An xdist worker is measured because coverage's `.pth` hook starts it from `COVERAGE_PROCESS_START`, and
    `Coverage()` in such a process disables its save at exit. The probe is one process started the same way, without
    the pytest controller and worker around it: those cost most of the time and are not what this proves.
    """
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
    work = tmp_path / "work"
    probe = tmp_path / "worker_probe.py"
    probe.write_text(
        textwrap.dedent(f"""
        import os
        from pathlib import Path

        import worker_marker
        from coverage import CoverageData
        from scripts import focused_coverage as focused
        from scripts.ci_workload import FocusedPlan
        from scripts.focused_plan import Scope

        CONFIG = Path({str(REPO / "pyproject.toml")!r}).read_bytes()
        PATH = "src/heinzel/example.py"
        SOURCE = b"def value():\\n    return 42\\n"
        WORKLOAD = FocusedPlan("pull_request", "refs/pull/1/merge", "a", "b", "c", ("Focused tests (python)",))
        SCOPE = Scope(WORKLOAD, "d", "e", {{}}, {{PATH: "source"}}, "config", {{}}, {{"python": ()}}, {{}}, "d")

        assert worker_marker.before_opportunities() == 11
        focused.git = lambda *_args: SOURCE
        rows = focused.source_opportunities(SCOPE, CONFIG)
        assert rows[PATH]["missing_lines"] == [1, 2]
        assert worker_marker.after_opportunities() == 12

        assert worker_marker.before_instrument() == 13
        work = Path({str(work)!r})
        module = work / PATH
        module.parent.mkdir(parents=True)
        module.write_bytes(SOURCE)
        os.chdir(work)  # the scope's paths are relative to the checkout
        focused.git = lambda *_args: CONFIG

        def execute(args, env):
            # The selected tests' data, written here: no runner interpreter; the analysis child stays real.
            del args
            data = CoverageData(basename=env["COVERAGE_FILE"], suffix=True)
            data.add_arcs({{str(module.resolve()): [(-1, 1), (1, -1)]}})
            data.write()

        focused._execute = execute
        focused._instrument(SCOPE, ["uv", "run", "--no-sync", "pytest"], work, {{}})
        assert (work / focused.MEASUREMENT).is_file()
        assert worker_marker.after_instrument() == 14
    """)
    )
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join((str(REPO), str(tmp_path))),
        "COVERAGE_FILE": str(tmp_path / ".coverage"),
        "COVERAGE_PROCESS_START": str(config),
    }
    env.pop("COVERAGE_PROCESS_CONFIG", None)  # the outer suite's own measurement, if any, must not configure the probe
    subprocess.run([sys.executable, str(probe)], cwd=tmp_path, env=env, check=True, capture_output=True, timeout=90)
    # Read the probe's data files directly: no combine or report interpreters, and nothing measured is disturbed.
    executed: set[int] = set()
    for path in tmp_path.glob(".coverage.*"):
        data = CoverageData(basename=str(path))
        data.read()
        executed.update(*(data.lines(name) or () for name in data.measured_files() if Path(name).name == marker.name))
    assert {2, 5, 8, 11} <= executed
