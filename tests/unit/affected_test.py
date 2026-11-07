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
        {"full": False, "jobs_has": ["electron"], "jobs_not": ["test-linux", "lint"]},
    ),
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
    files = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "tests/e2e").glob("*_test.py"))
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
    with pytest.raises(SystemExit, match="not a core checkout"):
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
