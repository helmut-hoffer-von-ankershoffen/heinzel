"""The advisory upgrade scenario: its decisions and workflow, without network or installs."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

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


def test_receipt_limits_describe_the_selected_upgrade_workload() -> None:
    candidate = " ".join(scenario.receipt_limits(candidate=True))
    assert "explicitly updates it in place" in candidate
    assert "one foreground server and answers a chat" in candidate
    assert "ADVISORY" not in candidate
    published = " ".join(scenario.receipt_limits(candidate=False))
    assert "ADVISORY" in published
    assert "two web servers" in published


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
    run = next(step for step in job["steps"] if step.get("name") == "Run the scenario")
    assert run["continue-on-error"] is True  # advisory: reported, never a red check
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    assert not any("Scenario" in str(need) for need in ci["jobs"]["ci-ok"]["needs"])


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_the_scenario_uses_the_users_default_state_folder_inside_the_private_home(tmp_path, monkeypatch, platform):
    """The updater calls any other state folder development and never updates (the first hosted run's B3)."""
    monkeypatch.setattr(scenario.sys, "platform", platform)
    (tmp_path / "home").mkdir()
    case = SimpleNamespace(
        root=tmp_path, env={"HZ_STATE_DIR": str(tmp_path / "state_dir"), "XDG_STATE_HOME": str(tmp_path / "xdg")}
    )
    folder = Path(scenario.default_state_folder(case)["state_folder"])
    assert folder.name == "hz"
    assert folder.is_relative_to(tmp_path)
    assert "HZ_STATE_DIR" not in case.env
    assert (tmp_path / "state_dir").resolve() == folder.resolve()
    with pytest.raises(scenario.HARNESS.StepFailed, match="already exists"):
        scenario.default_state_folder(case)


@pytest.mark.parametrize(("hours", "expected"), [(0, "0"), (24, "24")])
def test_the_second_pass_keeps_the_plugin_gate_so_only_the_core_updates(hours, expected):
    settings = scenario.user_settings(hours)
    assert settings["hz.auto_update_plugin_min_age_hours"] == expected
    assert settings["hz.auto_update_min_age_hours"] == "0"
    assert (
        scenario.parse_args(["--expect-version", "1.0.0", "--plugin-age-hours", str(hours)]).plugin_age_hours == hours
    )
    with pytest.raises(SystemExit):
        scenario.parse_args(["--expect-version", "1.0.0", "--plugin-age-hours", "5"])


def test_diagnostics_copy_logs_plan_and_receipts_redacted_and_never_outside_the_case(tmp_path):
    root = tmp_path / "case"
    (root / "logs").mkdir(parents=True)
    (root / "logs/01-server-a.log").write_text("token SECRET-TOKEN here")
    (root / "state_dir/web").mkdir(parents=True)
    (root / "state_dir/web/auto-update.json").write_text('{"core": "1.0.1"}')
    outside = tmp_path / "outside.txt"
    outside.write_text("not ours")
    (root / "data_dir").mkdir()
    (root / "data_dir/standard-app.json").symlink_to(outside)
    out = tmp_path / "out"
    copied = scenario.save_diagnostics(SimpleNamespace(root=root), out, {"SECRET-TOKEN"})
    assert copied == ["logs/01-server-a.log", "state_dir/web/auto-update.json"]
    assert "SECRET-TOKEN" not in (out / "logs/01-server-a.log").read_text()
    assert not (out / "data_dir/standard-app.json").exists()


def test_lane_b_takes_a_candidate_wheel_only_with_the_candidate_expectation(tmp_path):
    wheel = tmp_path / "heinzel-1.0.0-py3-none-any.whl"
    args = scenario.parse_args(["--expect-version", "candidate", "--candidate-wheel", str(wheel)])
    assert args.candidate_wheel == wheel
    assert args.from_version == ""
    for argv in (
        ["--expect-version", "candidate"],
        ["--expect-version", "1.0.0", "--candidate-wheel", str(wheel)],
        ["--expect-version", "published", "--candidate-wheel", str(wheel)],
    ):
        with pytest.raises(SystemExit):
            scenario.parse_args(argv)


def test_lane_b_passes_only_when_every_candidate_step_passed():
    passing = {ident: {"status": "PASS"} for ident in scenario.CANDIDATE_STEPS}
    assert "B3" not in scenario.CANDIDATE_STEPS
    assert scenario.verdict(passing, scenario.CANDIDATE_STEPS) == "SCENARIO_PASSED"
    assert scenario.verdict(passing | {"B4": {"status": "FAIL"}}, scenario.CANDIDATE_STEPS) == "SCENARIO_FAILED"
    assert scenario.verdict(passing) == "SCENARIO_FAILED", "the published story still needs B3"


def test_lane_b_starts_from_pypi_latest_and_binds_the_candidate(tmp_path, monkeypatch):
    harness = scenario.HARNESS
    monkeypatch.setattr(harness, "fetch", lambda url, limit: json.dumps({"info": {"version": "1.0.0"}}).encode())
    monkeypatch.setattr(
        harness, "bind_old_app", lambda version, dest: {"version": version, "profile_sha256": "p" * 64, "url": "u"}
    )
    candidate = {"version": "1.0.0", "sha256": "c" * 64, "wheel": tmp_path / "w.whl", "dir": tmp_path}
    monkeypatch.setattr(harness, "bind_candidate", lambda path, parent: candidate)
    args = SimpleNamespace(from_version="", candidate_wheel=tmp_path / "w.whl", expect_wheel_sha256="")
    old, new, identity = scenario.bind_candidate_releases(args, tmp_path)
    assert old["version"] == "1.0.0"
    assert new == {"version": "1.0.0", "wheel": candidate["wheel"], "accepted": tmp_path}
    assert identity["candidate_wheel_sha256"] == "c" * 64
    args = SimpleNamespace(from_version="0.9.9", candidate_wheel=tmp_path / "w.whl", expect_wheel_sha256="d" * 64)
    with pytest.raises(harness.StepFailed, match="differs from the expected sha256"):
        scenario.bind_candidate_releases(args, tmp_path)


def test_lane_b_updates_in_place_with_the_published_cores_own_updater(tmp_path, monkeypatch):
    calls = []

    def run_update(case, label, *, previous=None):
        calls.append((case.ctx["update_target"], label, previous))
        return {"updated": True, "before": "1.0.0", "after": "1.0.0", "message": "Reinstalled", "extra": "x"}

    monkeypatch.setattr(scenario.HARNESS, "run_update", run_update)
    case = SimpleNamespace(ctx={})
    new = {"version": "1.0.0", "wheel": tmp_path / "w.whl"}
    evidence = scenario.update_in_place(case, {"version": "1.0.0"}, new)
    assert calls == [
        ({"mode": "candidate", "version": "1.0.0", "wheel": new["wheel"]}, "hz-system-update-candidate", "1.0.0")
    ]
    assert evidence == {"updated": True, "before": "1.0.0", "after": "1.0.0", "message": "Reinstalled"}


class _Case:
    def __init__(self, ident, parent, ctx):
        self.root, self.ctx, self.env, self.steps, self.hits = parent, dict(ctx), {}, [], []
        self.first_failure, self.started = None, 0.0

    def provide_uv(self):
        pass

    def step(self, name, action):
        self.steps.append({"name": name})
        return action()


@pytest.mark.parametrize("failing", [None, "update_in_place"])
def test_lane_b_runs_its_steps_in_order_and_stops_at_the_first_failure(tmp_path, monkeypatch, failing):
    seen = []
    harness = scenario.HARNESS
    old = {"version": "1.0.0", "profile": {}, "accepted": tmp_path, "url": "u"}
    new = {"version": "1.0.0", "wheel": tmp_path / "w.whl", "accepted": tmp_path}
    monkeypatch.setattr(scenario, "bind_candidate_releases", lambda args, parent: (old, new, {"candidate": True}))
    monkeypatch.setattr(harness, "Case", _Case)
    monkeypatch.setattr(harness, "app_cycle", lambda case, name, command: seen.append("install"))

    def fake(name):
        def action(*args, **kwargs):
            seen.append(name)
            if name == failing:
                message = "boom"
                raise harness.StepFailed(message)

        return action

    for name in ("default_state_folder", "prepare_state", "update_in_place", "verify_after", "serve_and_chat"):
        monkeypatch.setattr(scenario, name, fake(name))
    monkeypatch.setattr(harness, "verify_state", fake("verify_state"))
    monkeypatch.setattr(scenario, "start_servers", fake("start_servers"))
    monkeypatch.setattr(scenario, "await_update", fake("await_update"))
    args = SimpleNamespace(candidate_wheel=new["wheel"], plugin_age_hours=0, update_deadline=60)
    result = {"steps": {}}
    scenario.run_scenario(args, tmp_path, result, {})
    steps = {ident: step["status"] for ident, step in result["steps"].items()}
    if failing is None:
        assert seen == [
            "default_state_folder", "install", "prepare_state", "verify_state",
            "update_in_place", "verify_after", "serve_and_chat",
        ]  # fmt: skip
        assert steps == dict.fromkeys(scenario.CANDIDATE_STEPS, "PASS")
        assert scenario.verdict(result["steps"], scenario.CANDIDATE_STEPS) == "SCENARIO_PASSED"
    else:
        assert seen[-1] == "update_in_place"
        assert steps["B4"] == "FAIL"
        assert steps["B5"] == steps["T1"] == "NOT_RUN"
    assert "start_servers" not in seen
    assert "await_update" not in seen


def test_lane_b_summary_names_the_gate_and_its_steps(tmp_path):
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps({"mode": "candidate", "verdict": "SCENARIO_PASSED", "steps": {}}))
    text = scenario.summary(path)
    assert "Upgrade in place, published to candidate (gate)" in text
    assert "| B3 |" not in text
    assert "| B4 | NOT_RUN |" in text
