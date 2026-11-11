"""What a person reads when the desktop installer fails follows the CLI writing rule (docs/CODING_STANDARDS.md).

At most two lines of at most `MAX_LINE` characters, none of the `BANNED_WORDS`, and the last sentence names a command
to type (`run hz ...`, `: hz ...`) or `--details`. An installer error with a `Problem` shows its two lines, so every
Problem in `_desktop_messages` is checked; an error without one still shows its message, so those are read from the
installer's source and checked too, the day they are written.
"""

import ast
import inspect
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from heinzel.heads import _desktop_installation
from heinzel.heads import _desktop_messages as say

pytestmark = pytest.mark.unit

SOURCE = Path(_desktop_installation.__file__)
BANNED = re.compile(rf"\b({'|'.join(say.BANNED_WORDS)})s?\b", re.IGNORECASE)
COMMAND = re.compile(r"(?:\brun |: )hz [a-z]|--details", re.IGNORECASE)
PLACEHOLDER = re.compile(r"\{[^}]*\}")
"""A value filled in at run time, such as `{bundle}`: its variable name is not shown, so it is not checked."""
ERRORS = {"DesktopInstallationError", "LeftoverInstallError", "_conflict"}
"""What builds an installer error; a second argument is the Problem the person reads instead of the message."""

# Messages without a Problem, written before the rule. The recovery rewrite gives each a Problem; this list only
# shrinks, never grows: a message that is fixed or gone must leave it (the last test fails until it does).
LEGACY = {
    "Managed desktop installation requires an Apple silicon Mac.",
    "The desktop installer cannot safely use {applications}.",
    "The desktop installer cannot safely use {path}.",
    "The desktop installer could not establish a safe installation lock.",
    "The desktop app is not at {bundle} after installation.",
    "The verified archive does not contain the expected Apple silicon desktop bundle.",
    "The desktop bundle or its metadata is present without a matching ownership receipt.",
    "Desktop installation requires a stable release and an absolute, executable hz path.",
    "The desktop installation metadata exceeds the allowed size.",
    "Quit Heinzelmännchen before checking the desktop app.",
    "Quit Heinzelmännchen before repairing the desktop app.",
}


def _assignments(scope: ast.AST) -> dict[str, list[tuple[int, ast.expr]]]:
    found: dict[str, list[tuple[int, ast.expr]]] = {}
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            found.setdefault(node.targets[0].id, []).append((node.lineno, node.value))
    return found


def _text(node: ast.expr, lookup: Callable[[str], ast.expr | None]) -> str | None:
    """The text `node` evaluates to, with run-time values left as `{name}`; None when it is not a literal."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = [
            part.value if isinstance(part, ast.Constant) else f"{{{ast.unparse(part.value)}}}"  # type: ignore[attr-defined]
            for part in node.values
        ]
        return "".join(str(part) for part in parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _text(node.left, lookup), _text(node.right, lookup)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        return _text(node.func.value, lookup)
    if isinstance(node, ast.Name) and (value := lookup(node.id)) is not None:
        return _text(value, lookup)
    return None


def messages() -> set[str]:
    """Every message an installer error is built with when it has no Problem to show instead."""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    module = _assignments(ast.Module(body=[n for n in tree.body if isinstance(n, ast.Assign)], type_ignores=[]))
    found: set[str] = set()
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef) or function.name == "_conflict":
            continue
        local = _assignments(function)
        for call in ast.walk(function):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id in ERRORS):
                continue
            if len(call.args) > 1:
                continue

            def lookup(
                name: str, line: int = call.lineno, local: dict[str, list[tuple[int, ast.expr]]] = local
            ) -> ast.expr | None:
                before = [value for at, value in local.get(name, []) if at < line]
                if before:
                    return before[-1]
                return module[name][-1][1] if name in module else None

            text = _text(call.args[0], lookup)
            assert text is not None, f"line {call.lineno}: the message is not a literal this test can read"
            if call.func.id == "_conflict":
                text = f"{_text(ast.Name('_CONFLICT'), lookup)} {text}"
            found.add(text)
    return found


def problems(text: str) -> list[str]:
    """What breaks the writing rule in `text`; empty when it follows it."""
    lines = text.splitlines()
    found = [f"{len(lines)} lines"] if len(lines) > 2 else []
    found += [f"{len(line)} characters" for line in lines if len(line) > say.MAX_LINE]
    found += [f"says {word.group()!r}" for word in BANNED.finditer(PLACEHOLDER.sub("", text))]
    last = re.split(r"(?<=[.!?])\s+", lines[-1].strip())[-1] if lines else ""
    if not COMMAND.search(last):
        found.append("does not end with a command or --details")
    return found


def _sample(parameter: inspect.Parameter) -> object:
    if parameter.annotation in {Path, "Path"}:
        return Path("{path}")  # measured like the installer's messages: the template, not one user's path
    if parameter.annotation in {bool, "bool"}:
        return True
    return parameter.default if parameter.default is not inspect.Parameter.empty else "install"


def _problems_in_messages() -> dict[str, say.Problem]:
    found: dict[str, say.Problem] = {}
    for name, function in inspect.getmembers(say, inspect.isfunction):
        signature = inspect.signature(function)
        if signature.return_annotation not in {say.Problem, "Problem"}:
            continue
        for everyone in (True, False):
            arguments = {
                key: (everyone if key == "everyone" else _sample(parameter))
                for key, parameter in signature.parameters.items()
            }
            found[f"{name}(everyone={everyone})"] = function(**arguments)
    return found


def test_the_rule_accepts_the_designed_two_line_shape():
    assert not problems(
        "Heinzelmännchen is open, so the app in /Applications cannot be replaced right now.\n"
        "Quit it, then run hz app install again."
    )
    assert problems("The desktop bundle or its metadata is present without a matching ownership receipt.")


def test_every_problem_the_person_reads_follows_the_writing_rule():
    found = _problems_in_messages()
    assert len(found) >= 10
    broken = {name: problems("\n".join(problem.lines())) for name, problem in found.items()}
    assert not {name: issues for name, issues in broken.items() if issues}


def test_every_new_installer_error_without_a_problem_follows_the_writing_rule():
    broken = {text: problems(text) for text in messages() - LEGACY if problems(text)}
    assert not broken


def test_the_legacy_list_only_shrinks():
    current = messages()
    assert not LEGACY - current, "gone from the installer: remove from LEGACY"
    assert not [text for text in LEGACY if not problems(text)], "follows the rule now: remove from LEGACY"
