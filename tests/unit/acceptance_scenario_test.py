"""The advisory upgrade scenario: its decisions and workflow, without network or installs."""

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / "scripts/acceptance"


def load(name):
    spec = importlib.util.spec_from_file_location(f"acceptance_{name}_under_test", DIRECTORY / f"{name}.py")
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scenario = load("scenario")


def test_previous_release_is_the_newest_final_unyanked_one_before_n():
    releases = {
        "0.9.50": [{"yanked": False}],
        "0.9.51": [{"yanked": True}],
        "0.9.52rc1": [{"yanked": False}],
        "0.9.53": [{"yanked": False}],
        "0.9.9": [{"yanked": False}],
        "0.9.49": [],
    }
    assert scenario.previous_release("0.9.53", releases) == "0.9.50"
    with pytest.raises(scenario.HARNESS.StepFailed, match="No published release precedes"):
        scenario.previous_release("0.9.9", releases)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ({"version": "1.0.1", "phase": "idle", "core": None, "core_held": None}, True),
        ({"version": "1.0.0", "phase": "idle", "core": None, "core_held": None}, False),
        ({"version": "1.0.1", "phase": "applying", "core": None, "core_held": None}, False),
        ({"version": "1.0.1", "phase": "idle", "core": "1.0.2", "core_held": None}, False),
        ({"version": "1.0.1", "phase": "idle", "core": None, "core_held": "1.0.2"}, False),
    ],
)
def test_a_server_is_settled_only_on_n_with_nothing_left_to_do(status, expected):
    assert scenario.settled(status, "1.0.1") is expected


def test_verdict_passes_only_when_every_step_passed():
    passing = {ident: {"status": "PASS"} for ident in scenario.STEPS}
    assert scenario.verdict(passing) == "SCENARIO_PASSED"
    assert scenario.verdict(passing | {"B4": {"status": "FAIL"}}) == "SCENARIO_FAILED"
    assert scenario.verdict(passing | {"B1": {"status": "REFUSED"}}) == "SCENARIO_REFUSED"
    assert scenario.verdict({ident: passing[ident] for ident in scenario.STEPS[:-1]}) == "SCENARIO_FAILED"


@pytest.mark.parametrize(
    "argv",
    [
        ["--expect-version", "1.0"],
        ["--expect-version", "1.0.0", "--from-version", "published"],
        ["--expect-version", "1.0.0rc1"],
        ["--expect-version", "1.0.0", "--update-deadline", "10"],
    ],
)
def test_arguments_take_only_final_versions_and_a_bounded_deadline(argv):
    with pytest.raises(SystemExit):
        scenario.parse_args(argv)


def test_arguments_default_to_the_release_before_n():
    args = scenario.parse_args(["--expect-version", "published"])
    assert args.from_version == ""
    assert args.update_deadline == scenario.UPDATE_DEADLINE


def test_the_scenario_never_asks_the_updater_for_a_check():
    text = (DIRECTORY / "scenario.py").read_text()
    assert "/api/auto-update/check" not in text
    assert '"system", "update"' not in text


def test_summary_lists_every_step_and_survives_a_missing_receipt(tmp_path):
    assert "No scenario receipt" in scenario.summary(tmp_path / "missing.json")
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps({"verdict": "SCENARIO_FAILED", "steps": {"B4": {"status": "FAIL", "why": "a|b"}}}))
    text = scenario.summary(path)
    assert "SCENARIO_FAILED" in text
    assert "| B4 | FAIL | a/b |" in text
    assert "| T1 | NOT_RUN |" in text


def test_scenario_workflow_is_advisory_and_never_named_like_release_acceptance():
    workflow = yaml.safe_load((ROOT / ".github/workflows/acceptance-scenario.yml").read_text())
    assert workflow["name"] != "Acceptance"
    assert workflow["permissions"] == {}
    job = workflow["jobs"]["scenario"]
    assert not job["name"].startswith("Acceptance (")
    assert job["permissions"] == {"contents": "read"}
    assert job["strategy"]["matrix"]["os"] == ["ubuntu-24.04", "macos-latest"]
    assert "secrets." not in json.dumps(workflow)
