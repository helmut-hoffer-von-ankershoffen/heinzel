"""`contexts.toml` is the one map: every file has one owner, the import contracts follow from it, allowlists shrink."""

import importlib.util
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("contexts", ROOT / "scripts" / "contexts.py")
assert spec
assert spec.loader
contexts = importlib.util.module_from_spec(spec)
sys.modules["contexts"] = contexts
spec.loader.exec_module(contexts)

pytestmark = [pytest.mark.unit, pytest.mark.timeout(60)]

CONFIG = contexts.load(ROOT)
FILES = contexts.tracked(ROOT)
OWNERS = contexts.owners(CONFIG, FILES)
WORLD = re.compile(
    r"\bsubprocess\.(?:run|Popen|check_output|check_call|call)\b|\btime\.(?:sleep|monotonic)\b|\blaunchctl\b|\bdatetime\.now\("
)
MAX_CONTEXT_EDGES = 8
MAX_HEAD_EDGES = 12


def test_every_mapped_file_has_exactly_one_owner():
    wrong = {path: names for path, names in OWNERS.items() if len(names) != 1}
    assert wrong == {}, "a file belongs to no context or to two; fix the globs in contexts.toml"


def test_every_context_owns_something():
    owned = {name for names in OWNERS.values() for name in names}
    assert sorted(set(CONFIG["context"]) - owned) == []


def test_a_context_names_the_package_it_is_checked_by():
    for name, context in CONFIG["context"].items():
        if "package" in context:
            assert (ROOT / "src" / PurePosixPath(*context["package"].split("."))).exists(), name


def test_test_globs_never_overlap_between_contexts():
    all_tests = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "tests").rglob("*_test.py"))
    seen: dict[str, str] = {}
    for name, context in CONFIG["context"].items():
        for path in all_tests:
            if any(PurePosixPath(path).full_match(g) for g in context.get("tests", [])):
                assert seen.setdefault(path, name) == name, f"{path} is claimed by {seen[path]} and {name}"


def test_the_import_contracts_in_pyproject_are_generated_from_the_map():
    problems = contexts.problems(ROOT)
    assert problems == []


def test_the_contract_allowlists_only_shrink():
    allow = CONFIG["allow"]
    assert len(allow["contexts"]) <= MAX_CONTEXT_EDGES, (
        "a context may not gain an import of another; lower the cap when one goes"
    )
    assert len(allow["heads"]) <= MAX_HEAD_EDGES, "a head may not gain an import of another head"


def _world_touches(path: str) -> int:
    return len(WORLD.findall((ROOT / path).read_text(encoding="utf-8")))


def _adapter_only_files() -> list[str]:
    return sorted(
        path
        for path, names in OWNERS.items()
        if path.endswith(".py")
        and CONFIG["context"][names[0]].get("adapters_only")
        and not PurePosixPath(path).name.startswith("_adapter")
    )


@pytest.mark.parametrize("path", _adapter_only_files())
def test_only_adapters_touch_the_world(path):
    """Processes, sleeps, clocks and launchctl belong behind a port in `_adapter*.py`; the rest is a shrinking list."""
    allowed = CONFIG["allow"]["adapters"].get(path, 0)
    found = _world_touches(path)
    assert found <= allowed, f"{path} touches the world in {found} places, {allowed} allowed: go through a port"
    assert found >= allowed, f"{path} now touches the world in {found} places: lower its entry in contexts.toml"


def test_the_adapter_allowlist_names_files_that_exist_and_are_checked():
    assert sorted(set(CONFIG["allow"]["adapters"]) - set(_adapter_only_files())) == []


def _needs_python(*changed):
    return contexts.python_needed(CONFIG, list(changed))[0]


def test_a_docs_only_or_apple_only_change_selects_no_python_job():
    assert _needs_python("docs/guide/start.md") is False
    assert _needs_python("apple/App/HeinzelApp.swift", "apple/Widgets/Widget.swift") is False
    assert _needs_python("apple/App/HeinzelApp.swift", "docs/adr/0001.md", "mkdocs.yml") is False


