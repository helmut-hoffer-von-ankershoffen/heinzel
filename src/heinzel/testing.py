"""Pytest fixtures for testing Heinzelmännchen modules and plugins, registered automatically (`pytest11`).

A plugin's test needs no conftest:

```python
def test_clock(hz):
    times = hz("clock", "--at", "2026-10-23T12:00:00Z").json
    assert times[0]["zone"] == "Europe/Berlin"
```

Every test sees the terminal CI has: 80 columns and no colour, whatever the developer's terminal and `FORCE_COLOR`
say (`pin_terminal`), so output that depends on either fails locally too.

Every test runs with its own configuration, data, cache, state, log and daemon unit directories, so nothing touches
the developer's setup, and credentials come from the (empty) environment instead of the OS keychain. A test that
stores credentials asks for `hz_credentials`, which keeps them in memory.

A project that declares the markers `unit`, `integration` and `e2e` (Heinzelmännchen and every plugin from its
template) also gets the test-suite policy of docs/CODING_STANDARDS.md: every test carries exactly one of them, they
run in that order, and each test has its level's timeout (`TIMEOUTS`) unless it sets `@pytest.mark.timeout`.
"""

import json
import os
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from heinzel.kernel import Credentials, Kernel

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

TERMINAL_WIDTH = "80"
UNCOLOURED = ("FORCE_COLOR", "NO_COLOR", "PY_COLORS", "CLICOLOR_FORCE")
"""Variables that switch colour on or off; unset, so output is plain whether or not the CI sets one."""

OWN_DIRECTORIES = (
    "HZ_CONFIG_HOME",
    "HZ_DATA_DIR",
    "HZ_CACHE_DIR",
    "HZ_STATE_DIR",
    "HZ_LOG_DIR",
    "HZ_SYSTEM_DAEMON__UNIT_DIR",  # `hz system doctor` asks launchd or systemd only when a unit file is here
)
"""Each test gets its own empty directory for each of these."""


@dataclass(frozen=True, slots=True)
class Result:
    """What a `hz` command printed and how it exited."""

    exit_code: int
    output: str

    @property
    def json(self) -> Any:  # noqa: ANN401 - whatever the command printed
        """The output parsed as JSON (the command must have run with `--json`, which `hz(...)` adds)."""
        return json.loads(self.output)


def pin_terminal(environ: MutableMapping[str, str]) -> None:
    """Make terminal width and colour the same everywhere: 80 columns, no colour, no matter where tests run.

    Typer reads `TERMINAL_WIDTH` and `_TYPER_FORCE_DISABLE_TERMINAL` once, when it is imported; Rich reads `COLUMNS`
    and the colour variables when it prints. So this runs before any test module is imported (`pytest_configure`) and
    again for every test.
    """
    for name in UNCOLOURED:
        environ.pop(name, None)
    environ["COLUMNS"] = TERMINAL_WIDTH
    environ["TERMINAL_WIDTH"] = TERMINAL_WIDTH
    environ["_TYPER_FORCE_DISABLE_TERMINAL"] = "1"


def pytest_configure(config: pytest.Config) -> None:  # noqa: ARG001 - the hook's signature
    """Pin the terminal before Typer is imported by any test module (see `pin_terminal`)."""
    pin_terminal(os.environ)


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
    """No test sees the developer's HZ_* variables, config or state, touches launchd or systemd or asks for updates."""
    for name in ISOLATED:
        monkeypatch.delenv(name, raising=False)
    for name in OWN_DIRECTORIES:
        monkeypatch.setenv(name, str(tmp_path_factory.mktemp(name.lower())))
    isolate_credentials(monkeypatch)
    monkeypatch.setenv("HZ_UPDATE_CHECK", "false")  # no `hz` a test starts asks GitHub for the latest release
    for name in (*UNCOLOURED, "COLUMNS", "TERMINAL_WIDTH"):
        monkeypatch.delenv(name, raising=False)  # registers the restore; a test may change them
    pin_terminal(os.environ)


def isolate_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never the real keychain (ADR 0030): credentials come from HZ_CREDENTIAL_* variables, the developer's removed."""
    monkeypatch.setenv("HZ_SYSTEM_CREDENTIAL__BACKEND", "environment")
    for name in [name for name in os.environ if name.startswith("HZ_CREDENTIAL_")]:
        monkeypatch.delenv(name)


class MemoryBackend:
    """Credentials kept in a dictionary, for tests: `hz_credentials` puts one behind `Credentials`."""

    name = "memory"

    def __init__(self) -> None:
        """Start empty."""
        self.values: dict[tuple[str, str], str] = {}

    def get(self, service: str, name: str) -> str | None:
        """The stored value, or None."""
        return self.values.get((service, name))

    def set(self, service: str, name: str, value: str) -> None:
        """Store a value."""
        self.values[service, name] = value

    def delete(self, service: str, name: str) -> bool:
        """Remove a value; whether there was one."""
        return self.values.pop((service, name), None) is not None


@pytest.fixture
def hz_kernel() -> "Kernel":
    """A kernel with the built-in modules and every installed plugin, configured only by environment variables."""
    from heinzel.kernel import Config, Kernel, Settings, discover  # noqa: PLC0415 - see the module comment

    config = Config.empty()
    records, errors = discover()
    return Kernel(config.settings(Settings, "hz"), records, errors, config)


@pytest.fixture
def hz_credentials(hz_kernel: "Kernel") -> "Credentials":
    """The `Credentials` of `hz_kernel` (and so of `hz`), kept in memory: `hz_credentials.set("api-token", "x")`."""
    from heinzel.kernel import CredentialLet, Credentials  # noqa: PLC0415 - see the module comment

    credentials = Credentials(MemoryBackend(), [(r.qualname, let) for r, let in hz_kernel.lets(CredentialLet)])
    hz_kernel.bind(Credentials, credentials)
    return credentials


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
