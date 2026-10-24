"""The public API plugins build on changes only on purpose: adding, renaming or removing a name updates this snapshot.

Plugins import from `heinzel` and `heinzel.kernel` (ADR 0026, the public API). A change here is a change for every
plugin: record it in this file, in the same commit, and mark a removal or rename as breaking (`!`).
"""

import pytest

import heinzel
import heinzel.kernel

pytestmark = pytest.mark.unit

HEINZEL = [
    "Check",
    "Config",
    "HealthLet",
    "HeinzelError",
    "InfoLet",
    "Kernel",
    "Let",
    "Module",
    "ModuleRecord",
    "ModuleSettings",
    "OperationLet",
    "Progress",
    "ServiceLet",
    "Settings",
    "SettingsLet",
    "Status",
    "__version__",
]
KERNEL = [
    "BUILTIN_PACKAGE",
    "CORE_DISTRIBUTION",
    "Check",
    "Config",
    "ConfigFile",
    "ENTRY_POINT_GROUP",
    "FILENAME",
    "HealthLet",
    "HeinzelError",
    "InfoLet",
    "Installation",
    "KERNEL_SECTION",
    "Kernel",
    "Let",
    "Module",
    "ModuleRecord",
    "ModuleSettings",
    "OperationLet",
    "Origin",
    "PROJECT_FILENAME",
    "Progress",
    "ReleaseCheck",
    "ServiceLet",
    "Settings",
    "SettingsLet",
    "Status",
    "boot",
    "check_for_update",
    "discover",
    "env_prefix",
    "github_get_json",
    "requirement_from_receipt",
    "update_hint",
    "user_config_dir",
    "walk",
]
HINT = "the public API changed: update the snapshot in tests/unit/public_api_test.py on purpose (see ADR 0026)"


def api_change(exported: list[str], snapshot: list[str]) -> dict[str, list[str]]:
    """Names added to and removed from a module's `__all__`, compared with the snapshot."""
    return {
        "added": sorted(set(exported) - set(snapshot)),
        "removed": sorted(set(snapshot) - set(exported)),
    }


def test_finds_api_changes():
    assert api_change(["A", "B"], ["B", "C"]) == {"added": ["A"], "removed": ["C"]}


@pytest.mark.parametrize(
    ("module", "snapshot"), [(heinzel, HEINZEL), (heinzel.kernel, KERNEL)], ids=["heinzel", "kernel"]
)
def test_public_api_matches_the_snapshot(module, snapshot):
    assert api_change(module.__all__, snapshot) == {"added": [], "removed": []}, HINT
    assert snapshot == sorted(snapshot), "keep the snapshot sorted"
    assert all(hasattr(module, name) for name in module.__all__), "__all__ names something the module lacks"
