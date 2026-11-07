"""Shared fixtures. The test-suite policy (one level per test, in order, with timeouts) and the isolation of every
test from the developer's HZ_* variables, config and state (`hz_isolation`) are heinzel.testing's."""

import math
import os
import re
import sys
import time
from collections.abc import Callable, Iterator
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Protocol

import pytest

from heinzel import Config, Kernel, Module, ModuleRecord, Settings
from heinzel.kernel import Origin, discover
from heinzel.modules.org import _discovery as discovery
from heinzel.modules.system import _github
from heinzel.modules.system.prerequisite._host import ToolHost
from heinzel.testing import CATEGORIES

TEST_ORIGIN = Origin(distribution="heinzel-test", version="9.9.9", builtin=False)


@pytest.fixture(autouse=True)
def _no_machine_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test finds the developer's GitHub or Google login, time zone or Claude Code (or asks gh and gws)."""
    monkeypatch.setattr(discovery, "discover", discovery.Discovered)
    monkeypatch.setattr(discovery, "_run", lambda args: None)  # and no later caller reaches the real CLIs


@pytest.fixture(autouse=True)
def _release_redirect_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """The latest-release redirect is a network call like the REST API: no test reaches it unless it fakes it."""

    def offline(url: str, timeout: float) -> tuple[int, str]:
        del timeout
        msg = f"the suite stays offline: {url}"
        raise OSError(msg)

    monkeypatch.setattr(_github, "_head", offline)


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
# A browser driver's own stack and the Playwright call that timed out: fixed shapes that hold no value from the failure.
_DRIVER_FRAME = re.compile(r"\b((?:web|desktop)/e2e/[A-Za-z0-9_./-]+\.mjs:[0-9]+)(?::[0-9]+)?")
_DRIVER_TIMEOUT = re.compile(r"\b([a-z][A-Za-z]*\.[a-z][A-Za-z]*: Timeout [0-9]+ms exceeded)")
_ALLOWED_TYPES = frozenset(
    {"AssertionError", "ValueError", "TypeError", "RuntimeError", "TimeoutError", "OSError", "Failed"}
)
_LEVELS = frozenset(CATEGORIES)
_LEVEL_LAYOUT = all((_ROOT / "tests" / level).is_dir() for level in _LEVELS)
_E2E_NODES = pytest.StashKey[tuple[str, ...]]()
_E2E_FILES = pytest.StashKey[set[Path]]()


def _argument_covers(config: pytest.Config, target: Path, *, allow_nodes: bool = False) -> bool:
    """Check whole-directory scope, allowing node-qualified scopes only to locate the guard consumer."""
    for argument in config.args:
        if "::" in argument and not allow_nodes:
            continue
        scope = Path(argument.partition("::")[0])
        if not scope.is_absolute():
            scope = config.invocation_params.dir / scope
        if target.is_relative_to(scope.resolve()):
            return True
    return False


def _inventory_requested(config: pytest.Config) -> bool:
    guard = _ROOT / "tests/integration/e2e_lane_partition_test.py"
    # Copied fixture projects without this core-only guard retain their original lane collection behavior.
    return guard.is_file() and _argument_covers(config, guard, allow_nodes=True)


def pytest_sessionstart(session: pytest.Session) -> None:
    """Start each collection with empty config-owned inventory, even if a caller reuses a config object."""
    session.config.stash[_E2E_NODES] = ()
    session.config.stash[_E2E_FILES] = set()


@pytest.hookimpl
def pytest_collectstart(collector: pytest.Collector) -> None:
    """Record real module collection, including empty files, within this pytest config/xdist worker only."""
    if isinstance(collector, pytest.Module) and collector.path.is_relative_to(_ROOT / "tests/e2e"):
        collector.config.stash.setdefault(_E2E_FILES, set()).add(collector.path.resolve())