def test_everything_else_keeps_the_python_jobs():
    assert _needs_python("src/heinzel/kernel/_kernel.py")
    assert _needs_python("web/src/App.tsx"), "the web app shares the jobs; only Apple and prose skip them"
    assert _needs_python("apple/App/HeinzelApp.swift", "src/heinzel/ports.py")
    assert _needs_python("CHANGELOG.md"), "release checks read the changelog"
    assert _needs_python("README.md")
    assert _needs_python("contexts.toml")
    assert _needs_python("scripts/affected.py")
    assert _needs_python(".github/workflows/ci.yml")
    assert _needs_python("something/nobody/owns.txt")


def test_this_pull_request_keeps_the_complete_run():
    assert _needs_python("src/heinzel/ports.py", "contexts.toml", "web/src/features/runtime/updates/controller.ts")


def test_ci_asks_the_selector_for_pull_requests_only():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "python3 -I scripts/contexts.py select" in workflow
    assert '[ "$EVENT" = pull_request ] && [ "${RELEASE_FULL:-false}" != true ] && [ -z "${BATCH_BASE:-}" ]' in workflow


def _upgrade(*changed):
    return contexts.upgrade_needed(CONFIG, list(changed))[0]


def test_update_lifecycle_and_kernel_changes_select_the_upgrade_lane_and_macos():
    assert _upgrade("src/heinzel/modules/system/updater/_schedule.py")
    assert _upgrade("src/heinzel/heads/web/_updates.py")
    assert _upgrade("src/heinzel/modules/system/lifecycle/_handover.py")
    assert _upgrade("src/heinzel/_lifecycle_control.py")
    assert _upgrade("src/heinzel/kernel/_kernel.py")
    for path in (
        "src/heinzel/modules/system/daemon/_package_handover_recovery.py",
        "src/heinzel/modules/system/daemon/_handover_files.py",
        "src/heinzel/modules/system/daemon/_handover_request.py",
        "src/heinzel/modules/system/daemon/_legacy_retirement.py",
        "src/heinzel/heads/cli/_update.py",
        "src/heinzel/heads/web/_auto_update.py",
        "src/heinzel/heads/web/_lifecycle.py",
        "src/heinzel/heads/web/_supervision.py",
        "src/heinzel/heads/daemon_recovery.py",
        "src/heinzel/kernel/_daemon_recovery.py",
    ):
        assert _upgrade(path), path
    assert _upgrade("src/heinzel/ports.py"), "the spec context owns the public heinzel names"
    assert _upgrade("tests/unit/runtime/updater/plan_test.py"), "a test of an upgrade context"
    assert _upgrade("tests/unit/kernel/lets_test.py")
    for path in ("uv.lock", "pyproject.toml", ".github/workflows/ci.yml", "scripts/acceptance/scenario.py"):
        assert _upgrade("docs/guide/start.md", path), path
    assert _upgrade(), "an empty change fails closed"


def test_no_update_lifecycle_handover_or_daemon_source_is_left_out_of_the_upgrade_lane():
    sources = subprocess.run(
        ["git", "ls-files", "src"], cwd=ROOT, capture_output=True, text=True, check=True, timeout=30
    ).stdout.split()
    named = [p for p in sources if re.search(r"update|lifecycle|handover|daemon", p) and p.endswith(".py")]
    assert named
    assert [p for p in named if not _upgrade(p)] == []


def test_other_contexts_skip_the_upgrade_lane_and_macos_on_a_pull_request():
    assert not _upgrade("src/heinzel/modules/ui/browser/_page.py")
    assert not _upgrade("web/src/App.tsx", "docs/guide/start.md")
    assert not _upgrade("apple/App/HeinzelApp.swift")
    assert not _upgrade("tests/integration/chat_test.py")


def test_ci_asks_the_upgrade_selector_and_fails_closed():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "python3 -I scripts/contexts.py upgrade" in workflow
    assert "upgrade=true" in workflow  # no answer from the selector keeps the lane
