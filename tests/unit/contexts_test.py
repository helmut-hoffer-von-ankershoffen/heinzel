"""`contexts.toml` is the one map: every file has one owner, the import contracts follow from it, allowlists shrink."""

import importlib.util
import re
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
MAX_CONTEXT_EDGES = 9
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
