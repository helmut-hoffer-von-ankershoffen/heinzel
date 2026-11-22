"""Cross-version imports stay working: a move or a new name must break this fast test, not only the macOS e2e.

Two kinds of code run across a version boundary:

- `hz system update` replaces the installed files while the published process keeps its booted modules in
  memory. Each import that process makes later, inside a function, loads a file of the new tree, and that file
  takes names from the old modules in memory. The manifests in `tests/fixtures/published_resident/` record, per
  published version, the modules in memory after boot with their runtime names, and the imports made inside
  functions (`record.py` says how to record one).
- `tests/fixtures/legacy_recovery_manager.py` is installed next to published wheels and imported by their
  interpreters (0.9.14, then 0.9.15, then the candidate).
"""

import ast
import importlib
import importlib.util
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "src"
MANIFESTS = REPO / "tests/fixtures/published_resident"
FIXTURE = REPO / "tests/fixtures/legacy_recovery_manager.py"
VERSIONS = ("0.9.14", "0.9.15")

# Imports inside functions of a published version that already failed after a swap to a newer tree before the
# check existed (version skew of those heads and renderers, not of the update path). This list may only shrink.
KNOWN_BROKEN = {
    "0.9.14": {"heinzel.heads"},
    "0.9.15": {
        "heinzel.heads",
        "heinzel.heads.cli._discovery",
        "heinzel.heads.cli._join",
        "heinzel.heads.cli._setup",
        "heinzel.modules.heinzel._interactive_turn",
        "heinzel.modules.system.claude._render",
        "heinzel.modules.system.plugin._render",
    },
}


@dataclass
class Published:
    resident: dict[str, set[str]] = field(default_factory=dict)
    lazy: list[tuple[str, str, list[str]]] = field(default_factory=list)

    def has(self, module: str, name: str) -> bool:
        return name in self.resident.get(module, set()) or f"{module}.{name}" in self.resident


def published(version: str) -> Published:
    record = Published()
    for line in (MANIFESTS / f"{version}.txt").read_text().splitlines():
        kind, *rest = line.split(" ")
        if kind == "resident":
            record.resident[rest[0]] = set(rest[1:])
        elif kind == "lazy":
            record.lazy.append((rest[0], rest[1], rest[2].split(",") if len(rest) > 2 else []))
    return record


class Tree:
    """The current source tree, read without importing it."""

    def __init__(self, root: Path) -> None:
        self.modules: dict[str, tuple[ast.Module, bool]] = {}
        for path in (root / "heinzel").rglob("*.py"):
            parts = list(path.relative_to(root).with_suffix("").parts)
            package = parts[-1] == "__init__"
            self.modules[".".join(parts[:-1] if package else parts)] = (ast.parse(path.read_text()), package)

    def defines(self, module: str, name: str) -> bool:
        if f"{module}.{name}" in self.modules:
            return True
        tree, _ = self.modules[module]
        return name in {
            *(n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)),
            *(n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))),
            *(a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names),
        }

    def top_level_imports(self, module: str) -> Iterator[tuple[str, list[str]]]:
        """What executing the module imports at once (outside functions and TYPE_CHECKING)."""
        tree, package = self.modules[module]

        def visit(nodes: list[ast.stmt]) -> Iterator[tuple[str, list[str]]]:
            for node in nodes:
                if isinstance(node, ast.ImportFrom):
                    target = node.module if node.level == 0 else _relative(module, package, node)
                    if target and target.split(".")[0] == "heinzel":
                        yield target, [alias.name for alias in node.names]
                elif isinstance(node, ast.Import):
                    yield from ((alias.name, []) for alias in node.names if alias.name.split(".")[0] == "heinzel")
                elif isinstance(node, ast.If):
                    yield from visit(
                        node.orelse if "TYPE_CHECKING" in ast.unparse(node.test) else node.body + node.orelse
                    )
                elif isinstance(node, ast.Try):
                    if not any(_guards_import(handler) for handler in node.handlers):  # a guarded import may fail
                        yield from visit(node.body)
                    yield from visit([s for handler in node.handlers for s in handler.body] + node.orelse)
                elif isinstance(node, ast.With):
                    yield from visit(node.body)

        yield from visit(tree.body)


def _guards_import(handler: ast.ExceptHandler) -> bool:
    caught = handler.type
    if caught is None:
        return True
    names = caught.elts if isinstance(caught, ast.Tuple) else [caught]
    return any(ast.unparse(n) in {"ImportError", "ModuleNotFoundError", "Exception"} for n in names)


