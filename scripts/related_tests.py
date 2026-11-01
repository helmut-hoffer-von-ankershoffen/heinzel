# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Pick the unit tests a change can affect, from the import graph, for the pre-push hook.

A test is related when it is itself changed, when a changed file is one of the modules it imports directly or through
other modules (`from heinzel.a import b` follows `b` to the module that defines it, not to everything `heinzel`
re-exports), when it is named after a changed module (`tests/unit/<module>_test.py`), or, for a changed script, when
it mentions the script by name (tests load scripts by path). Only `tests/unit` is selected: that is what the fast gate
runs. `None` means "cannot tell, run everything": a changed
file that is neither Python under `src/heinzel`, `tests` or `scripts` nor a document (configuration, lockfile,
`conftest.py`, packaged data, the template). CI runs the whole suite either way.
"""

import ast
import functools
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_ROOTS = {"heinzel": "src/heinzel", "tests": "tests"}
DOCUMENT = re.compile(r"(?:.*\.md|docs/.*|mkdocs\.yml)")
MAPPABLE = re.compile(r"(?:src/heinzel/.*|tests/.*|scripts/[^/]*)\.py")


def module_file(dotted: str) -> str | None:
    """The file of a dotted module path (`heinzel.kernel.x`), or None when it is not ours."""
    top, _, rest = dotted.partition(".")
    base = PYTHON_ROOTS.get(top)
    if base is None:
        return None
    path = Path(base, *rest.split(".")) if rest else Path(base)
    for candidate in (path.with_suffix(".py"), path / "__init__.py"):
        if (ROOT / candidate).is_file():
            return candidate.as_posix()
    return None


def statements(tree: ast.AST) -> list[ast.stmt]:
    """Every statement at any depth, without descending into expressions: imports are statements."""
    found: list[ast.stmt] = []
    stack: list[ast.AST] = [tree]
    while stack:
        for child in ast.iter_child_nodes(stack.pop()):
            if isinstance(child, ast.stmt):
                found.append(child)
                stack.append(child)
            elif isinstance(child, ast.excepthandler | ast.match_case):
                stack.append(child)
    return found


@functools.cache
def graph() -> "Graph":
    """One graph per process: parsing the tree is the cost."""
    return Graph()


class Graph:
    """Who imports whom among the project's own files, resolved to the defining module."""

    def __init__(self) -> None:
        """Parse every Python file once; the graph is built from these trees."""
        self.files = sorted(
            path.relative_to(ROOT).as_posix()
            for base in (*PYTHON_ROOTS.values(), "scripts")
            for path in (ROOT / base).rglob("*.py")
            if "__pycache__" not in path.parts
        )
        self.trees: dict[str, ast.Module] = {}
        for file in self.files:
            try:
                self.trees[file] = ast.parse((ROOT / file).read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                self.trees[file] = ast.parse("")
        self.imports = {file: self._imports(file) for file in self.files}

    def package(self, file: str) -> str:
        """The dotted package a file's relative imports start from."""
        parts = Path(file).with_suffix("").parts
        top = next((name for name, base in PYTHON_ROOTS.items() if file.startswith(base + "/")), None)
        if top is None:
            return ""
        dotted = [top, *parts[len(Path(PYTHON_ROOTS[top]).parts) :]]
        return ".".join(dotted if file.endswith("__init__.py") else dotted[:-1]).removesuffix(".__init__")

    def _imports(self, file: str) -> set[str]:
        found: set[str] = set()
        for node in statements(self.trees[file]):
            if isinstance(node, ast.Import):
                found.update(filter(None, (module_file(alias.name) for alias in node.names)))
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parent = self.package(file).split(".")
                    parent = parent[: len(parent) - node.level + 1]
                    base = ".".join([*parent, *([node.module] if node.module else [])])
                for alias in node.names:
                    target = self.resolve(base, alias.name, set())
                    if target:
                        found.add(target)
        found.discard(file)
        return found

    def resolve(self, package: str, name: str, seen: set[str]) -> str | None:
        """The file that defines `name` imported from `package`: a submodule, a re-export's source, else the package."""
        sub = module_file(f"{package}.{name}")
        if sub:
            return sub
        init = module_file(package)
        if init is None or init in seen:
            return init
        seen.add(init)
        if init.endswith("__init__.py") and init in self.trees:
            for node in self.trees[init].body:
                if isinstance(node, ast.ImportFrom) and any((a.asname or a.name) == name for a in node.names):
                    base = node.module or ""
                    if node.level:
                        parent = self.package(init).split(".")
                        parent = parent[: len(parent) - node.level + 1]
                        base = ".".join([*parent, *([node.module] if node.module else [])])
                    original = next(a.name for a in node.names if (a.asname or a.name) == name)
                    return self.resolve(base, original, seen) or init
        return init

    def dependents(self, changed: set[str]) -> set[str]:
        """Every file that reaches a changed file through imports."""
        reverse: dict[str, set[str]] = {}
        for file, targets in self.imports.items():
            for target in targets:
                reverse.setdefault(target, set()).add(file)
        reached, queue = set(changed), list(changed)
        while queue:
            for importer in reverse.get(queue.pop(), ()):
                if importer not in reached:
                    reached.add(importer)
                    queue.append(importer)
        return reached


def related_tests(changed: list[str]) -> list[str] | None:
    """Test files for the changed paths, or None when only the whole suite is a safe answer."""
    code = [path for path in changed if MAPPABLE.fullmatch(path)]
    others = [path for path in changed if path not in code and not DOCUMENT.fullmatch(path)]
    if others or any(path.endswith("conftest.py") for path in code):
        return None
    tree = graph()
    existing = [path for path in code if path in tree.trees]  # a deleted file leaves its importers to the graph's
    reached = tree.dependents(set(existing))  # names, which no longer resolve: its tests simply fail in CI
    tests = {path for path in reached if re.fullmatch(r"tests/unit/.*_test\.py", path)}  # the hook runs unit tests
    for path in code:
        stem = Path(path).stem
        if path.startswith("scripts/"):
            mention = re.compile(rf"(?<![\w]){re.escape(stem)}(?![\w])")
            tests |= {
                t
                for t in tree.files
                if t.startswith("tests/unit/") and t.endswith("_test.py") and mention.search(_text(t))
            }
        elif path.startswith("src/heinzel/"):
            tests |= {t for t in tree.files if t.endswith(f"/{stem}_test.py") and t.startswith("tests/unit/")}
    return sorted(test for test in tests if (ROOT / test).is_file())


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    result = related_tests(sys.argv[1:])
    print("ALL" if result is None else "\n".join(result))
