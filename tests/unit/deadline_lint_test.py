"""A wall-clock limit in a test goes through `heinzel.testing.scaled`, so a slow runner scales it (#885).

One concept needed eleven files in five directories when the macOS runners got slow (#885). The tests under the
contexts that own time (`runtime/`) and the port contracts may not hold a bare number of seconds: `timeout=20`,
`thread.join(5)`, `event.wait(5)` and `time.sleep(0.1)` become `scaled(20)` and so on. Waiting on a `FakeClock` is not
a wall-clock limit and is not checked.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LINTED = [*sorted((ROOT / "tests/unit/runtime").rglob("*_test.py")), ROOT / "tests/unit/ports_contract_test.py"]
WAITS = {"wait", "join", "result", "communicate"}

pytestmark = pytest.mark.unit


def _number(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, int | float) and not isinstance(node.value, bool)


def bare_limits(source: str) -> list[int]:
    """The line numbers of calls that pass a literal number of seconds as a limit."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        name = function.attr if isinstance(function, ast.Attribute) else getattr(function, "id", "")
        owner = ast.unparse(function.value) if isinstance(function, ast.Attribute) else ""
        sleeping = name == "sleep" and owner == "time"
        limited = name in WAITS and node.args and _number(node.args[0])
        keyword = any(k.arg == "timeout" and _number(k.value) for k in node.keywords)
        if (sleeping and node.args and _number(node.args[0])) or limited or keyword:
            found.append(node.lineno)
    return found


def test_the_lint_finds_a_bare_limit_and_lets_scaled_ones_pass():
    assert bare_limits("run(cmd, timeout=20)\nthread.join(5)\ntime.sleep(0.1)\nevent.wait(2)") == [1, 2, 3, 4]
    assert bare_limits("run(cmd, timeout=scaled(20))\nthread.join(scaled(5))\nclock.sleep(3600)") == []


@pytest.mark.parametrize("path", LINTED, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_a_limit_in_a_context_test_is_scaled(path):
    assert bare_limits(path.read_text(encoding="utf-8")) == [], "wrap the seconds in heinzel.testing.scaled(...)"
