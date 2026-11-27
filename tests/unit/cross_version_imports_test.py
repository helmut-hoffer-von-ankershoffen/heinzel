"""Cross-version imports stay working: a move or a new name must break this fast test, not only the macOS e2e.

Two kinds of code run across a version boundary:

- `hz system update` replaces the installed files while the published process keeps its booted modules in
  memory. Each import that process makes later, inside a function, loads a file of the new tree, and that file
  takes names from the old modules in memory. The manifests in `tests/fixtures/published_resident/` record, per
  published version (the legacy fixture's 0.9.14 and 0.9.15, and the 0.9.52 to 0.9.54 users update from), the
  modules in memory after boot with their runtime names, the classes each of them defines (from 0.9.52 on), and
  the imports made inside functions (`record.py` says how to record one).
- A class that moved keeps one identity: a published process that holds it from the old file must never load a
  second definition from the new one, and in the current tree the old path re-exports the very same object.
- `tests/fixtures/legacy_recovery_manager.py` is installed next to published wheels and imported by their
  interpreters (0.9.14, then 0.9.15, then the candidate).
"""

import ast
import gzip
import importlib
import importlib.util
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

# Each test parses the whole source tree, which takes seconds on a loaded runner: the integration limit, scaled by CI.
pytestmark = [pytest.mark.unit, pytest.mark.timeout(15)]

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "src"
MANIFESTS = REPO / "tests/fixtures/published_resident"
FIXTURE = REPO / "tests/fixtures/legacy_recovery_manager.py"
# The legacy fixture's versions and what users update from.
VERSIONS = ("0.9.14", "0.9.15", "0.9.52", "0.9.53", "0.9.54")
FIXTURE_VERSIONS = ("0.9.14", "0.9.15")
DEFINING = ("0.9.52", "0.9.53", "0.9.54")  # recorded with their `define` lines
# Packages the kernel move (KERNEL-HARNESS-056) fills: only facades import them, so a published process never
# loads them after a swap, until the facades go with the oldest supported version.
FACADE_ONLY = ("heinzel.spec", "heinzel.services")

# Imports inside functions of a published version that already failed after a swap to a newer tree before the
# check existed (version skew of those heads and renderers, not of the update path). This list may only shrink.
KNOWN_BROKEN: dict[str, set[str]] = {
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
    "0.9.52": set(),
    "0.9.53": set(),
    "0.9.54": set(),
}

# Files #895 (published in 0.9.54) moved into kernel subpackages without a file at the old path: after a swap, a
# 0.9.52 or 0.9.53 process holds their classes from the old file and loads a second copy from the new one. This
# list may only shrink; nothing may be added to it.
_MOVED_BY_895 = {
    "heinzel.kernel.ai._agent_writes",
    "heinzel.kernel.ai._agents",
    "heinzel.kernel.ai._ai_models",
    "heinzel.kernel.session._chat",
    "heinzel.kernel.session._chat_services",
    "heinzel.kernel.session._chat_tools",
    "heinzel.kernel.session._presence",
}
KNOWN_SPLIT: dict[str, set[str]] = {"0.9.52": _MOVED_BY_895, "0.9.53": _MOVED_BY_895, "0.9.54": set()}


@dataclass
class Published:
    resident: dict[str, set[str]] = field(default_factory=dict[str, set[str]])
    defines: dict[str, set[str]] = field(default_factory=dict[str, set[str]])
    lazy: list[tuple[str, str, list[str]]] = field(default_factory=list[tuple[str, str, list[str]]])

    def has(self, module: str, name: str) -> bool:
        return name in self.resident.get(module, set()) or f"{module}.{name}" in self.resident


