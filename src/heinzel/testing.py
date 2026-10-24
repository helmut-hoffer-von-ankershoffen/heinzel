"""Pytest fixtures for testing Heinzelmännchen modules and plugins, registered automatically (`pytest11`).

A plugin's test needs no conftest:

```python
def test_clock(hz):
    times = hz("clock", "--at", "2026-10-23T12:00:00Z").json
    assert times[0]["zone"] == "Europe/Berlin"
```

Every test runs with its own configuration, data and cache directories, so nothing touches the developer's setup.

A project that declares the markers `unit`, `integration` and `e2e` (Heinzelmännchen and every plugin from its
template) also gets the test-suite policy of docs/CODING_STANDARDS.md: every test carries exactly one of them, they
run in that order, and each test has its level's timeout (`TIMEOUTS`) unless it sets `@pytest.mark.timeout`.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from heinzel.kernel import Kernel

# Heinzelmännchen itself is imported inside the fixtures: pytest loads this plugin before coverage measurement starts,
# and a top-level import would hide the import-time lines of every module from the coverage report.

CATEGORIES = ("unit", "integration", "e2e")  # also the order they run in: fastest first, so failures show early
TIMEOUTS = {"unit": 5, "integration": 15, "e2e": 120}  # seconds per test unless a test sets @pytest.mark.timeout

ISOLATED = (
    "HZ_CONFIG",
    "HZ_LOG_LEVEL",
    "HZ_OUTPUT_FORMAT",
    "HZ_PLUGINS_ENABLED",
    "HZ_DISABLED_MODULES",
    "XDG_CONFIG_HOME",
)


@dataclass(frozen=True, slots=True)
class Result:
    """What a `hz` command printed and how it exited."""

    exit_code: int
    output: str

    @property
    def json(self) -> Any:  # noqa: ANN401 - whatever the command printed
        """The output parsed as JSON (the command must have run with `--json`, which `hz(...)` adds)."""
        return json.loads(self.output)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Enforce the test levels in projects that declare them: exactly one per test, run in order, with timeouts.

    Raises:
        pytest.UsageError: If a test carries none or several of the levels.
    """
    declared = {line.split(":", 1)[0].strip() for line in config.getini("markers")}
    if not declared.issuperset(CATEGORIES):
        return
    offenders = [item.nodeid for item in items if sum(item.get_closest_marker(c) is not None for c in CATEGORIES) != 1]
    if offenders:
        msg = f"Tests must carry exactly one of the markers {CATEGORIES}:\n  " + "\n  ".join(offenders)
        raise pytest.UsageError(msg)
    items.sort(key=_category_index)
    if not config.pluginmanager.hasplugin("timeout"):
        return
    for item in items:
        if item.get_closest_marker("timeout") is None:
            item.add_marker(pytest.mark.timeout(TIMEOUTS[CATEGORIES[_category_index(item)]]))


def _category_index(item: pytest.Item) -> int:
    return next(i for i, category in enumerate(CATEGORIES) if item.get_closest_marker(category))


@pytest.fixture(autouse=True)
def hz_isolation(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """No test sees the developer's HZ_* variables, configuration or state."""
    for name in ISOLATED:
        monkeypatch.delenv(name, raising=False)
    for name in ("HZ_CONFIG_HOME", "HZ_DATA_DIR", "HZ_CACHE_DIR"):
        monkeypatch.setenv(name, str(tmp_path_factory.mktemp(name.lower())))


@pytest.fixture
def hz_kernel() -> "Kernel":
    """A kernel with the built-in modules and every installed plugin, configured only by environment variables."""
    from heinzel.kernel import Config, Kernel, Settings, discover  # noqa: PLC0415 - see the module comment

    config = Config.empty()
    records, errors = discover()
    return Kernel(config.settings(Settings, "hz"), records, errors, config)


@pytest.fixture
def hz(hz_kernel: "Kernel") -> Callable[..., Result]:
    """Run a `hz` command in-process: `hz("clock", "--at", "...")`; JSON output unless `json=False`."""
    from typer.testing import CliRunner  # noqa: PLC0415 - see the module comment

    from heinzel.heads.cli import build  # noqa: PLC0415 - see the module comment

    app = build(hz_kernel)
    runner = CliRunner()

    def run(*args: str, json: bool = True) -> Result:
        result = runner.invoke(app, [*args, "--json"] if json else list(args))
        return Result(exit_code=result.exit_code, output=result.output)

    return run
