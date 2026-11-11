"""Hosted-only preparation for #653; never normal PR/main acceptance evidence."""

import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import focused_coverage as coverage
from scripts import focused_job

pytestmark = pytest.mark.unit
PRODUCT = "e30d0255e63f296709ab31e5e77f1ce85a46d832"
PRODUCT_TREE = "388429205f7bdb88a66e9bb0730e49fb196db156"
COMPOSITION_PARENT = "0d3f67b78989dce2c4b9f51eff2ec08f3a2d817a"
HOOK_REPAIR = "bba0faa2b479eddc69057952ed84b6c9f9d0f9f4"
HOOK_BLOBS = {
    "scripts/scoped_checks.py": "4228d7980f2d03ff694807af631201b45921b816",
    "tests/unit/scoped_checks_test.py": "a82c28c42b448b50b6054612122c5b7b01d1f7b2",
}
BRANCH = "validation/sonar653-coverage-e30d"
PROBE = "tests/unit/sonar653_diagnostic_test.py"
REPOSITORY = "helmut-hoffer-von-ankershoffen/heinzel"
FEATURE_BLOBS = {
    "scripts/pypi_release.py": "7ea0d79dcf8a58a54c3a22af6a06b14836a1ee9b",
    "scripts/sync_template.py": "25f84cc6d2499feb04fee179b1e78e87253c186a",
    "src/heinzel/heads/_desktop_installation.py": "8e1b2e1bfb4dc79ce7a43347f5ba363642a4b59c",
    "src/heinzel/modules/system/daemon/_abandoned_mutation.py": "b34cbedd2353c727933fbfbe4abc8be221effa12",
    "tests/unit/heads/desktop_recovery_safety_test.py": "8ea7cf80154b07a1842b5aefdd28af7047f1f319",
    "tests/unit/modules/daemon_abandoned_mutation_test.py": "4e54046063f4dce9cf06de0a8a8998e617457a43",
}
PRODUCTION = {
    "src/heinzel/heads/_desktop_installation.py": "8e1b2e1bfb4dc79ce7a43347f5ba363642a4b59c",
    "src/heinzel/modules/system/daemon/_abandoned_mutation.py": "b34cbedd2353c727933fbfbe4abc8be221effa12",
}
SELECTORS = [
    "tests/unit/modules/daemon_abandoned_mutation_test.py",
    "tests/unit/modules/daemon_service_test.py",
    "tests/unit/heads/desktop_location_test.py",
    "tests/unit/heads/desktop_recovery_safety_test.py",
    "tests/unit/heads/desktop_seal_test.py",
    "tests/unit/heads/desktop_release_progress_test.py",
    "tests/unit/heads/desktop_installation_test.py::test_mismatched_bundle_identity_is_rejected",
    "tests/unit/heads/desktop_installation_test.py::test_ownership_conflicts_fail_closed",
    "tests/unit/heads/desktop_installation_test.py::test_receipt_publication_failure_rolls_back",
    "tests/unit/heads/desktop_installation_test.py::test_failed_verification_leaves_no_bundle_or_staging",
]


def hosted_context(env: dict[str, str], platform: str) -> bool:
    """A local collection or ordinary hook can never enter the probe body."""
    required = {
        "GITHUB_ACTIONS": "true",
        "CI": "true",
        "RUNNER_OS": "Linux",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_WORKFLOW": "Focused job",
        "GITHUB_REF": f"refs/heads/{BRANCH}",
        "FOCUSED_REF": BRANCH,
        "FOCUSED_KIND": "pytest",
        "FOCUSED_SELECTOR": PROBE,
        "FOCUSED_REPEAT": "1",
        "FOCUSED_STRESS": "false",
        "GITHUB_RUN_ATTEMPT": "1",
    }
    return platform == "linux" and all(env.get(key) == value for key, value in required.items())


def git(*args: str) -> str:
    """Read the exact hosted checkout without changing refs or the index."""
    return subprocess.check_output(["git", *args], text=True, timeout=30).strip()


def write_report(path: Path, value: object) -> None:
    path.write_bytes(coverage.canonical(value) + b"\n")


def denominator(config: bytes, out: Path) -> dict:
    """Use the canonical static analyzer on both complete frozen production blobs."""
    with tempfile.TemporaryDirectory(prefix="sonar653-source-") as scratch:
        root = Path(scratch)
        rc = root / "pyproject.toml"
        rc.write_bytes(config)
        paths = {}
        for name, blob in PRODUCTION.items():
            dest = root / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(subprocess.check_output(["git", "cat-file", "blob", blob], timeout=30))
            paths[str(dest.resolve())] = name
        output = root / "opportunities.json"
        coverage._analyse(rc, list(paths), output)
        report = coverage.parse(output.read_bytes())
        assert report["meta"]["branch_coverage"] is True
        inventory = {paths[str(Path(name).resolve())]: row for name, row in report["files"].items()}
        assert set(inventory) == set(PRODUCTION)
        assert all(not row["executed_lines"] and not row["executed_branches"] for row in inventory.values())
        write_report(out / "source-opportunities.json", inventory)
        return inventory


