"""The update and lifecycle tests that need the real system are marked and listed.

These tests run real processes, `uv` or the service manager: they are the upgrade proof (they caught #919), so they
stay, and they run in the upgrade and macOS lanes. What may not happen is a new one: a new test on this path uses the
fakes of `heinzel.testing` instead. When a file stops needing the real system, remove its entry.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PATH = (
    "integration/*update*_test.py",
    "integration/*ui_*_test.py",
    "integration/*lifecycle*_test.py",
    "integration/*daemon*_test.py",
    "e2e/*update*_test.py",
    "e2e/*ui*_test.py",
    "e2e/*daemon*_test.py",
)
"""The update and lifecycle path, by test file name."""
REAL = re.compile(r"subprocess\.(?:run|Popen)|launchctl|real_uv|uv tool")
MARK = re.compile(r"pytest\.mark\.real_system\b")
REAL_SYSTEM_FILES = {
    "tests/e2e/auto_update_test.py",
    "tests/e2e/daemon_browser_proof_test.py",
    "tests/e2e/daemon_install_ui_test.py",
    "tests/e2e/daemon_legacy_recovery_test.py",
    "tests/e2e/isolated_build_cache_test.py",
    "tests/e2e/ui_service_browser_test.py",
    "tests/e2e/ui_service_test.py",
    "tests/e2e/web_ui_test.py",
    "tests/integration/apple_prompt_lifecycle_test.py",
    "tests/integration/auto_update_real_uv_test.py",
    "tests/integration/lifecycle_commands_test.py",
    "tests/integration/lifecycle_prepare_test.py",
    "tests/integration/lifecycle_uninstall_test.py",
    "tests/integration/supervised_update_recovery_test.py",
    "tests/integration/ui_service_test.py",
    "tests/integration/ui_stop_test.py",
    "tests/integration/update_heads_real_uv_test.py",
    "tests/integration/update_test.py",
    "tests/integration/web_update_test.py",
}

pytestmark = pytest.mark.unit


def on_the_path(root: Path = ROOT) -> list[Path]:
    """The test files of the update and lifecycle path."""
    return sorted({path for pattern in PATH for path in (root / "tests").glob(pattern)})


def real(path: Path) -> bool:
    return bool(REAL.search(path.read_text(encoding="utf-8")))


def marked(path: Path) -> bool:
    return bool(MARK.search(path.read_text(encoding="utf-8")))


def test_a_test_on_the_update_path_that_needs_the_real_system_is_marked() -> None:
    unmarked = [path.relative_to(ROOT).as_posix() for path in on_the_path() if real(path) and not marked(path)]
    assert unmarked == [], "mark it real_system, or better: use the fakes of heinzel.testing"


def test_the_real_system_tests_only_get_fewer() -> None:
    found = {path.relative_to(ROOT).as_posix() for path in (ROOT / "tests").rglob("*_test.py") if marked(path)}
    assert not (added := sorted(found - REAL_SYSTEM_FILES)), f"use fakes for new real-system tests: {added}"
    assert not (retired := sorted(REAL_SYSTEM_FILES - found)), f"remove retired real-system exceptions: {retired}"
