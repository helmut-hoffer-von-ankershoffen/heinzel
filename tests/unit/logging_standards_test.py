"""Structured logging (ADR 0138): logger calls name an event and pass fields as `extra`, never a formatted message.

Code that predates the standard is listed in `FORMATTED_ALLOWED` with its count of formatted calls. The list may
only shrink: a new file or a higher count fails, and so does a count higher than the file still has, so a migration
lowers the number in the same change.
"""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "src" / "heinzel"
LEVELS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "fatal"}
LOGGER_NAMES = {"log", "logger", "_log", "_logger", "LOG", "LOGGER", "_LOG", "_LOGGER", "logging"}

FORMATTED_ALLOWED: dict[str, int] = {
    # Formatted logger calls from before ADR 0138; migrate to `log_event`/`step` and lower the count. Never add.
    "src/heinzel/heads/_stream.py": 1,
    "src/heinzel/heads/mcp/__init__.py": 2,
    "src/heinzel/heads/web/_auto_update.py": 14,
    "src/heinzel/heads/web/_supervision.py": 1,
    "src/heinzel/kernel/_bindings.py": 1,
    "src/heinzel/kernel/_discovery.py": 1,
    "src/heinzel/kernel/_kernel.py": 3,
    "src/heinzel/modules/ai/_usage.py": 1,
    "src/heinzel/modules/ai/access/_service.py": 2,
    "src/heinzel/modules/heinzel/_service.py": 2,
    "src/heinzel/modules/org/_sync.py": 2,
    "src/heinzel/modules/system/daemon/_abandoned_mutation.py": 1,
    "src/heinzel/modules/system/daemon/_service.py": 2,
    "src/heinzel/modules/system/notify/_resolve.py": 1,
    "src/heinzel/modules/system/notify/_service.py": 10,
    "src/heinzel/modules/system/notify/_store.py": 4,
    "src/heinzel/modules/system/runner/chat/_service.py": 2,
    "src/heinzel/modules/system/theme/_service.py": 4,
}


def _is_logger(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id in LOGGER_NAMES
    if isinstance(node, ast.Attribute):
        return node.attr in LOGGER_NAMES
    if isinstance(node, ast.Call):  # logging.getLogger(...).info(...), get_logger(...).info(...)
        func = node.func
        return (isinstance(func, ast.Attribute) and func.attr == "getLogger") or (
            isinstance(func, ast.Name) and func.id in {"getLogger", "get_logger"}
        )
    return False


def _is_formatted(message: ast.expr) -> bool:
    if isinstance(message, ast.JoinedStr):
        return True
    if isinstance(message, ast.BinOp) and isinstance(message.op, ast.Mod | ast.Add):
        return True
    return isinstance(message, ast.Call) and isinstance(message.func, ast.Attribute) and message.func.attr == "format"


def formatted_logger_calls(source: str) -> list[int]:
    """Lines of logger calls whose message is an f-string, `%`, `+` or `.format`, or that pass `%`-arguments."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and _is_logger(node.func.value)):
            continue
        method = node.func.attr
        if method not in LEVELS and method != "log":
            continue
        arguments = node.args[1:] if method == "log" else node.args
        if arguments and (_is_formatted(arguments[0]) or len(arguments) > 1):
            lines.append(node.lineno)
    return lines


def _counts() -> dict[str, int]:
    counts = {}
    for path in sorted(SOURCE.rglob("*.py")):
        found = formatted_logger_calls(path.read_text(encoding="utf-8"))
        if found:
            counts[path.relative_to(REPO).as_posix()] = len(found)
    return counts


@pytest.mark.parametrize(
    "call",
    [
        'log.info(f"installed {name}")',
        'logger.warning("installed %s", name)',
        'log.error("installed %s" % name)',
        'log.info("installed " + name)',
        'log.debug("installed {}".format(name))',
        'logging.getLogger(__name__).info("installed %s", name)',
        'get_logger("plugin").info(f"{name}")',
        'self._log.exception("failed: %s", exc)',
        'log.log(logging.INFO, "installed %s", name)',
    ],
)
def test_finds_formatted_logger_calls(call):
    assert formatted_logger_calls(call) == [1]


@pytest.mark.parametrize(
    "call",
    [
        'log.info("plugin.install.download", extra={"plugin": name})',
        'log.debug("noting presence failed", exc_info=True)',
        'log_event(log, "plugin.install.download", plugin=name)',
        'console.print(f"installed {name}")',
        'log.log(logging.INFO, "plugin.install.done")',
    ],
)
def test_leaves_structured_and_plain_calls_alone(call):
    assert formatted_logger_calls(call) == []


def test_new_code_logs_structured_events():
    counts = _counts()
    grown = {path: count for path, count in counts.items() if count > FORMATTED_ALLOWED.get(path, 0)}
    assert not grown, (
        "These files format logger messages; log an event name with fields instead "
        f"(`log_event`/`step` in heinzel.kernel._logging, CODING_STANDARDS 'Structured logging'): {grown}"
    )


def test_the_allowlist_only_shrinks():
    counts = _counts()
    stale = {path: allowed for path, allowed in FORMATTED_ALLOWED.items() if counts.get(path, 0) < allowed}
    assert not stale, f"Lower these counts in FORMATTED_ALLOWED to what the files still have: {stale} now {counts}"
