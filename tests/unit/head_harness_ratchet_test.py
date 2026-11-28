"""Keep direct head factories in their existing tests. New operation tests use the shared harness."""

import ast
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "tests/fixtures/head-build-callers.txt"
HEADS = {"cli", "api", "mcp", "web"}
BINDINGS = {ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign}

pytestmark = pytest.mark.unit


def _qualified(node, aliases):
    match node:
        case ast.Name(id=name):
            return aliases.get(name, {name})
        case ast.Attribute(value=value, attr=attribute):
            return {f"{owner}.{attribute}" for owner in _qualified(value, aliases)}
        case ast.Call(func=func, args=[ast.Constant(value=str() as module), *_]):
            loaders = {"__import__", "builtins.__import__", "importlib.import_module"}
            if loaders & _qualified(func, aliases):
                return {module}
        case ast.Call(func=func, args=[instance, ast.Constant(value=str() as attribute), *_]):
            if {"getattr", "builtins.getattr"} & _qualified(func, aliases):
                return {f"{owner}.{attribute}" for owner in _qualified(instance, aliases)}
    return set()


def _aliases(nodes):
    aliases = defaultdict(set)
    assignments = []
    for node in nodes:
        match node:
            case ast.Import(names=names):
                for item in names:
                    visible = item.asname or item.name.partition(".")[0]
                    aliases[visible].add(item.name if item.asname else visible)
            case ast.ImportFrom(module=str() as package, level=0, names=names):
                for item in names:
                    name = "build" if item.name == "*" else item.name
                    aliases[item.asname or name].add(f"{package}.{name}")
            case ast.Assign(targets=targets, value=value):
                assignments.extend((target.id, value) for target in targets if isinstance(target, ast.Name))
            case ast.AnnAssign(target=ast.Name(id=target), value=value) if value is not None:
                assignments.append((target, value))
    # Follow straightforward rename chains without repeatedly expanding later assignments through themselves.
    # Reaching definitions by source order handles ordinary aliases and keeps cyclic or self-referential values finite.
    for target, value in assignments:
        aliases[target].update(
            name for name in _qualified(value, aliases) if name == "heinzel" or name.startswith("heinzel.")
        )
    return aliases


def _literal_build(node):
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and (node.value == "build" or node.value.endswith(".build"))
    )


def _direct_heads(text):
    bindings = []
    candidates = []
    for node in ast.walk(ast.parse(text)):
        kind = type(node)
        if kind in BINDINGS:
            bindings.append(node)
        elif kind is ast.Attribute and node.attr == "build":
            candidates.append(node)
        elif kind is ast.Call:
            args = node.args
            if args and (_literal_build(args[0]) or (len(args) > 1 and _literal_build(args[1]))):
                # Dynamic imports and getattr can return a qualified name ending in build.
                candidates.append(node)
    aliases = _aliases(sorted(bindings, key=lambda node: node.lineno))
    names = set().union(*aliases.values())
    for node in candidates:
        names.update(_qualified(node, aliases))
    return {
        parts[2]
        for name in names
        if len(parts := name.split(".")) >= 4
        and parts[:2] == ["heinzel", "heads"]
        and parts[2] in HEADS
        and parts[-1] == "build"
    }


def _current_callers():
    return {
        f"{head} {path.relative_to(ROOT).as_posix()}"
        for path in (ROOT / "tests").rglob("*.py")
        for head in _direct_heads(path.read_text(encoding="utf-8"))
    }


def test_direct_head_factory_callers_only_get_fewer():
    pinned = {line for line in BASELINE.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")}
    current = _current_callers()
    added, removed = sorted(current - pinned), sorted(pinned - current)
    assert not added, f"New direct factory callers must use heinzel.testing.heads: {added}"
    assert not removed, f"Delete retired entries from the baseline: {removed}"


@pytest.mark.parametrize(
    ("source", "heads"),
    [
        ("from heinzel.heads.cli import build as make\nmake(kernel)", {"cli"}),
        ("from heinzel.heads.cli import *\nbuild(kernel)", {"cli"}),
        ("from heinzel.heads import api as http\nfactory = http\nfactory.build(kernel)", {"api"}),
        ("import heinzel.heads.web\nheinzel.heads.web.build(kernel)", {"web"}),
        (
            'from importlib import import_module as load\nserver = load("heinzel.heads.mcp")\nserver.build(kernel)',
            {"mcp"},
        ),
        ('from heinzel import heads as gateways\ngetattr(gateways.api, "build")(kernel)', {"api"}),
        (
            (
                "import builtins as b\nfrom importlib import import_module as load\n"
                r'b.getattr(load("heinzel.heads.web"), "\x62uild")(kernel)'
            ),
            {"web"},
        ),
        ('from importlib import import_module as load\nload("heinzel.heads.mcp.build")', {"mcp"}),
        ("from heinzel.testing.heads import operate\noperate('cli', kernel, 'clock.now')", set()),
    ],
)
def test_direct_head_scan_follows_factory_and_module_aliases(source, heads):
    assert _direct_heads(source) == heads
