"""The affected-tests selector narrows only what it knows and selects everything otherwise."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("affected", ROOT / "scripts" / "affected.py")
assert spec
assert spec.loader
affected = importlib.util.module_from_spec(spec)
sys.modules["affected"] = affected
spec.loader.exec_module(affected)

pytestmark = [pytest.mark.unit, pytest.mark.timeout(60)]  # the import graph parses the tree: ~1 s idle, more loaded

# Each case is (name, changed files, expectations). `<key>_has` / `<key>_not` check containment, so the repo may grow.
CASES: list[tuple[str, list[str], dict[str, Any]]] = [
    (
        "web-only page",
        ["web/src/pages/Home.tsx", "web/src/pages/Home.test.tsx"],
        {
            "full": False,
            "jobs_has": ["lint", "e2e-web-1"],
            "jobs_not": ["test-linux", "coverage", "e2e-cli"],
            "vitest_has": ["web/src/pages/Home.test.tsx"],
            "pytest": [],
        },
    ),
    (
        "web shared i18n",
        ["web/src/i18n.ts", "web/src/layout/SearchPalette.tsx"],
        {"full": False, "vitest": ["ALL"], "jobs_not": ["test-linux"]},
    ),
    (
        "docs only",
        ["docs/guides/install.md", "README.md"],
        {
            "full": False,
            "jobs_has": ["site-tests"],
            "jobs_not": ["lint", "test-linux", "e2e-web-1"],
            "pytest_has": ["tests/unit/documentation_test.py"],
        },
    ),
    ("kernel = full", ["src/heinzel/kernel/_session_store.py"], {"full": True, "pytest": ["ALL"]}),
    ("workflow = full", [".github/workflows/ci.yml"], {"full": True}),
    ("lockfile = full", ["uv.lock"], {"full": True}),
    ("daemon module = full", ["src/heinzel/modules/system/daemon/_service.py"], {"full": True}),
    (
        "notify module",
        ["src/heinzel/modules/system/notify/_store.py", "tests/unit/modules/notify_recovery_test.py"],
        {
            "full": False,
            "jobs_has": ["test-linux", "coverage", "e2e-web-1", "macos-smoke"],
            "jobs_not": ["e2e-cli"],
            "pytest_has": ["tests/unit/modules/notify_recovery_test.py"],
        },
    ),
    (
        "cli head",
        ["src/heinzel/heads/cli/_overview.py", "tests/unit/heads/cli_overview_test.py"],
        {
            "full": False,
            "jobs_has": ["e2e-cli", "e2e-supervised", "consumers"],
            "pytest_has": ["tests/unit/heads/cli_overview_test.py"],
        },
    ),
    (
        "one unit test",
        ["tests/unit/standards_test.py"],
        {"full": False, "pytest_has": ["tests/unit/standards_test.py"], "jobs_has": ["site-tests"]},
    ),
    (
        "integration test only",
        ["tests/integration/installation_test.py"],
        {
            "full": False,
            "pytest": ["tests/integration/installation_test.py"],
            "jobs_has": ["test-linux"],
            "jobs_not": ["e2e-web-1"],
        },
    ),
    (
        "e2e script",
        ["web/e2e/mobile-responsive.mjs"],
        {"full": False, "e2e_lanes": ["web-1"], "jobs_not": ["test-linux"]},
    ),
    ("e2e python test", ["tests/e2e/audio_visualizer_test.py"], {"full": False, "e2e_lanes": ["web-2"]}),
    (
        "desktop",
        ["desktop/src/main.js", "desktop/test/window.test.js"],
        {
            "full": False,
            "jobs_has": ["electron"],
            "jobs_not": ["test-linux", "lint"],
            "desktop_has": ["desktop/test/window.test.js"],
        },
    ),
    (
        "desktop source selects the node tests that import it",
        ["desktop/src/shutdown.js"],
        {"full": False, "desktop": ["desktop/test/shutdown.test.js", "desktop/test/window.test.js"]},
    ),
    ("desktop lockfile runs the full set", ["desktop/pnpm-lock.yaml"], {"full": True}),
    ("desktop package metadata runs the full set", ["desktop/package.json"], {"full": True}),
    (
        "nested prose runs the site tests",
        ["research/notes.md", "desktop/README.md"],
        {"full": False, "jobs_has": ["site-tests"]},
    ),
    ("a deleted desktop test selects the whole desktop suite", ["desktop/test/gone.test.js"], {"desktop": ["ALL"]}),
    ("managed backend runs the full set", ["desktop/managed/src/backend.ts"], {"full": True}),
    ("desktop readme runs no desktop test", ["desktop/README.md"], {"desktop": [], "jobs_has": ["site-tests"]}),
    ("conftest = full", ["tests/conftest.py"], {"full": True}),
    ("unknown path = full", ["something/new.txt"], {"full": True}),
    (
        "openapi",
        ["web/openapi.json", "web/src/api/schema.d.ts"],
        {"full": False, "jobs_has": ["test-linux", "e2e-web-2"], "vitest": ["ALL"]},
    ),
    (
        "heinzel module",
        ["src/heinzel/modules/heinzel/_autonomous_schedule.py"],
        {
            "full": False,
            "pytest_has": ["tests/unit/modules/autonomous_schedule_test.py"],
            "jobs_has": ["e2e-web-1"],
            "jobs_not": ["e2e-cli", "macos-smoke"],
        },
    ),
    ("empty", [], {"full": False, "jobs": list(affected.JOBS_ALWAYS)}),
]


@pytest.fixture(scope="module")
def repo() -> Any:
    return affected.Repo(ROOT)


def problems(got: dict[str, Any], want: dict[str, Any]) -> list[str]:
    found = []
    for key, value in want.items():
        if key.endswith("_has"):
            missing = [v for v in value if v not in got[key[:-4]]]
            if missing:
                found.append(f"{key[:-4]} lacks {missing}")
        elif key.endswith("_not"):
            present = [v for v in value if v in got[key[:-4]]]
            if present:
                found.append(f"{key[:-4]} should not have {present}")
        elif got[key] != value:
            found.append(f"{key} = {got[key]!r}, want {value!r}")
    return found


@pytest.mark.parametrize(("files", "want"), [case[1:] for case in CASES], ids=[case[0] for case in CASES])
def test_selection_matches_the_expectation(repo, files, want):
    got = affected.classify(repo, files)
    assert problems(got, want) == []
    assert not (got["full"] is False and got["pytest"] == ["ALL"]), "pytest ALL without full"


def test_every_e2e_file_has_a_lane(repo):
    files = sorted(str(p.relative_to(ROOT)) for p in (ROOT / affected.E2E_DIR).glob("*_test.py"))
    assert [f for f in files if repo.lane_of(f) is None] == []


def test_the_selector_itself_is_a_script_change_so_it_selects_everything(repo):
    assert affected.classify(repo, ["scripts/affected.py"])["full"] is True


def test_a_mixed_change_with_one_unknown_path_selects_everything(repo):
    got = affected.classify(repo, ["docs/guides/install.md", "something/new.txt"])
    assert got["full"] is True
    assert got["pytest"] == ["ALL"]
    assert got["jobs"] == [*affected.JOBS_ALWAYS, *affected.JOBS_FULL]
    assert any("something/new.txt" in reason for reason in got["reasons"])


def test_a_directory_that_is_not_a_checkout_is_refused(tmp_path):
    with pytest.raises(affected.UnusableRepoError, match="not a core checkout"):
        affected.Repo(tmp_path)


def test_the_cli_prints_json_for_the_repo_it_lives_in():
    out = subprocess.run(
        [sys.executable, "-I", str(ROOT / "scripts/affected.py"), "--json", "docs/guides/install.md"],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    result = json.loads(out.stdout)
    assert result["full"] is False
    assert "site-tests" in result["jobs"]


def test_the_cli_exits_two_for_an_unusable_repo(tmp_path):
    out = subprocess.run(
        [sys.executable, "-I", str(ROOT / "scripts/affected.py"), "--repo", str(tmp_path), "README.md"],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 2
    assert "not a core checkout" in out.stderr


@pytest.mark.parametrize("base", ["--output=/tmp/x", "-p", "main;rm", "a b", ""])
def test_a_base_that_is_not_a_revision_never_reaches_git(base):
    with pytest.raises(SystemExit, match="not a git revision"):
        affected.changed_from_git(ROOT, base)


def test_a_base_that_names_no_commit_is_refused():
    with pytest.raises(SystemExit, match="is not a commit"):
        affected.changed_from_git(ROOT, "no-such-branch-for-affected-test")


def test_a_base_is_diffed_as_its_commit_id():
    assert isinstance(affected.changed_from_git(ROOT, "HEAD"), list)


def test_makefile_variables_are_read_by_line():
    text = "E2E_SUPERVISED := a b\n\tE2E_SUPERVISED := recipe\nOTHER = x\n"
    assert affected.make_variable(text, "E2E_SUPERVISED") == "a b"
    assert affected.make_variable(text, "OTHER") is None


def test_web_imports_are_found_in_each_form():
    text = 'import a from "./a"\nexport { b } from \'./b\'\nconst c = await import("./c")\nimport "./d.css"\n'
    found = sorted(spec for pattern in affected.WEB_IMPORTS for spec in pattern.findall(text))
    assert found == ["./a", "./b", "./c", "./d.css"]


def test_every_tracked_file_maps_to_a_lane_or_the_full_set(repo):
    """Each tracked path, one at a time: the full set, or at least one focused lane. Names every path with neither."""
    files = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files"], check=True, capture_output=True, text=True, timeout=60
    ).stdout.split()
    unmapped = []
    for path in files:
        result = affected.classify(repo, [path])
        if result["full"]:
            continue
        lanes, reason = affected.focused_lanes(result, repo.lanes)
        if lanes is None and reason == "the selector selects no tests":
            unmapped.append(path)
    assert len(files) > 1000
    assert not unmapped, "no lane and no full-set rule for:\n" + "\n".join(unmapped)


def test_lane_limits_and_tokens_match_the_focused_ci_resolver(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    from scripts import focused_job, focused_plan

    assert affected.LANE_LIMIT == focused_job.MAX_IDS
    assert affected.PYTHON_LANE_LIMIT == focused_job.PYTHON_CI_MAX_IDS
    assert affected.WEB_ALL == focused_plan.WHOLE_SUITES["web"] == focused_job.VITEST_ALL
    assert focused_plan.WHOLE_SUITES["desktop"] == affected.DESKTOP_ALL
    assert focused_plan.WHOLE_SUITES["consumers"] == affected.CONSUMERS_MAIN
    for lane in affected.BROWSER_LANES:
        assert focused_plan.WHOLE_SUITES[f"e2e-{lane}"] == f"{affected.E2E_DIR}/{lane}"


def _result(**changes):
    base = {"full": False, "pytest": [], "vitest": [], "e2e_lanes": [], "jobs": [], "desktop": []}
    return base | changes


@pytest.mark.parametrize(
    ("result", "lanes", "reason"),
    [
        (_result(full=True), None, "the selector selects the full set"),
        (_result(pytest=["tests/unit/"]), None, "a test directory is selected"),
        (_result(e2e_lanes=["cli"]), None, "e2e lane(s) cli have no focused lane"),
        (_result(pytest=[f"tests/unit/t{i}_test.py" for i in range(33)]), None, "33 pytest files exceed"),
        (_result(), None, "the selector selects no tests"),
        (_result(pytest=["tests/unit/a_test.py"]), {"python": ["tests/unit/a_test.py"]}, "focused"),
        (_result(vitest=["ALL"]), {"web": ["web/ALL"]}, "focused"),
        (_result(vitest=[f"web/src/f{i}.test.ts" for i in range(11)]), {"web": ["web/ALL"]}, "focused"),
        (_result(desktop=[f"desktop/test/t{i}.test.js" for i in range(11)]), {"desktop": ["desktop/ALL"]}, "focused"),
        (
            _result(e2e_lanes=["web-2"], vitest=["web/src/a.test.ts"]),
            {"web": ["web/src/a.test.ts"], "browser": ["tests/e2e/b_test.py"]},
            "focused",
        ),
        (
            _result(e2e_lanes=["web-1", "web-2"], vitest=["web/src/a.test.ts"]),
            {"web": ["web/src/a.test.ts"], "e2e-web-1": ["tests/e2e/web-1"], "e2e-web-2": ["tests/e2e/web-2"]},
            "focused",
        ),
        (_result(jobs=["consumers"], pytest=["tests/unit/a_test.py"]), None, None),
    ],
)
def test_focused_lanes_fail_open_and_bound_each_lane(result, lanes, reason):
    files = {"web-1": [f"tests/e2e/w{i}_test.py" for i in range(11)], "web-2": ["tests/e2e/b_test.py"]}
    got, why = affected.focused_lanes(result, files)
    if reason is None:  # contract paths add the consumers lane
        assert got == {"python": ["tests/unit/a_test.py"], "consumers": ["plugins/main"]}
        return
    assert got == lanes
    assert why.startswith(reason)
