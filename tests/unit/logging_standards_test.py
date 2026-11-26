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
LOGGER_SPELLINGS = LOGGER_NAMES | {"getLogger", "get_logger"}

FORMATTED_ALLOWED: dict[str, int] = {
    # Formatted logger calls from before ADR 0138; migrate to `log_event`/`step` and lower the count. Never add.
    "src/heinzel/heads/web/_auto_update.py": 2,
    "src/heinzel/modules/heinzel/_service.py": 2,
    "src/heinzel/modules/system/updater/_schedule.py": 12,
}
SOURCE_PATHS = {path.relative_to(REPO).as_posix() for path in SOURCE.rglob("*.py")}


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
    tree = ast.parse(source)
    # Non-ASCII identifiers can normalize to a logger spelling during parsing.
    if source.isascii() and not any(name in source for name in LOGGER_SPELLINGS):
        return []
    lines = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and _is_logger(node.func.value)):
            continue
        method = node.func.attr
        if method not in LEVELS and method != "log":
            continue
        arguments = node.args[1:] if method == "log" else node.args
        if arguments and (_is_formatted(arguments[0]) or len(arguments) > 1):
            lines.append(node.lineno)
    return lines


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
        'LOGGER.info(f"installed {name}")',
        '\N{SCRIPT SMALL L}og.info(f"installed {name}")',
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


@pytest.mark.parametrize("path", sorted(SOURCE_PATHS | FORMATTED_ALLOWED.keys()))
def test_new_code_logs_structured_events(path: str):
    """Check each file once, including stale entries for deleted files."""
    lines = formatted_logger_calls((REPO / path).read_text(encoding="utf-8")) if path in SOURCE_PATHS else []
    allowed = FORMATTED_ALLOWED.get(path, 0)
    assert len(lines) <= allowed, (
        f"{path}: log an event name with fields (`log_event`/`step` in heinzel.kernel._logging); "
        f"found {len(lines)} formatted calls on lines {lines}, allowed {allowed}"
    )
    assert len(lines) >= allowed, f"Lower FORMATTED_ALLOWED for {path} from {allowed} to {len(lines)}"
