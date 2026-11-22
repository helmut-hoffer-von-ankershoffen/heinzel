"""Cross-version entry names stay stable: a module move must break this fast test, not only the macOS e2e.

`tests/fixtures/legacy_recovery_manager.py` is installed next to published Heinzel wheels and imported by their
interpreters (0.9.14, then 0.9.15, then the candidate). Product code crosses versions the same way: an older
installed tool or desktop app starts the newer one by module name. Those names are frozen here, as read from
the published 0.9.14 and 0.9.15 wheels; the current tree must still provide each one.
"""

import ast
import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/legacy_recovery_manager.py"

# Modules present in both published wheels (heinzel 0.9.14 and 0.9.15), checked against the wheel contents.
PUBLISHED = frozenset(
    {
        "heinzel",
        "heinzel.heads.cli",
        "heinzel.kernel",
        "heinzel.modules.system._announced",
        "heinzel.modules.system._update",
        "heinzel.modules.system.daemon",
        "heinzel.modules.system.daemon._service",
        "heinzel.modules.system.daemon._units",
    }
)

# Entries an older installed Heinzel or desktop app runs in the newer one (`python -m NAME`, launcher scripts).
PRODUCT_ENTRIES = ("heinzel.__main__", "heinzel._lifecycle_control", "_heinzel_launcher")


def _heinzel_imports(nodes):
    names = set()
    for node in nodes:
        for child in ast.walk(node):
            if isinstance(child, ast.ImportFrom) and child.module and child.level == 0:
                names.add(child.module)
            elif isinstance(child, ast.Import):
                names.update(alias.name for alias in child.names)
    return {name for name in names if name == "heinzel" or name.startswith("heinzel.")}


def _branches():
    """Split the fixture into code every interpreter runs, the 0.9.14-only branch and the candidate-only branch."""
    tree = ast.parse(FIXTURE.read_text())
    dispatch = next(
        node for node in tree.body if isinstance(node, ast.If) and "automatic-015" in ast.unparse(node.test)
    )
    recovery = dispatch.orelse[0]
    assert isinstance(recovery, ast.If)
    assert "recovery" in ast.unparse(recovery.test)
    shared = [node for node in tree.body if node is not dispatch] + list(recovery.orelse)
    return _heinzel_imports(shared), _heinzel_imports(dispatch.body), _heinzel_imports(recovery.body)


def test_fixture_code_run_by_published_interpreters_names_only_published_modules():
    shared, published_0914, _ = _branches()
    assert shared, "the shared fixture code imports Heinzel"
    assert published_0914 >= {"heinzel.modules.system._announced", "heinzel.modules.system._update"}
    assert (shared | published_0914) - PUBLISHED == set()


def test_fixture_code_run_by_the_candidate_resolves_in_the_current_tree():
    shared, _, candidate = _branches()
    missing = {name for name in shared | candidate if importlib.util.find_spec(name) is None}
    assert missing == set()


@pytest.mark.parametrize("name", PRODUCT_ENTRIES)
def test_cross_version_product_entries_keep_their_module_names(name):
    assert importlib.util.find_spec(name) is not None, name
