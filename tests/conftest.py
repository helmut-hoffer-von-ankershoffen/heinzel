"""Shared fixtures. The test-suite policy (one level per test, in order, with timeouts) and the isolation of every
test from the developer's HZ_* variables, config and state (`hz_isolation`) are heinzel.testing's."""

import os
import re
import sys
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Protocol

import pytest

from heinzel import Config, Kernel, Module, ModuleRecord, Settings
from heinzel.kernel import Origin, discover
from heinzel.modules.org import _discovery as discovery
from heinzel.modules.system.prerequisite._host import ToolHost

TEST_ORIGIN = Origin(distribution="heinzel-test", version="9.9.9", builtin=False)


@pytest.fixture(autouse=True)
def _no_machine_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test finds the developer's GitHub or Google login, time zone or Claude Code (or asks gh and gws)."""
    monkeypatch.setattr(discovery, "discover", discovery.Discovered)
    monkeypatch.setattr(discovery, "_run", lambda args: None)  # and no later caller reaches the real CLIs


@pytest.fixture(autouse=True)
def _no_real_homebrew(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test reaches the Homebrew or the tools of the machine it runs on (ADR 0112).

    The machine the prerequisite engine looks at is a Linux host where every tool is present and prints a version, so
    results do not depend on what the developer installed and nothing is ever installed. Tests about the engine, the
    installer or setup describe their own machine (`tests/_prerequisite_machine.py`).
    """
    try:
        from tests._prerequisite_machine import ready_everywhere
    except ImportError:  # a test that runs a copy of this file elsewhere (e2e scheduling) has no `tests` package
        return
    monkeypatch.setattr(ToolHost, "system", classmethod(lambda cls: ready_everywhere()))


@pytest.fixture
def settings() -> Settings:
    return Config.empty().settings(Settings, "hz")


@pytest.fixture
def make_kernel(settings: Settings) -> Callable[..., Kernel]:
    """Build a kernel from modules given as `qualname=module`, optionally on top of the built-in modules."""

    def make(
        *, builtins: bool = False, settings_: Settings | None = None, config: Config | None = None, **modules: Module
    ) -> Kernel:
        records = discover(plugins=False)[0] if builtins else []
        records += [
            ModuleRecord(qualname=name.replace("__", "."), module=module, origin=TEST_ORIGIN)
            for name, module in modules.items()
        ]
        return Kernel(settings_ or settings, records, config=config)

    return make


_ROOT = Path(__file__).resolve().parents[1]
_NODE = re.compile(r"tests/e2e/[A-Za-z0-9_./:-]+\Z")
_TYPE = re.compile(r"(?:^|\n)(?:[A-Za-z_]\w*\.)*([A-Za-z_]\w*(?:Error|Exception)|Failed)(?::|\Z)")
_ALLOWED_TYPES = frozenset(
    {"AssertionError", "ValueError", "TypeError", "RuntimeError", "TimeoutError", "OSError", "Failed"}
)


def _node(value: str) -> str:
    value = value.split("[", 1)[0]
    return value if _NODE.fullmatch(value) else "<redacted-node>"


def _frames(report: object) -> tuple[list[str], str]:
    representation = getattr(report, "longrepr", None)
    crash = getattr(representation, "reprcrash", None)
    message = getattr(crash, "message", "")
    match = _TYPE.search(message) if isinstance(message, str) else None
    kind = match.group(1) if match and match.group(1) in _ALLOWED_TYPES else "Failure"
    traceback = getattr(representation, "reprtraceback", None)
    result = []
    for entry in getattr(traceback, "reprentries", ()):
        location = getattr(entry, "reprfileloc", None)
        raw = getattr(location, "path", None)
        line = getattr(location, "lineno", None)
        if not isinstance(raw, str) or not isinstance(line, int) or line < 1:
            continue
        path = Path(raw)
        try:
            resolved = path.resolve(strict=False)
            relative = resolved.relative_to(_ROOT).as_posix()
        except (OSError, RuntimeError, ValueError):
            continue
        if re.fullmatch(r"[A-Za-z0-9_./-]+", relative):
            result.append(f"{relative}:{line}")
    return result[-12:], kind


def _emit(value: str) -> None:
    sys.stdout.write(value + "\n")
    sys.stdout.flush()


class _Diagnostics:
    def __init__(self) -> None:
        self._durations: dict[str, float] = {}

    def pytest_runtest_logstart(self, nodeid: str, location: object) -> None:
        del location
        _emit(f"E2E start {_node(nodeid)}")

    def pytest_runtest_logreport(self, report: object) -> None:
        nodeid = getattr(report, "nodeid", "")
        if not isinstance(nodeid, str):
            return
        phase = getattr(report, "when", "")
        duration = getattr(report, "duration", 0.0)
        seconds = float(duration) if isinstance(duration, (int, float)) and 0 <= duration <= 3600 else 0.0
        self._durations[nodeid] = self._durations.get(nodeid, 0.0) + seconds
        if getattr(report, "failed", False) and phase in {"setup", "call", "teardown"}:
            frames, kind = _frames(report)
            _emit(f"E2E failure {_node(nodeid)} phase={phase} seconds={seconds:.3f} type={kind}")
            for frame in frames:
                _emit(f"E2E frame {frame}")
        if phase == "teardown":
            _emit(f"E2E end {_node(nodeid)} seconds={self._durations.pop(nodeid, 0.0):.3f}")


def pytest_configure(config: pytest.Config) -> None:
    """Only the controller emits reports; workers forward their normal reports unchanged."""
    if (
        not os.environ.get("PYTEST_XDIST_WORKER")
        and not hasattr(config, "workerinput")
        and config.getoption("markexpr") == "e2e"
    ):
        config.pluginmanager.register(_Diagnostics(), "heinzel-e2e-immediate-diagnostics")


def _spread_early_e2e(items: list[pytest.Item], workers: int) -> list[pytest.Item]:
    """Place one hinted test per known xdist initial batch, preserving all other order."""
    if workers != 3 or len(items) < 2 * workers:
        return list(items)
    early = [item for item in items if item.get_closest_marker("e2e_early")]
    if not early:
        return list(items)
    regular = [item for item in items if not item.get_closest_marker("e2e_early")]
    batch = max(2, (len(items) // workers) // 4)
    result: list[pytest.Item] = []
    for _ in range(workers):
        capacity = batch
        if early:
            result.append(early.pop(0))
            capacity -= 1
        take = min(capacity, len(regular))
        result.extend(regular[:take])
        del regular[:take]
    return result + early + regular


class _WorkerNode(Protocol):
    config: pytest.Config
    workerinput: dict[str, object]


def _early_scheduler(config: pytest.Config) -> bool:
    if (
        config.getoption("markexpr") != "e2e"
        or getattr(config.option, "numprocesses", None) != 3
        or getattr(config.option, "dist", None) != "load"
        or getattr(config.option, "maxschedchunk", None) is not None
        or getattr(config.option, "maxprocesses", None) is not None
    ):
        return False
    try:
        return version("pytest-xdist") == "3.8.0"
    except PackageNotFoundError:
        return False


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node: _WorkerNode) -> None:
    """Propagate the checked controller scheduler choice to each xdist worker."""
    node.workerinput["heinzel_e2e_early"] = _early_scheduler(node.config)


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Use E2E hints only for the known three-worker xdist load scheduler."""
    workerinput = getattr(config, "workerinput", None)
    if isinstance(workerinput, dict):
        enabled = workerinput.get("heinzel_e2e_early") is True and workerinput.get("workercount") == 3
    else:
        enabled = _early_scheduler(config)
    if enabled:
        items[:] = _spread_early_e2e(items, 3)
