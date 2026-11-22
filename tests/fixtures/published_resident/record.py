"""Record what a published Heinzel holds in memory when it replaces its own files, for the cross-version test.

`hz system update` swaps the installed files while the old process keeps running with its booted kernel. Every
later import that process makes loads the new file, which then takes names from the old modules in memory. This
script runs inside a published interpreter, standard library only, with its own HOME, and writes one manifest:

    resident MODULE NAME...        a module in memory after the CLI boot, with its runtime names
    define MODULE CLASS...         the classes a resident module defines itself (`__module__` is the module)
    lazy IMPORTER TARGET NAME,...  an import the published code makes inside a function, so after a swap

Record a published version (never against a real profile):

    HOME="$(mktemp -d)" uv run --no-project --isolated --with heinzel==VERSION python -I \
        tests/fixtures/published_resident/record.py | gzip -n -9 > tests/fixtures/published_resident/VERSION.txt.gz

Stored compressed: the names are a published version's own, and text scans of the tests must not read them.
"""

import ast
import sys
from collections.abc import Iterator
from pathlib import Path


def _heinzel(name: str | None) -> bool:
    return name is not None and (name == "heinzel" or name.startswith("heinzel."))


def _target(module: str, package: bool, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module
    base = module.split(".") if package else module.split(".")[:-1]
    base = base[: len(base) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def _lazy(module: str, package: bool, tree: ast.AST) -> Iterator[tuple[str, list[str]]]:
    def visit(node: ast.AST, *, inside: bool) -> Iterator[tuple[str, list[str]]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.If) and "TYPE_CHECKING" in ast.unparse(child.test):
                for other in child.orelse:
                    yield from visit(ast.Module(body=[other], type_ignores=[]), inside=inside)
                continue
            if inside and isinstance(child, ast.ImportFrom):
                target = _target(module, package, child)
                if target is not None and _heinzel(target):
                    yield target, [alias.name for alias in child.names]
            elif inside and isinstance(child, ast.Import):
                yield from ((alias.name, []) for alias in child.names if _heinzel(alias.name))
            function = isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
            yield from visit(child, inside=inside or function)

    yield from visit(tree, inside=False)


def _defined_class(module: object, name: str) -> bool:
    value = vars(module)[name]
    return isinstance(value, type) and value.__module__ == getattr(module, "__name__", None)


def main() -> None:
    import heinzel.heads.cli
    from heinzel import __version__
    from heinzel.kernel import boot

    boot()
    lines = [f"version {__version__}"]
    for name in sorted(module for module in sys.modules if _heinzel(module)):
        names = sorted(vars(sys.modules[name]))
        lines.append(" ".join(["resident", name, *names]))
        classes = [n for n in names if _defined_class(sys.modules[name], n)]
        if classes:
            lines.append(" ".join(["define", name, *classes]))
    root = Path(heinzel.__file__).parent.parent
    for path in sorted((root / "heinzel").rglob("*.py")):
        parts = list(path.relative_to(root).with_suffix("").parts)
        package = parts[-1] == "__init__"
        module = ".".join(parts[:-1] if package else parts)
        for target, names in _lazy(module, package, ast.parse(path.read_text())):
            lines.append(f"lazy {module} {target} {','.join(names)}".rstrip())
    sys.stdout.write("\n".join(dict.fromkeys(lines)) + "\n")


if __name__ == "__main__":
    main()
