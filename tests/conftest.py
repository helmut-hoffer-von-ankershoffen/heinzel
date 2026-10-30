"""Shared fixtures. The test-suite policy (one level per test, in order, with timeouts) and the isolation of every
test from the developer's HZ_* variables, config and state (`hz_isolation`) are heinzel.testing's."""

import os
import re
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from heinzel import Config, Kernel, Module, ModuleRecord, Settings
from heinzel.kernel import Origin, discover
from heinzel.modules.org import _discovery as discovery

TEST_ORIGIN = Origin(distribution="heinzel-test", version="9.9.9", builtin=False)


@pytest.fixture(autouse=True)
def _no_machine_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test finds the developer's GitHub or Google login, time zone or Claude Code (or asks gh and gws)."""
    monkeypatch.setattr(discovery, "discover", discovery.Discovered)
    monkeypatch.setattr(discovery, "_run", lambda args: None)  # and no later caller reaches the real CLIs


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
