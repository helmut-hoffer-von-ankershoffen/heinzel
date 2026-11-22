"""The committed release-policy manifest is exactly what this commit's workflow, recorder and lanes derive."""

import fnmatch
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from heinzel.testing import scaled

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def release_policy(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    from scripts import release_policy

    return release_policy


@pytest.fixture
def manifest(release_policy):
    return json.loads((ROOT / release_policy.MANIFEST).read_text(encoding="utf-8"))


def test_committed_manifest_matches_its_regeneration_byte_for_byte(release_policy):
    committed = (ROOT / release_policy.MANIFEST).read_bytes()
    assert committed == release_policy.render().encode(), "stale: run python scripts/release_policy.py"
    assert release_policy.main(["--check"]) == 0


def _tracked() -> list[str]:
    run = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True, timeout=scaled(30))
    return [path for path in run.stdout.decode().split("\0") if path]


def test_ci_checks_the_manifest_through_make_lint(release_policy):
    """`make lint` and `make check-fast` refuse a stale manifest; the Lint job runs `make lint` on Core changes."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    step = '"&release-policy=uv run --script scripts/release_policy.py --check"'
    lint = makefile[makefile.index("LINT := ") : makefile.index("TYPES := ")]
    assert step in lint
    jobs = yaml.safe_load((ROOT / release_policy.WORKFLOW).read_text(encoding="utf-8"))["jobs"]
    assert next(step for step in jobs["lint"]["steps"] if step.get("run") == "make lint").get("if") is None
    template = (ROOT / "template/Makefile").read_text(encoding="utf-8")
    assert "release_policy" not in template  # plugins have no Core release policy


def test_a_later_edit_makes_the_manifest_stale_but_a_new_plain_product_test_does_not(release_policy, tmp_path):
    from scripts.ci_paths import GLOBS

    tracked = _tracked()
    # The policy reads workflow files and only glob-matched Python contents. Keep the complete tracked path list,
    # but do not copy unrelated assets and scripts just to derive the manifest.
    inputs = [
        path
        for path in tracked
        if re.fullmatch(r"\.github/workflows/[^/]+\.ya?ml", path)
        or (path.endswith(".py") and any(fnmatch.fnmatchcase(path, glob) for glob in GLOBS))
    ]
    for path in inputs:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        if (ROOT / path).is_symlink():
            (tmp_path / path).symlink_to((ROOT / path).readlink())
        else:
            shutil.copyfile(ROOT / path, tmp_path / path)
    assert release_policy.render(tmp_path, tracked) == release_policy.render()
    workflow = tmp_path / release_policy.WORKFLOW
    original = workflow.read_text(encoding="utf-8")
    renamed = original.replace("name: Focused tests (", "name: Focused checks (")
    assert renamed != original
    workflow.write_text(renamed, encoding="utf-8")
    assert release_policy.render(tmp_path, tracked) != release_policy.render()
    workflow.write_text(original, encoding="utf-8")
    added = "tests/unit/kernel/zz_added_test.py"
    (tmp_path / added).write_text("def test_added():\n    pass\n", encoding="utf-8")
    assert release_policy.render(tmp_path, [*tracked, added]) == release_policy.render()  # no manifest change
    (tmp_path / added).write_text("pytest_plugins = ('x',)\n", encoding="utf-8")
    rendered = json.loads(release_policy.render(tmp_path, [*tracked, added]))
    assert added in rendered["not_ci"]["except"]  # a test that configures pytest is CI, and stales the manifest


def test_check_refuses_a_stale_or_missing_manifest(release_policy, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(release_policy, "ROOT", tmp_path)
    assert release_policy.main(["--check"]) == 1
    (tmp_path / release_policy.MANIFEST).parent.mkdir(parents=True)
    (tmp_path / release_policy.MANIFEST).write_text("{}\n", encoding="utf-8")
    assert release_policy.main(["--check"]) == 1
    assert "stale" in capsys.readouterr().err


def test_manifest_has_exactly_the_keys_the_release_tooling_parses(manifest):
    assert sorted(manifest) == [
        "aggregate",
        "expected_jobs",
        "focused_job",
        "gate_checks",
        "judge_workflow",
        "lanes",
        "not_ci",
        "policy",
        "quarantine",
        "recorder",
        "repository",
        "required_contexts",
        "test_jobs",
        "workflow",
    ]
    assert manifest["policy"] == 1


def test_expected_jobs_split_the_recorders_inventory_without_overlap(release_policy, manifest):
    for event, split in manifest["expected_jobs"].items():
        inventory = release_policy.expected_jobs(event)
        assert sorted(split) == ["skipped", "success"]
        assert not set(split["success"]) & set(split["skipped"])
        assert {name: conclusion for conclusion, names in split.items() for name in names} == inventory
        for names in split.values():
            assert names == sorted(set(names))
        assert manifest["aggregate"] in split["success"]
    assert sorted(manifest["expected_jobs"]) == sorted(release_policy.EVENTS)


def test_aggregate_and_required_contexts_follow_the_workflows(release_policy, manifest):
    from scripts.focused_gate import API_CHECKS

    jobs = yaml.safe_load((ROOT / release_policy.WORKFLOW).read_text(encoding="utf-8"))["jobs"]
    assert manifest["aggregate"] == jobs[release_policy.AGGREGATE_JOB]["name"]
    assert set(manifest["required_contexts"]) == {manifest["aggregate"], *API_CHECKS}
    assert manifest["required_contexts"] == sorted(set(manifest["required_contexts"]))
    gate = (ROOT / ".github/workflows/merge-gate.yml").read_text(encoding="utf-8")
    assert all(context in gate for context in release_policy.GATE_CONTEXTS)


def test_aggregate_refuses_a_workflow_without_the_aggregate_job(release_policy):
    with pytest.raises(release_policy.PolicyError, match="no named ci-ok job"):
        release_policy.aggregate("jobs:\n  build:\n    name: Build\n")


def test_quarantine_jobs_come_from_the_recorder_and_stay_out_of_the_inventory(release_policy, manifest):
    quarantine = manifest["quarantine"]
    assert quarantine == {
        "idle": release_policy.QUARANTINE_IDLE,
        "plan": release_policy.QUARANTINE_PLAN,
        "registry": release_policy.QUARANTINE_REGISTRY,
        "runs": sorted(release_policy.QUARANTINE_RUNS),
    }
    names = {quarantine["idle"], quarantine["plan"], quarantine["registry"], *quarantine["runs"]}
    for split in manifest["expected_jobs"].values():
        assert not names & {*split["success"], *split["skipped"]}


def test_test_jobs_and_lanes_are_derived(release_policy, manifest):
    successes = [set(split["success"]) for split in manifest["expected_jobs"].values()]
    assert manifest["test_jobs"] == sorted({job for names in successes for job in names if job.startswith("Test (")})
    assert all(set(manifest["test_jobs"]) <= names for names in successes)
    assert manifest["aggregate"] not in manifest["test_jobs"]
    assert manifest["lanes"] == {
        lane: {"kind": kind, "python": python, "runner": runner}
        for lane, (runner, python, kind) in release_policy.LANES.items()
    }
    assert all(release_policy.LANE_NAME.fullmatch(lane) for lane in manifest["lanes"])


def test_main_writes_the_manifest_and_refuses_unknown_arguments(release_policy, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(release_policy, "ROOT", tmp_path)
    (tmp_path / release_policy.MANIFEST).parent.mkdir(parents=True)
    assert release_policy.main([]) == 0
    assert (tmp_path / release_policy.MANIFEST).read_bytes() == (ROOT / release_policy.MANIFEST).read_bytes()
    assert release_policy.main(["--check"]) == 0
    assert release_policy.main(["--write"]) == 2
    assert "Usage" in capsys.readouterr().err


def test_policy_refuses_an_aggregate_that_is_not_required_or_an_unplain_lane(release_policy, monkeypatch):
    monkeypatch.setattr(release_policy, "LANES", {"Bad lane": ("ubuntu-24.04", "3.15", "pytest")})
    with pytest.raises(release_policy.PolicyError, match="not a plain lane name"):
        release_policy.policy()
    monkeypatch.setattr(release_policy, "expected_jobs", lambda event: {"CI OK": "skipped"})
    with pytest.raises(release_policy.PolicyError, match="not a required success job on pull_request"):
        release_policy.policy()


def test_focused_job_is_the_workflows_lane_job_with_a_lane_placeholder(release_policy, manifest):
    jobs = yaml.safe_load((ROOT / release_policy.WORKFLOW).read_text(encoding="utf-8"))["jobs"]
    assert manifest["focused_job"].replace("{lane}", "${{ matrix.lane }}") == jobs["focused-tests"]["name"]
    assert manifest["focused_job"].count("{lane}") == 1
    assert "{" not in manifest["focused_job"].replace("{lane}", "")
    assert (
        manifest["focused_job"].replace("{lane}", "${{ matrix.lane }}")
        in manifest["expected_jobs"]["pull_request"]["skipped"]
    )
    with pytest.raises(release_policy.PolicyError, match="names no lane"):
        release_policy.focused_job("jobs:\n  focused-tests:\n    name: Focused tests\n")


def _ci_inputs() -> list[str]:
    """Every tracked file a CI run reads to decide what it checks: never not CI, wherever it lives."""
    name = re.compile(
        r"(^|/)(conftest\.py|pyproject\.toml(\.jinja|\.tmpl)?|Makefile[^/]*|GNUmakefile|[^/]*\.mk|quarantine\.(toml|py)"
        r"|[^/]*\.lock|pnpm-lock\.yaml|package(-lock)?\.json|\.python-version|\.tool-versions|[^/]*\.config\.[a-z]+"
        r"|setup\.(py|cfg|ts)|tsconfig[^/]*\.json|biome\.json|[^/]*\.d\.ts)$",
        re.IGNORECASE,
    )
    roots = (
        ".github/",
        "scripts/",
        "template/",
        "starter/",
        "tests/e2e/",
        "tests/integration/",
        "web/e2e/",
        "desktop/",
    )
    inputs = [path for path in _tracked() if path.startswith(roots) or name.search(path) or "/" not in path]
    gates = [path for path in _tracked() if re.fullmatch(r"tests/unit/[^/]+_test\.py", path)]
    return sorted({*inputs, *gates, "src/heinzel/testing.py", "web/src/test/setup.ts"})


def test_no_ci_input_is_not_ci(manifest):
    from scripts import ci_paths

    rule = manifest["not_ci"]
    inputs = _ci_inputs()
    assert len(inputs) > 500  # workflows, scripts, configs, conftests, drivers, gate tests, root files
    assert [path for path in inputs if ci_paths.is_not_ci(path, rule)] == []
    for gate in (
        "tests/unit/release_policy_test.py",
        "tests/unit/standards_test.py",
        "tests/unit/quarantine_test.py",
        "tests/unit/release_candidate_test.py",
        "tests/unit/focused_plan_test.py",
    ):
        assert (ROOT / gate).is_file(), gate
        assert not ci_paths.is_not_ci(gate, rule), gate


def test_not_ci_is_globs_and_literal_exceptions(manifest, tmp_path):
    from scripts import ci_paths

    rule = manifest["not_ci"]
    assert sorted(rule) == ["except", "match"]
    assert rule["match"] == sorted(set(ci_paths.GLOBS))
    assert rule["except"] == sorted(set(rule["except"]))
    assert all(ci_paths.LITERAL.fullmatch(path) and (ROOT / path).is_file() for path in rule["except"])
    assert all(glob.strip() and not glob.startswith("/") and ".." not in glob.split("/") for glob in rule["match"])
    assert "tests/unit/modules/daemon_legacy_retirement_test.py" in rule["except"]  # it sets pytest_plugins
    odd = "tests/unit/kernel/odd[1]_test.py"  # an exception must be a literal path, never a glob
    (tmp_path / odd).parent.mkdir(parents=True)
    (tmp_path / odd).write_text("pytest_plugins = ('x',)\n", encoding="utf-8")
    with pytest.raises(ValueError, match="literally"):
        ci_paths.exceptions(tmp_path, [odd])


def test_not_ci_covers_product_code_and_its_tests_and_fails_closed(manifest):
    from scripts import ci_paths

    rule = manifest["not_ci"]
    tracked = _tracked()
    for prefix in (*ci_paths.PRODUCT_PACKAGES, *ci_paths.PRODUCT_TESTS, "web/src/", "docs/"):
        present = [path for path in tracked if path.startswith(prefix)]  # a package may not exist yet (connectors)
        assert not present or any(ci_paths.is_not_ci(path, rule) for path in present), prefix
    assert sum(ci_paths.is_not_ci(path, rule) for path in tracked) > 500
    for path in (
        "src/heinzel/kernel/_kernel.py",
        "web/src/App.tsx",
        "tests/unit/kernel/kernel_test.py",
        "tests/unit/kernel/brand_new_test.py",  # a new plain product test needs no manifest change
    ):
        assert ci_paths.is_not_ci(path, rule), path
    for path in ("", "README.md", "new/x.py", "src/heinzel/__init__.py", "tests/integration/cli_test.py"):
        assert not ci_paths.is_not_ci(path, rule), path


@pytest.mark.parametrize(
    "name",
    [
        "conftest.py",
        "deep/er/conftest.py",
        "setup.py",
        "pytest.ini",
        "pyproject.toml",
        "setup.cfg",
        "tox.ini",
        "Makefile",
        "rules.mk",
        "package.json",
        "uv.lock",
        "vitest.config.ts",
        "playwright.config.ts",
        "vite.config.tsx",
        "setup.ts",
        "lib/setup.tsx",
        "global.d.ts",
        ".env",
        "run.sh",
        "helpers.py.orig",
    ],
)
def test_a_new_file_a_tool_loads_by_itself_is_ci_wherever_it_is_added(manifest, name):
    """A conftest, plugin, setup or config file added under a not-CI directory is still CI."""
    from scripts import ci_paths

    for prefix in (*ci_paths.PRODUCT_TESTS, "web/src/components/", "docs/", *ci_paths.PRODUCT_PACKAGES):
        assert not ci_paths.is_not_ci(prefix + name, manifest["not_ci"]), prefix + name


def test_the_generated_globs_match_every_other_name_of_their_type(release_policy):
    from scripts import ci_paths

    rule = {"except": [], "match": list(ci_paths.GLOBS)}
    for stem in ("a", "_kernel", "fest", "contest", "my_conftest_helper", "setup_wizard", "upup", "x.t", "ab"):
        assert ci_paths.is_not_ci(f"src/heinzel/kernel/{stem}.py", rule), stem
        assert ci_paths.is_not_ci(f"web/src/lib/{stem}.ts", rule), stem
        assert ci_paths.is_not_ci(f"web/src/lib/{stem}.tsx", rule), stem
    # fail-closed: a name that ends like a loaded one, or is shorter than one ("test.py"), stays CI
    for name in ("myconftest.py", "tsetup.py", "test.py"):
        assert not ci_paths.is_not_ci(f"src/heinzel/kernel/{name}", rule), name
    for name in ("appconfig.ts", "x.d.ts", "presetup.tsx"):
        assert not ci_paths.is_not_ci(f"web/src/lib/{name}", rule), name
    assert ci_paths.ending(".py", ("setup.py", ".py")) == []  # an excluded bare extension leaves nothing


def test_every_matched_file_that_configures_pytest_is_an_exception(manifest, tmp_path):
    from scripts import ci_paths

    rule = manifest["not_ci"]
    for path in _tracked():
        if path.endswith(".py") and any(fnmatch.fnmatchcase(path, glob) for glob in rule["match"]):
            configures = ci_paths.CONFIGURES_PYTEST.search((ROOT / path).read_text(encoding="utf-8"))
            assert bool(configures) == (path in rule["except"]), path
    (tmp_path / "tests/unit/kernel").mkdir(parents=True)
    for body in ("pytest_plugins = ('x',)\n", "def pytest_configure(config):\n    pass\n", "def test_x():\n    pass\n"):
        (tmp_path / "tests/unit/kernel/a_test.py").write_text(body, encoding="utf-8")
        listed = ci_paths.exceptions(tmp_path, ["tests/unit/kernel/a_test.py"])
        assert listed == (["tests/unit/kernel/a_test.py"] if "pytest_" in body else []), body


def test_judge_workflow_is_the_one_workflow_that_runs_the_merge_gate_judge(release_policy, manifest):
    assert manifest["judge_workflow"] == ".github/workflows/merge-gate.yml"
    assert manifest["judge_workflow"] != manifest["workflow"]
    assert release_policy.JUDGE in (ROOT / manifest["judge_workflow"]).read_text(encoding="utf-8")
    assert all(
        context in (ROOT / manifest["judge_workflow"]).read_text(encoding="utf-8")
        for context in release_policy.GATE_CONTEXTS
    )
    tracked = _tracked()
    with pytest.raises(release_policy.PolicyError, match="expected one workflow"):
        release_policy.judge_workflow(ROOT, [path for path in tracked if path != manifest["judge_workflow"]])


def test_judge_workflow_refuses_two_judges_or_the_ci_workflow(release_policy, tmp_path):
    workflows = tmp_path / ".github/workflows"
    workflows.mkdir(parents=True)
    run = "jobs:\n  gate:\n    steps:\n      - run: python3 -I scripts/merge_gate.py\n"
    for name in ("merge-gate.yml", "second.yml", "ci.yml"):
        (workflows / name).write_text(run, encoding="utf-8")
    paths = [f".github/workflows/{name}" for name in ("merge-gate.yml", "second.yml")]
    with pytest.raises(release_policy.PolicyError, match="expected one workflow"):
        release_policy.judge_workflow(tmp_path, paths)
    with pytest.raises(release_policy.PolicyError, match="expected one workflow"):
        release_policy.judge_workflow(tmp_path, [release_policy.WORKFLOW])
    assert release_policy.judge_workflow(tmp_path, [paths[1], "README.md"]) == paths[1]


def test_gate_checks_name_the_sonar_and_coverage_checks_the_judge_reports(release_policy, manifest):
    assert manifest["gate_checks"] == {"coverage": "coverage-floor", "sonar": "merge-gate"}
    assert set(manifest["gate_checks"].values()) == set(manifest["required_contexts"]) - {manifest["aggregate"]}
    assert len(set(manifest["gate_checks"].values())) == len(manifest["gate_checks"])
    judge = (ROOT / manifest["judge_workflow"]).read_text(encoding="utf-8")
    swapped = judge.replace("steps.sonar.outcome", "steps.SWAP.outcome").replace(
        "steps.coverage.outcome", "steps.sonar.outcome"
    )
    assert release_policy.gate_checks(swapped.replace("steps.SWAP.outcome", "steps.coverage.outcome")) == {
        "coverage": "merge-gate",
        "sonar": "coverage-floor",
    }
    with pytest.raises(release_policy.PolicyError, match="0 step outcomes"):
        release_policy.gate_checks(judge.replace("MERGE_GATE_OUTCOME", "OTHER_OUTCOME"))
    with pytest.raises(release_policy.PolicyError, match="not coverage and sonar"):
        release_policy.gate_checks(judge.replace("steps.sonar.outcome", "steps.quality.outcome"))