@pytest.mark.timeout(840)  # Wrapper only; every nested case keeps its unchanged original limit.
def test_frozen_product_owning_lane() -> None:
    """Run the frozen ten selectors once and retain truthful diagnostic artifacts."""
    if not hosted_context(dict(os.environ), sys.platform):
        pytest.skip("hosted Linux diagnostic only; no local collection or execution of owning desktop tests")
    head = git("rev-parse", "HEAD")
    assert head == os.environ["GITHUB_SHA"]
    assert git("rev-list", "--parents", "-n", "1", "HEAD").split() == [head, COMPOSITION_PARENT]
    assert git("rev-list", "--parents", "-n", "1", COMPOSITION_PARENT).split() == [
        COMPOSITION_PARENT,
        PRODUCT,
        HOOK_REPAIR,
    ]
    assert git("diff", "--name-only", PRODUCT, COMPOSITION_PARENT).splitlines() == sorted(HOOK_BLOBS)
    assert all(git("rev-parse", f"HEAD:{name}") == blob for name, blob in HOOK_BLOBS.items())
    assert git("rev-parse", f"{PRODUCT}^{{tree}}") == PRODUCT_TREE
    assert git("diff", "--name-status", COMPOSITION_PARENT, "HEAD") == f"A\t{PROBE}"
    assert git("status", "--porcelain", "--untracked-files=no") == ""
    assert all(git("rev-parse", f"HEAD:{name}") == blob for name, blob in FEATURE_BLOBS.items())
    assert sys.version_info[:2] == (3, 14)
    assert len(SELECTORS) == 10
    out = Path("focused-artifacts/sonar653-inner")
    out.mkdir(parents=True, exist_ok=False)
    binding = {
        "kind": "diagnostic-only-not-pr-or-main-acceptance",
        "product_head": PRODUCT,
        "product_tree": PRODUCT_TREE,
        "diagnostic_head": head,
        "composition_parent": COMPOSITION_PARENT,
        "hook_repair": HOOK_REPAIR,
        "hook_blobs": HOOK_BLOBS,
        "run": os.environ["GITHUB_RUN_ID"],
        "attempt": 1,
        "selectors": SELECTORS,
        "production": PRODUCTION,
        "feature_blobs": FEATURE_BLOBS,
        "config_blob": git("rev-parse", "HEAD:pyproject.toml"),
        "python": sys.version,
    }
    write_report(out / "binding.json", binding)
    config = Path("pyproject.toml").read_bytes()
    coverage.config_policy(config)
    expected = denominator(config, out)
    commands = focused_job.plan("pytest", " ".join(SELECTORS), out)
    command = commands[-1]
    assert command[:3] == ["uv", "run", "--no-sync"]
    datafile = str((out / ".coverage").resolve())
    env = {key: os.environ[key] for key in coverage.ENV_KEYS if key in os.environ}
    env.update(UV_FROZEN="1", COVERAGE_FILE=datafile, HZ_SCREENSHOTS=str((out / "screenshots").resolve()))
    measured = [*command[:3], "coverage", "run", "--rcfile=pyproject.toml", "-m", *command[3:]]
    write_report(out / "command.json", measured)
    with (out / "pytest.log").open("wb") as log:
        result = subprocess.run(measured, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=780, check=False)
    write_report(out / "pytest-result.json", {"returncode": result.returncode})
    # Preserve measurements even if the producer/test gate fails; no exception becomes a passing case digest.
    coverage._analyse(Path("pyproject.toml"), list(PRODUCTION), out / "coverage.json", datafile=datafile)
    report = coverage.parse((out / "coverage.json").read_bytes())
    assert report["meta"]["branch_coverage"] is True
    assert report["meta"]["version"] == coverage.COVERAGE_VERSION
    assert set(report["files"]) == set(PRODUCTION)
    counts = {
        name: {kind: coverage._counts(row, expected[name], kind) for kind in ("lines", "branches")}
        for name, row in report["files"].items()
    }
    covered = sum(hit for row in counts.values() for hit, _ in row.values())
    total = sum(size for row in counts.values() for _, size in row.values())
    assert total > 0
    write_report(
        out / "coverage-counts.json",
        {
            "files": counts,
            "covered": covered,
            "total": total,
            "report_threshold": coverage.THRESHOLD,
            "floor": coverage.FLOOR,
            "passes_report": 100 * covered >= coverage.THRESHOLD * total,
            "passes_floor": 100 * covered >= coverage.FLOOR * total,
        },
    )
    junit = (out / "junit.xml").read_bytes()
    # XML came from this bounded test execution; the canonical digest reader rejects skipped/failed/duplicate cases.
    case_digest = coverage.cases_digest(junit)
    write_report(
        out / "cases.json",
        {
            "count": len(list(ET.fromstring(junit).iter("testcase"))),  # noqa: S314 - canonical reader validated bounded XML
            "case_sha256": case_digest,
        },
    )
    assert result.returncode == 0, "inner tests failed; inspect raw nested JUnit/log, not outer diagnostic status"
    assert 100 * covered >= coverage.FLOOR * total
    assert 100 * covered >= coverage.THRESHOLD * total
