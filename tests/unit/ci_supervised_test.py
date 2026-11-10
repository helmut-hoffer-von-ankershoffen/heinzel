"""Supervised startup retains its original validation while receiving a separate required CI allocation."""

import runpy
import sys

import pytest
import yaml

from tests._e2e_lanes import REPO, lane_commands

pytestmark = pytest.mark.unit
SUPERVISED = {
    "tests/e2e/ui_service_test.py",
    "tests/e2e/ui_service_browser_test.py",
    "tests/e2e/offline_uninstall_test.py",
}
README = "tests/e2e/docs_test.py::test_readme_instructions"


@pytest.mark.parametrize("suite", ["", "cli", "supervised", "web-1", "web-2"])
def test_all_e2e_lanes_keep_workers_and_deadlines_and_readme_runs_only_once(suite):
    commands = lane_commands(suite)
    assert len(commands) == (2 if suite in {"", "cli"} else 1)
    for command in commands:
        assert command[:5] == ["uv", "run", "pytest", "-m", "e2e"]
        workers = "2" if sys.platform == "darwin" else "3"
        assert command[command.index("-n") + 1] == workers
        assert command.count("--session-timeout=600") == command.count("-x") == 1
    if suite in {"", "cli"}:
        assert commands[0][-1] == README
    assert commands[-1][-1] == f"--deselect={README}"
    if suite == "cli":
        assert {f"--ignore={path}" for path in SUPERVISED} <= set(commands[-1])
    elif suite == "supervised":
        assert set(commands[-1][10:-1]) == SUPERVISED
        assert not any(arg.startswith("--ignore=") for arg in commands[-1])
    elif not suite:
        assert not any(arg.startswith("--ignore=") or arg in SUPERVISED for arg in commands[-1])


def test_supervised_stays_required_in_the_full_release_candidate_matrix():
    jobs = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text())["jobs"]
    e2e = jobs["e2e"]
    assert set(e2e["strategy"]["matrix"]) == {"os", "suite"}
    assert e2e["strategy"]["matrix"]["suite"] == ["cli", "supervised", "web-1", "web-2"]
    assert e2e["strategy"]["matrix"]["os"] == (
        "${{ fromJSON(github.event_name == 'pull_request' && needs.changes.outputs.macos_full != 'true' "
        '&& \'["ubuntu-24.04"]\' || \'["ubuntu-24.04","macos-latest"]\') }}'
    )
    assert e2e["if"] == (
        "needs.changes.outputs.full_release == 'true' && "
        "(needs.changes.outputs.core == 'true' && needs.changes.outputs.site_only != 'true')"
    )
    assert e2e["strategy"]["fail-fast"] is False
    assert e2e["timeout-minutes"] == 15
    assert "continue-on-error" not in e2e
    [run] = [step for step in e2e["steps"] if step.get("run", "").startswith("make test-e2e ")]
    assert run["run"] == "make test-e2e SUITE=${{ matrix.suite }}"
    assert "if" not in run
    assert "continue-on-error" not in run
    assert "e2e" in jobs["ci-ok"]["needs"]
    gate = jobs["ci-ok"]["steps"][0]
    assert gate["env"]["RESULTS"] == "${{ join(needs.*.result, ' ') }}"
    assert '*) echo "A required job ended with: $result"; exit 1 ;;' in gate["run"]


def test_installed_browser_journey_receives_cached_runtimes_and_linux_host_dependencies_before_starting():
    steps = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text())["jobs"]["e2e"]["steps"]
    [installer] = [step for step in steps if step.get("run") == "make test-browser-deps"]
    [cache] = [step for step in steps if step.get("name") == "Reuse the browsers this lockfile's Playwright installs"]
    [run] = [step for step in steps if step.get("run", "").startswith("make test-e2e ")]
    # The supervised lane now includes Chromium and WebKit. Downloading browsers inside the driver cannot
    # install WebKit's Linux shared libraries: the existing host-dependency target must run before pytest.
    assert installer["if"] == cache["if"] == "matrix.suite == 'supervised' || startsWith(matrix.suite, 'web')"
    assert steps.index(cache) < steps.index(installer) < steps.index(run)
    makefile = (REPO / "Makefile").read_text()
    browser_target = makefile.partition("test-browser-deps:")[2].partition("# Core E2E lanes")[0]
    assert '"$$(uname -s)" = Linux' in browser_target
    assert "playwright-core install --with-deps chromium webkit" in browser_target
    assert "continue-on-error" not in installer


def test_focused_browser_lane_installs_host_dependencies_before_selected_execution():
    steps = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text())["jobs"]["focused-tests"]["steps"]
    [installer] = [step for step in steps if step.get("run") == "make test-browser-deps"]
    [runner] = [step for step in steps if step.get("name") == "Run the exact reviewed selection with fresh evidence"]
    assert installer["if"] == "matrix.kind == 'e2e'"
    assert steps.index(installer) < steps.index(runner)
    assert "continue-on-error" not in installer


def test_plugin_generation_keeps_one_complete_suite_without_core_lane_references():
    sync = runpy.run_path(str(REPO / "scripts/sync_template.py"))
    makefile = sync["makefile"]()
    workflow = sync["ci_workflow"]()
    assert makefile == (REPO / "template/Makefile").read_text()
    assert workflow == (REPO / "template/.github/workflows/ci.yml.jinja").read_text()
    for text in (makefile, workflow):
        assert "supervised" not in text
        assert "matrix.suite" not in text
        assert "E2E_SUPERVISED" not in text
        assert "SUITE=" not in text
    assert "uv run pytest -m e2e -n 3 -q --session-timeout=600 -x" in makefile
    assert "make test-e2e" in workflow
    assert "Use Ubuntu's primary archive for browser dependencies" not in workflow