def published(version: str) -> Published:
    record = Published()
    for line in gzip.decompress((MANIFESTS / f"{version}.txt.gz").read_bytes()).decode().splitlines():
        kind, *rest = line.split(" ")
        if kind == "resident":
            record.resident[rest[0]] = set(rest[1:])
        elif kind == "define":
            record.defines[rest[0]] = set(rest[1:])
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

    def hook_imports(self, module: str) -> Iterator[tuple[str, list[str]]]:
        """What the module's PEP 562 `__getattr__` imports when it serves a name on first use."""
        tree, package = self.modules[module]
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "__getattr__":
                for child in ast.walk(node):
                    if isinstance(child, ast.ImportFrom):
                        target = child.module if child.level == 0 else _relative(module, package, child)
                        if target:
                            yield target, [alias.name for alias in child.names]

    def classes(self, module: str) -> set[str]:
        """The classes the module defines itself, at the top level."""
        tree, _ = self.modules[module]
        return {node.name for node in tree.body if isinstance(node, ast.ClassDef)}

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
    return _swap(old, tree)[0]


def swap_fresh_loads(old: Published, tree: Tree) -> dict[str, set[str]]:
    """Per import a published process makes after a swap: the files of the new tree it loads fresh."""
    return _swap(old, tree)[1]


def _swap(old: Published, tree: Tree) -> tuple[dict[str, list[str]], dict[str, set[str]]]:
    problems: dict[str, list[str]] = {}
    loaded: dict[str, set[str]] = {}
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
        seen = loaded.setdefault(target, set())
        while todo:
            module = todo.pop()
            if module in seen or module in old.resident:
                continue
            seen.add(module)
            for imported, wanted in tree.top_level_imports(module):
                if imported in old.resident:
                    missing = [n for n in wanted if not old.has(imported, n) and f"{imported}.{n}" not in tree.modules]
                    if missing and "__getattr__" in old.resident[imported] and imported in tree.modules:
                        # the resident hook serves them on first use and loads its files from the new tree
                        for hooked, served in tree.hook_imports(imported):
                            todo += _loads(tree, hooked, served)
                        missing = [n for n in missing if not tree.defines(imported, n)]
                    found += [f"{module} needs {imported}.{n}" for n in missing]  # a new file loads fresh
                    todo += [f"{imported}.{n}" for n in wanted if f"{imported}.{n}" in tree.modules]
                todo += _loads(tree, imported, wanted)
    found_problems = {target: sorted(set(found)) for target, found in problems.items() if found}
    return found_problems, {target: fresh for target, fresh in loaded.items() if fresh}


def moved_classes(old: Published, tree: Tree) -> dict[str, str]:
    """`module.Class` a published process defines in a file that no longer defines it itself (moved or gone)."""
    return {
        f"{module}.{name}": module
        for module, names in old.defines.items()
        for name in names
        if module not in tree.modules or name not in tree.classes(module)
    }


def _moved_to(old_module: str, name: str, new_module: str, tree: Tree) -> bool:
    """A move keeps the file name, or the old file re-exports the class from its new file."""
    if old_module.rpartition(".")[2] == new_module.rpartition(".")[2]:
        return True
    return old_module in tree.modules and any(
        target == new_module and name in names for target, names in tree.top_level_imports(old_module)
    )


def swap_identity_problems(old: Published, tree: Tree) -> dict[str, list[str]]:
    """Per import after a swap: fresh files that define again a class the process holds, or that only facades load."""
    moved: dict[str, set[str]] = {}
    for qualified, module in moved_classes(old, tree).items():
        moved.setdefault(qualified.rpartition(".")[2], set()).add(module)
    problems: dict[str, list[str]] = {}
    for target, fresh in swap_fresh_loads(old, tree).items():
        found = [f"{module} is facade-only and loads fresh" for module in fresh if module.startswith(FACADE_ONLY)]
        found += [
            f"{module} defines {name} again; the process holds it from {held}"
            for module in fresh
            for name in tree.classes(module)
            for held in moved.get(name, set())
            if _moved_to(held, name, module, tree)
        ]
        if found:
            problems[target] = sorted(set(found))
    return problems