def _relative(module: str, package: bool, node: ast.ImportFrom) -> str:
    base = module.split(".") if package else module.split(".")[:-1]
    base = base[: len(base) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def _loads(tree: Tree, target: str, names: list[str]) -> list[str]:
    parts = target.split(".")
    chain = [".".join(parts[:i]) for i in range(1, len(parts) + 1)] + [f"{target}.{n}" for n in names]
    return [name for name in chain if name in tree.modules]


def swap_problems(old: Published, tree: Tree) -> dict[str, list[str]]:
    """Per import a published process makes after a swap: what the new files ask of its modules in memory."""
    problems: dict[str, list[str]] = {}
    for _importer, target, names in old.lazy:
        found = problems.setdefault(target, [])
        if target in old.resident:  # loaded already; only its submodules not yet in memory come from the new tree
            fresh = [name for name in names if f"{target}.{name}" not in old.resident]
            found += [
                f"{target}.{n} is gone" for n in fresh if not old.has(target, n) and f"{target}.{n}" not in tree.modules
            ]
            todo = [f"{target}.{name}" for name in fresh if f"{target}.{name}" in tree.modules]
        elif target not in tree.modules:
            found.append(f"{target} is gone")
            continue
        else:
            found += [f"{target}.{name} is gone" for name in names if not tree.defines(target, name)]
            todo = _loads(tree, target, names)
        seen: set[str] = set()
        while todo:
            module = todo.pop()
            if module in seen or module in old.resident:
                continue
            seen.add(module)
            for imported, wanted in tree.top_level_imports(module):
                if imported in old.resident:
                    found += [f"{module} needs {imported}.{n}" for n in wanted if not old.has(imported, n)]
                    todo += [f"{imported}.{n}" for n in wanted if f"{imported}.{n}" in tree.modules]
                todo += _loads(tree, imported, wanted)
    return {target: sorted(set(found)) for target, found in problems.items() if found}


@pytest.mark.parametrize("version", VERSIONS)
def test_a_published_process_can_finish_its_update_on_the_current_tree(version: str) -> None:
    broken = swap_problems(published(version), Tree(SOURCE))
    new = {target: found for target, found in broken.items() if target not in KNOWN_BROKEN[version]}
    assert new == {}, "keep these imports light or keep the names; see heinzel.modules.system.updater"
    assert KNOWN_BROKEN[version] <= set(broken), "remove the entries that work now from KNOWN_BROKEN"


def test_the_update_result_renders_after_the_published_015_updater_swapped_the_files() -> None:
    """The exact failure of #895: `hz system update` of 0.9.15 renders its result with the new `_render`."""
    broken = swap_problems(published("0.9.15"), Tree(SOURCE))
    assert "heinzel.modules.system._render" not in broken


def test_the_check_catches_the_895_regression() -> None:
    """Pin the model itself: an eager schedule import in the updater package must show up as a problem."""
    tree = Tree(SOURCE)
    updater = "heinzel.modules.system.updater"
    eager = "from heinzel.modules.system.updater._schedule import Activity, AutoUpdateStatus, Schedule, StateFiles"
    tree.modules[updater] = (ast.parse(eager), True)
    found = swap_problems(published("0.9.15"), tree)["heinzel.modules.system._render"]
    assert "heinzel.modules.system.updater._schedule needs heinzel.kernel.PluginNews" in found


def _fixture_imports(nodes: list[ast.stmt]) -> set[tuple[str, str]]:
    """(module, name) pairs, including attributes read through an imported module alias (`daemon.X`)."""
    pairs: set[tuple[str, str]] = set()
    aliases: dict[str, str] = {}
    for node in nodes:
        for child in ast.walk(node):
            if isinstance(child, ast.ImportFrom) and child.module and child.module.split(".")[0] == "heinzel":
                for alias in child.names:
                    pairs.add((child.module, alias.name))
                    aliases[alias.asname or alias.name] = f"{child.module}.{alias.name}"
    for node in nodes:
        for child in ast.walk(node):
            if isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name) and child.value.id in aliases:
                pairs.add((aliases[child.value.id], child.attr))
    return pairs


def _fixture_branches() -> tuple[set[tuple[str, str]], set[tuple[str, str]], set[tuple[str, str]]]:
    """The code every interpreter runs, the branch only published 0.9.14 runs, the branch only the candidate runs."""
    tree = ast.parse(FIXTURE.read_text())
    dispatch = next(n for n in tree.body if isinstance(n, ast.If) and "automatic-015" in ast.unparse(n.test))
    recovery = dispatch.orelse[0]
    assert isinstance(recovery, ast.If)
    assert "recovery" in ast.unparse(recovery.test)
    shared = [n for n in tree.body if n is not dispatch] + list(recovery.orelse)
    return _fixture_imports(shared), _fixture_imports(dispatch.body), _fixture_imports(recovery.body)


def _current_has(module: str, name: str) -> bool:
    imported = importlib.import_module(module)
    return hasattr(imported, name) or importlib.util.find_spec(f"{module}.{name}") is not None


def test_the_legacy_fixture_names_only_what_each_interpreter_it_runs_in_has() -> None:
    shared, only_014, candidate = _fixture_branches()
    assert ("heinzel.modules.system._update", "UpdateService") in only_014
    assert ("heinzel.modules.system.daemon._service", "DaemonService") in shared
    for version in VERSIONS:
        old = published(version)
        missing = sorted(f"{m}.{n}" for m, n in shared if not old.has(m, n))
        assert missing == [], f"published {version} lacks what the shared fixture code imports"
    old = published("0.9.14")
    assert sorted(f"{m}.{n}" for m, n in only_014 if not old.has(m, n)) == []
    assert sorted(f"{m}.{n}" for m, n in shared | candidate if not _current_has(m, n)) == []


@pytest.mark.parametrize("name", ["heinzel.__main__", "heinzel._lifecycle_control", "_heinzel_launcher"])
def test_cross_version_product_entries_keep_their_module_names(name: str) -> None:
    """Older installed tools and desktop apps start the newer one by these module names."""
    assert importlib.util.find_spec(name) is not None, name
