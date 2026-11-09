"""The pytest side of the quarantine lane (`scripts/quarantine.py`, `tests/quarantine.toml`).

`HZ_QUARANTINE` picks what a run does with a test whose entry is in date:
  (unset)  deselect it, and say so in the summary: the required jobs (default)
  only     keep only such tests: the `Quarantine (<os>)` jobs
  include  keep everything, only mark it
A test named by node id on the command line always runs (reproducing a flake, `hz-ci-one dispatch --test`).
An entry past its `until` quarantines nothing, so its test is back in the required jobs.
`HZ_QUARANTINE_REPORT` (only mode): append one JSON line per test phase result there.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    import datetime as dt
    from collections.abc import Sequence
    from types import ModuleType

MODES = frozenset({"", "only", "include"})
MARKER = (
    "quarantine: The test's tests/quarantine.toml entry is in date; "
    "it runs in the Quarantine lane, not the required jobs."
)


def registry_module(root: Path) -> ModuleType | None:
    """scripts/quarantine.py of this checkout; None for a copied test tree that has no scripts (e2e scheduling)."""
    script = root / "scripts" / "quarantine.py"
    if not script.is_file():
        return None
    name = "heinzel_quarantine_registry"
    loaded = sys.modules.get(name)
    if loaded is not None and Path(loaded.__file__ or "") == script:
        return loaded
    spec = importlib.util.spec_from_file_location(name, script)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses look their module up while the file runs
    spec.loader.exec_module(module)
    return module


def _requested(config: pytest.Config) -> tuple[str, ...]:
    """Node ids named on the command line, relative to the root like item node ids."""
    found = []
    for argument in config.args:
        if "::" not in argument:
            continue
        path, _, rest = argument.partition("::")
        location = Path(path)
        if not location.is_absolute():
            location = config.invocation_params.dir / location
        try:
            relative = location.resolve().relative_to(config.rootpath.resolve()).as_posix()
        except ValueError:
            continue
        found.append(f"{relative}::{rest}")
    return tuple(found)


class Quarantine:
    def __init__(
        self, entries: Sequence[Any], on: dt.date, mode: str, requested: Sequence[str] = (), report: Path | None = None
    ) -> None:
        if mode not in MODES:
            message = f"HZ_QUARANTINE must be unset, 'only' or 'include', not {mode!r}"
            raise pytest.UsageError(message)
        self._entries, self._on, self._mode = tuple(entries), on, mode
        self._requested, self._report = tuple(requested), report
        self.deselected: list[str] = []

    def _entry(self, nodeid: str) -> Any:
        return next((entry for entry in self._entries if entry.active(self._on) and entry.matches(nodeid)), None)

    def _named(self, nodeid: str) -> bool:
        return any(nodeid == r or nodeid.startswith((r + "[", r + "::")) for r in self._requested)

    def pytest_report_header(self) -> str:
        active = sum(entry.active(self._on) for entry in self._entries)
        return f"quarantine: {active} entr{'y' if active == 1 else 'ies'} in date, mode {self._mode or 'required'}"

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection_modifyitems(self, config: pytest.Config, items: list[pytest.Item]) -> None:
        keep: list[pytest.Item] = []
        drop: list[pytest.Item] = []
        for item in items:
            entry = self._entry(item.nodeid)
            if entry is not None:
                item.add_marker(pytest.mark.quarantine(bead=entry.bead, until=entry.until.isoformat()))
            if self._mode == "only":
                selected = entry is not None
            else:
                selected = entry is None or self._mode == "include" or self._named(item.nodeid)
            (keep if selected else drop).append(item)
        if drop:
            if self._mode != "only":
                self.deselected = [item.nodeid for item in drop]
            config.hook.pytest_deselected(items=drop)
            items[:] = keep

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if self._report is None or self._mode != "only":
            return
        if report.when != "call" and report.outcome == "passed":
            return
        row = {"nodeid": report.nodeid, "when": report.when, "outcome": report.outcome}
        with self._report.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")

    def pytest_terminal_summary(self, terminalreporter: Any, config: pytest.Config) -> None:
        active = [
            entry
            for entry in self._entries
            if entry.active(self._on) and not any(entry.matches(nodeid) for nodeid in self._requested)
        ]
        if not self.deselected and self._mode == "" and active and getattr(config.option, "numprocesses", None):
            # Under xdist the workers collect and deselect; the controller names what they leave out.
            terminalreporter.write_line(
                f"quarantine: workers leave out {len(active)} entr(ies) of tests/quarantine.toml"
            )
            for entry in active:
                terminalreporter.write_line(f"  {entry.test} (bead {entry.bead}, until {entry.until})")
        if self.deselected:
            terminalreporter.write_line(
                f"quarantine: {len(self.deselected)} test(s) left out "
                "(tests/quarantine.toml; the Quarantine lane runs them)"
            )
            for nodeid in self.deselected:
                terminalreporter.write_line(f"  {nodeid}")


def configure(config: pytest.Config, root: Path, on: dt.date | None = None) -> None:
    """Register the marker and, when this checkout has the registry script, the plugin; a bad registry fails closed."""
    config.addinivalue_line("markers", MARKER)
    registry = registry_module(root)
    if registry is None:
        return
    try:
        entries = registry.load(root / "tests" / "quarantine.toml")
    except registry.RegistryError as error:
        message = f"tests/quarantine.toml breaks the quarantine rules:\n{error}"
        raise pytest.UsageError(message) from None
    report = os.environ.get("HZ_QUARANTINE_REPORT")
    plugin = Quarantine(
        entries,
        on or registry.today(),
        os.environ.get("HZ_QUARANTINE", ""),
        _requested(config),
        Path(report) if report else None,
    )
    config.pluginmanager.register(plugin, "heinzel-quarantine")