@pytest.mark.parametrize("version", VERSIONS)
def test_a_published_process_can_finish_its_update_on_the_current_tree(version: str) -> None:
    broken = swap_problems(published(version), Tree(SOURCE))
    new = {target: found for target, found in broken.items() if target not in KNOWN_BROKEN[version]}
    assert new == {}, "keep these imports light or keep the names; see heinzel.modules.system.updater"
    assert KNOWN_BROKEN[version] <= set(broken), "remove the entries that work now from KNOWN_BROKEN"


def test_recent_evidence_loads_without_any_resident_kernel_exports() -> None:
    """A synthetic resident package needs no exports to load the fresh child.

    Both ancestor packages are already loaded, as Python requires. This minimal
    closure models no published release; historical manifests remain separate.
    """
    old = Published(resident={"heinzel": set(), "heinzel.kernel": set()})
    target = "heinzel.kernel.recent_clients"
    old.lazy = [("probe", target, ["RecentClient", "RecentUse", "recent_clients"])]
    tree = Tree(SOURCE)
    assert swap_problems(old, tree) == {}
    assert swap_fresh_loads(old, tree) == {target: {target}}

    # Pin the guard: an accidental dependency on a new kernel export must fail.
    source, package = tree.modules[target]
    tree.modules[target] = (ast.parse("from heinzel.kernel import RecentUse\n" + ast.unparse(source)), package)
    assert swap_problems(old, tree) == {target: [f"{target} needs heinzel.kernel.RecentUse"]}


@pytest.mark.parametrize("version", DEFINING)
def test_a_published_process_never_loads_a_second_definition_of_a_class_it_holds(version: str) -> None:
    found = {line for lines in swap_identity_problems(published(version), Tree(SOURCE)).values() for line in lines}
    known = {line for line in found if line.split(" ")[0] in KNOWN_SPLIT[version]}
    assert sorted(found - known) == [], "keep the old file as a re-export and import the class from there"
    assert {line.split(" ")[0] for line in known} == KNOWN_SPLIT[version], "remove what is fixed from KNOWN_SPLIT"


@pytest.mark.parametrize("version", DEFINING)
def test_a_moved_class_is_the_same_object_at_its_old_path(version: str) -> None:
    """The current tree: every class a published version defined is, where its old file still exists, one object."""
    tree = Tree(SOURCE)
    split: list[str] = []
    for qualified, module in moved_classes(published(version), tree).items():
        name = qualified.rpartition(".")[2]
        if module not in tree.modules or not tree.defines(module, name):
            continue  # gone from the old path: the swap test above decides whether a published process needs it
        held = getattr(importlib.import_module(module), name)
        home = importlib.import_module(held.__module__)
        if getattr(home, held.__qualname__, None) is not held:
            split.append(f"{qualified} is not {held.__module__}.{held.__qualname__}")
    assert split == []


@pytest.mark.parametrize("version", ["0.9.54"])
def test_the_kernel_entry_keeps_every_name_the_published_updater_holds(version: str) -> None:
    """`heinzel.kernel` stays a re-export of all its published names while the code behind it moves."""
    import heinzel.kernel

    names = published(version).resident["heinzel.kernel"] - set(dir(heinzel.kernel))
    assert sorted(n for n in names if importlib.util.find_spec(f"heinzel.kernel.{n}") is None) == []


def test_a_resident_lazy_hook_serves_only_the_names_the_current_package_has() -> None:
    """0.9.54's updater package answers `Activity` through its PEP 562 hook; a name the hook lost is a problem."""
    tree, old, updater = Tree(SOURCE), published("0.9.54"), "heinzel.modules.system.updater"
    # The web head now asks `updater.api`, a file 0.9.54 never held; pin the model with a file that asks the package.
    asker = "heinzel.heads.web._auto_update"
    tree.modules[asker] = (ast.parse(f"from {updater} import Activity"), False)
    needs = f"{asker} needs {updater}.Activity"
    assert needs not in swap_problems(old, tree).get("heinzel.heads", [])
    source = ast.unparse(tree.modules[updater][0]).replace("Activity, ", "").replace("'Activity', ", "")
    tree.modules[updater] = (ast.parse(source), True)
    assert needs in swap_problems(old, tree)["heinzel.heads"]