def _complete_e2e_inventory(config: pytest.Config) -> tuple[str, ...]:
    """Use the outer real inventory only when its roots and collected files prove completeness."""
    root = _ROOT / "tests/e2e"
    guidance = "Collect the whole tests/e2e directory with -m integration when focusing the partition guard"
    assert _argument_covers(config, root), guidance
    if ignored := config.getoption("ignore"):
        # The Linux Python lane excludes this physical unit file after running it serially.
        retirement = _ROOT / "tests/unit/modules/daemon_legacy_retirement_test.py"
        message = "E2E inventory requires collection without --ignore outside the serial retirement module"
        assert [config.invocation_params.dir / path for path in ignored] == [retirement], message
        assert retirement.is_file(), message
        assert retirement.resolve() == retirement, message
    assert not config.getoption("ignore_glob"), "E2E inventory requires collection without --ignore-glob"
    assert not config.getoption("pyargs"), "E2E inventory requires directory-based collection"
    assert not config.getoption("deselect"), "E2E inventory requires collection without --deselect"
    assert not config.getoption("lf"), "E2E inventory requires collection without --lf"
    assert not config.getoption("doctestmodules"), "E2E inventory requires normal non-doctest collection"
    expected = {path.resolve() for path in root.rglob("*.py") if path.match("test_*.py") or path.match("*_test.py")}
    assert config.getini("python_files") == ["test_*.py", "*_test.py"], (
        "E2E inventory requires the normal file patterns"
    )
    assert config.getini("python_functions") == ["test"], "E2E inventory requires normal function patterns"
    assert config.getini("python_classes") == ["Test"], "E2E inventory requires normal class patterns"
    assert expected, "E2E inventory requires test files"
    assert config.stash.get(_E2E_FILES, set()) == expected, "E2E inventory file collection is incomplete"
    nodes = config.stash.get(_E2E_NODES, ())
    assert nodes, "E2E inventory must contain actual collected nodes"
    return nodes


@pytest.fixture
def collected_e2e_nodes(request: pytest.FixtureRequest) -> tuple[str, ...]:
    """Expose only this config's complete real inventory; never recollect inside a test's deadline."""
    return _complete_e2e_inventory(request.config)


# Keep these integration files' existing node IDs and unit-scoped fixtures until they can move together.
_INTEGRATION_FILES = frozenset(
    {
        "unit/steps_signal_test.py",
        "unit/modules/autonomous_schedule_test.py",
        "unit/modules/user_chat_test.py",
        "unit/modules/scheduled_notifications_test.py",
    }
)


def pytest_addoption(parser: pytest.Parser) -> None:
    """Retain full import-based collection as a comparison; lane validation remains required in both modes."""
    parser.addoption(
        "--full-level-collection",
        action="store_true",
        help="Import every test level before marker selection (diagnostic comparison).",
    )


def _level_layout(config: pytest.Config) -> bool:
    return _LEVEL_LAYOUT and config.getini("testpaths") == ["tests"]


def _path_level(path: Path) -> str | None:
    try:
        relative = path.relative_to(_ROOT / "tests")
    except ValueError:
        return None
    if relative.as_posix() in _INTEGRATION_FILES:
        return "integration"
    return relative.parts[0] if relative.parts and relative.parts[0] in _LEVELS else None


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    """Prune unrelated unit files, retaining E2E inventory when the integration partition guard is collected."""
    if (
        config.getoption("markexpr") != "(integration) and not perf"
        or config.getoption("full_level_collection")
        or not _level_layout(config)
        or not collection_path.is_file()
        or not any(collection_path.match(pattern) for pattern in config.getini("python_files"))
    ):
        return None
    level = _path_level(collection_path)
    # The partition guard consumes the real E2E inventory before marker deselection, rather than recursively
    # collecting a second pytest session inside its test deadline. E2E fixture bodies remain deselected normally.
    if level == "e2e" and _inventory_requested(config):
        return None
    return True if level is not None and level != "integration" else None


@pytest.hookimpl(specname="pytest_collection_modifyitems", hookwrapper=True, tryfirst=True)
def pytest_collection_modifyitems_layout(config: pytest.Config, items: list[pytest.Item]) -> Iterator[None]:
    """Validate each lane's actual markers, including cases another hook deselects or reclassifies."""
    original = tuple(items) if _level_layout(config) else ()
    config.stash[_E2E_NODES] = tuple(item.nodeid for item in original if item.get_closest_marker("e2e") is not None)
    yield
    offenders: set[str] = set()
    for item in original:
        if not item.path.is_relative_to(_ROOT / "tests"):
            continue
        expected = _path_level(item.path)
        actual = [level for level in _LEVELS if item.get_closest_marker(level) is not None]
        if actual != [expected]:
            relative = item.path.relative_to(_ROOT).as_posix()
            safe = relative if re.fullmatch(r"[A-Za-z0-9_./-]+", relative) else "<unclassified-test>"
            offenders.add(f"{safe}: expected {expected or 'a unit, integration or e2e directory'}")
    if offenders:
        message = "Tests must carry exactly one level matching their collection lane:\n  "
        raise pytest.UsageError(message + "\n  ".join(sorted(offenders)))


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


