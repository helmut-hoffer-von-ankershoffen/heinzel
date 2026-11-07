"""Every `DesktopInstallationError` message follows the CLI writing rule (docs/CODING_STANDARDS.md, CLI output).

At most two lines of at most 110 characters, none of the installer's internal names, and the last sentence names
a command to type (`run hz ...`, `: hz ...`) or `--details`. Messages are read from the source, so a new one is
checked the day it is written.
"""

import ast
import re
from pathlib import Path

import pytest

from heinzel.heads import _desktop_installation

pytestmark = pytest.mark.unit

SOURCE = Path(_desktop_installation.__file__)
BANNED = re.compile(r"\b(receipt|bundle|metadata|binding|ownership|journal|seal|lock)s?\b", re.IGNORECASE)
COMMAND = re.compile(r"(?:\brun |: )hz [a-z]|--details", re.IGNORECASE)
PLACEHOLDER = re.compile(r"\{[^}]*\}")
"""A value filled in at run time, such as `{bundle}`: its variable name is not shown, so it is not checked."""
MAX_WIDTH = 110

# Messages written before the rule. The recovery rewrite replaces them; this list only shrinks, never grows: a
# message that is fixed or gone must leave it (the second test fails until it does).
LEGACY = {
    "Managed desktop installation requires an Apple silicon Mac.",
    "The desktop installer cannot safely use {applications}.",
    "The desktop installer cannot safely use {path}.",
    "The desktop installer could not establish a safe installation lock.",
    (
        "Another Heinzelmännchen install, update or removal has held the desktop lock for {LOCK_WAIT} seconds. "
        "Wait for it to finish or quit it (look for `hz app` in `ps aux`), then run the command again."
    ),
    "The desktop app is not at {bundle} after installation.",
    "The verified archive does not contain the expected Apple silicon desktop bundle.",
    "The desktop bundle or its metadata is present without a matching ownership receipt.",
    "Desktop installation requires a stable release and an absolute, executable hz path.",
    "The desktop installation metadata exceeds the allowed size.",
    "Quit Heinzelmännchen before uninstalling the desktop app.",
    "Quit Heinzelmännchen before checking the desktop app.",
    "Quit Heinzelmännchen before repairing the desktop app.",
    (
        "{target} is already there and was not installed by hz app install. Move it to the Trash, then run hz app "
        "install again."
    ),
}


def _text(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value if isinstance(part, ast.Constant) else f"{{{ast.unparse(part.value)}}}"  # type: ignore[attr-defined]
            for part in node.values
        )
    return None


def _assignments(scope: ast.AST) -> dict[str, list[tuple[int, ast.expr]]]:
    found: dict[str, list[tuple[int, ast.expr]]] = {}
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            found.setdefault(node.targets[0].id, []).append((node.lineno, node.value))
    return found


def messages() -> set[str]:
    """Every message a `DesktopInstallationError` is raised with in the installer."""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    module = _assignments(ast.Module(body=[n for n in tree.body if isinstance(n, ast.Assign)], type_ignores=[]))
    found: set[str] = set()
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef):
            continue
        local = _assignments(function)
        for call in ast.walk(function):
            if not (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "DesktopInstallationError"
            ):
                continue
            (argument,) = call.args
            text = _text(argument)
            if text is None and isinstance(argument, ast.Name):
                before = [value for line, value in local.get(argument.id, []) if line < call.lineno]
                value = before[-1] if before else module[argument.id][-1][1]
                text = _text(value)
            assert text is not None, f"line {call.lineno}: the message is not a literal this test can read"
            found.add(text)
    return found


def problems(text: str) -> list[str]:
    """What breaks the writing rule in `text`; empty when it follows it."""
    lines = text.splitlines()
    found = [f"{len(lines)} lines"] if len(lines) > 2 else []
    found += [f"{len(line)} characters" for line in lines if len(line) > MAX_WIDTH]
    found += [f"says {word.group()!r}" for word in BANNED.finditer(PLACEHOLDER.sub("", text))]
    last = re.split(r"(?<=[.!?])\s+", lines[-1].strip())[-1] if lines else ""
    if not COMMAND.search(last):
        found.append("does not end with a command or --details")
    return found


def test_the_rule_accepts_the_designed_two_line_shape():
    assert not problems(
        "Heinzelmännchen is open, so the app in /Applications cannot be replaced right now.\n"
        "Quit it, then run hz app install again."
    )
    assert problems("The desktop bundle or its metadata is present without a matching ownership receipt.")


def test_every_new_installer_error_follows_the_writing_rule():
    broken = {text: problems(text) for text in messages() - LEGACY if problems(text)}
    assert not broken


def test_the_legacy_list_only_shrinks():
    current = messages()
    assert not LEGACY - current, "gone from the installer: remove from LEGACY"
    assert not [text for text in LEGACY if not problems(text)], "follows the rule now: remove from LEGACY"