def test_every_published_version_since_052_records_its_classes() -> None:
    for version in DEFINING:
        assert published(version).defines, f"record {version} again with record.py"


def test_the_identity_check_catches_a_second_definition() -> None:
    """Pin the model: a class moved to a fresh file that a published process loads after the swap is a split."""
    tree = Tree(SOURCE)
    old = published("0.9.54")
    module, names = next((m, n) for m, n in sorted(old.defines.items()) if m in tree.modules and m in old.resident)
    name = min(names)
    renderer = "heinzel.modules.system._render"
    copy = "heinzel.spec._moved"
    tree.modules[module] = (ast.parse(f"from {copy} import {name}"), tree.modules[module][1])
    tree.modules[copy] = (ast.parse(f"class {name}: ..."), False)
    tree.modules[renderer] = (ast.parse(f"from {copy} import {name}"), False)
    found = swap_identity_problems(old, tree)[renderer]
    assert f"{copy} defines {name} again; the process holds it from {module}" in found
    assert f"{copy} is facade-only and loads fresh" in found


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
    for version in FIXTURE_VERSIONS:
        old = published(version)
        missing = sorted(f"{m}.{n}" for m, n in shared if not old.has(m, n))
        assert missing == [], f"published {version} lacks what the shared fixture code imports"
    old = published("0.9.14")
    assert sorted(f"{m}.{n}" for m, n in only_014 if not old.has(m, n)) == []
    assert sorted(f"{m}.{n}" for m, n in shared | candidate if not _current_has(m, n)) == []


ENTRIES = [
    "heinzel.__main__",
    "heinzel._lifecycle_control",
    "_heinzel_launcher",
    "heinzel.kernel",  # the kernel entry: its names are re-exports, its path stays (KERNEL-HARNESS-056)
    "heinzel.kernel._kernel",
    "heinzel.kernel._boot",
    "heinzel.heads.cli",
    "heinzel.testing",
]


@pytest.mark.parametrize("name", ENTRIES)
def test_cross_version_product_entries_keep_their_module_names(name: str) -> None:
    """Older installed tools and desktop apps start the newer one by these module names."""
    assert importlib.util.find_spec(name) is not None, name


def test_the_kept_mcp_tokens_path_answers_with_the_client_context() -> None:
    """`hz system serve` up to 0.9.53 imports this path inside a function, also after an in-place update."""
    from heinzel.heads._mcp_tokens import tokens_for_user
    from heinzel.modules.system.client import tokens_for_user as current

    assert tokens_for_user is current


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("heinzel.modules.system.updater._phases", "heinzel._phases"),
        ("heinzel.heads.cli._serve", "heinzel.heads._compose"),
    ],
)
def test_a_moved_module_keeps_its_old_path_with_the_same_objects(old: str, new: str) -> None:
    """No importable path disappears in 0.9.55: the old module re-exports every name of the new one, as is."""
    before, after = importlib.import_module(old), importlib.import_module(new)
    assert before.__all__, old
    assert {name: getattr(before, name) is getattr(after, name) for name in before.__all__} == dict.fromkeys(
        before.__all__, True
    )


def test_the_rest_head_keeps_the_authority_names_the_kit_now_owns() -> None:
    """The Host and Origin policy moved to the head kit; `heinzel.heads.api` still answers with the same objects."""
    from heinzel.heads import _kit, api

    names = ("API_HOST_HELP", "DEFAULT_PORTS", "FORBIDDEN", "LOOPBACK", "WEB_HOST_HELP", "refusal", "request_refusal")
    for name in names:
        assert getattr(api, name) is getattr(_kit, name), name