def _driver_lines(report: object) -> list[str]:
    """What a failing browser driver was doing, so the line survives a session limit that cuts off pytest's summary."""
    text = str(getattr(report, "longrepr", ""))
    frames = list(dict.fromkeys(_DRIVER_FRAME.findall(text)))[:8]
    return [f"E2E driver {found}" for found in dict.fromkeys(_DRIVER_TIMEOUT.findall(text))] + [
        f"E2E driver frame {frame}" for frame in frames
    ]


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
            for line in _driver_lines(report):
                _emit(line)
        if phase == "teardown":
            _emit(f"E2E end {_node(nodeid)} seconds={self._durations.pop(nodeid, 0.0):.3f}")


def pytest_configure(config: pytest.Config) -> None:
    """Only the controller emits reports; workers forward their normal reports unchanged."""
    if config.getoption("markexpr") == "(integration) and not perf":
        config.pluginmanager.register(_SessionTiming(config), "heinzel-integration-session-timing")
    if (
        not os.environ.get("PYTEST_XDIST_WORKER")
        and not hasattr(config, "workerinput")
        and config.getoption("markexpr") == "e2e"
    ):
        config.pluginmanager.register(_Diagnostics(), "heinzel-e2e-immediate-diagnostics")


@pytest.hookimpl(specname="pytest_configure")
def pytest_configure_quarantine(config: pytest.Config) -> None:
    """Known flakes in tests/quarantine.toml leave the required jobs for the Quarantine lane (scripts/quarantine.py)."""
    try:
        from tests import _quarantine
    except ImportError:  # a copy of this file elsewhere (e2e scheduling) has no `tests` package and no registry
        return
    _quarantine.configure(config, _ROOT)


class _SessionTiming:
    """Separate collection, test phases and CPU use when the integration session exhausts its budget."""

    def __init__(self, config: pytest.Config) -> None:
        self._config = config
        self._started = time.monotonic()
        self._cpu = os.times()
        self._phases = 0.0
        self._completed = 0

    @pytest.hookimpl(optionalhook=True)
    def pytest_xdist_node_collection_finished(self, node: object, ids: list[str]) -> None:
        worker = getattr(getattr(node, "gateway", None), "id", "")
        if isinstance(worker, str) and re.fullmatch(r"gw[0-9]+", worker):
            _emit(
                f"INTEGRATION collected worker={worker} tests={len(ids)} "
                f"controller_seconds={time.monotonic() - self._started:.3f}"
            )

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        self._phases += report.duration
        self._completed += report.when == "teardown"

    @pytest.hookimpl(tryfirst=True)
    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        del session
        cpu = os.times()
        # Subtract each rounded counter first, so unchanged ticks cannot become a tiny negative total.
        timing = {
            "wall": time.monotonic() - self._started,
            "cpu": (cpu.user - self._cpu.user) + (cpu.system - self._cpu.system),
            "child_cpu": (cpu.children_user - self._cpu.children_user)
            + (cpu.children_system - self._cpu.children_system),
            "phases": self._phases,
            "completed": self._completed,
        }
        if hasattr(self._config, "workeroutput"):
            self._config.workeroutput["heinzel_integration_timing"] = timing
        else:
            _emit(f"INTEGRATION session exit={int(exitstatus)} wall_seconds={timing['wall']:.3f}")

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node: object, error: object) -> None:
        del error  # Exception text can contain private paths or values.
        worker = getattr(getattr(node, "gateway", None), "id", "")
        timing = getattr(node, "workeroutput", {}).get("heinzel_integration_timing")
        if not isinstance(worker, str) or not re.fullmatch(r"gw[0-9]+", worker) or not isinstance(timing, dict):
            return
        if not all(
            type(timing.get(key)) in {int, float} and math.isfinite(timing[key]) and timing[key] >= 0
            for key in ("completed", "wall", "cpu", "child_cpu", "phases")
        ):
            return
        _emit(
            f"INTEGRATION worker={worker} completed={int(timing['completed'])} "
            f"wall_seconds={timing['wall']:.3f} cpu_seconds={timing['cpu']:.3f} "
            f"child_cpu_seconds={timing['child_cpu']:.3f} phase_seconds={timing['phases']:.3f}"
        )


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
