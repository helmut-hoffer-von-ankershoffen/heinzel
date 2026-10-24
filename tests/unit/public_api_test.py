"""The public API plugins build on changes only on purpose: adding, renaming or removing a name updates this snapshot.

Plugins import from `heinzel` and `heinzel.kernel` (ADR 0026, the public API). A change here is a change for every
plugin: record it in this file, in the same commit, and mark a removal or rename as breaking (`!`).
"""

import pytest

import heinzel
import heinzel.kernel

pytestmark = pytest.mark.unit

HEINZEL = [
    "AgentResult",
    "AgentRunner",
    "Budget",
    "Calendar",
    "CatchUp",
    "Channel",
    "Chat",
    "Check",
    "CodeHost",
    "CompiledJobs",
    "Config",
    "CredentialLet",
    "Credentials",
    "Document",
    "Documents",
    "Event",
    "HealthLet",
    "HeinzelError",
    "InfoLet",
    "Issue",
    "JobLet",
    "Kernel",
    "Let",
    "Meeting",
    "Meetings",
    "Message",
    "Module",
    "ModuleRecord",
    "ModuleSettings",
    "OperationLet",
    "Participant",
    "PostedMessage",
    "Progress",
    "ProviderLet",
    "PullRequest",
    "RequirementLet",
    "SectionReplacement",
    "ServiceLet",
    "Settings",
    "SettingsLet",
    "Status",
    "TokenUsage",
    "ToolProfile",
    "Tracker",
    "Transcript",
    "TranscriptSegment",
    "Window",
    "__version__",
    "clean_text",
]
KERNEL = [
    "AgentResult",
    "AgentRunner",
    "BUILTIN_PACKAGE",
    "Budget",
    "CAPABILITIES",
    "CORE_DISTRIBUTION",
    "Calendar",
    "Capability",
    "CapabilityView",
    "CatchUp",
    "Channel",
    "Chat",
    "Check",
    "CodeHost",
    "CompiledJobs",
    "Config",
    "ConfigFile",
    "CredentialBackend",
    "CredentialLet",
    "Credentials",
    "DeclaredCredential",
    "Document",
    "Documents",
    "ENTRY_POINT_GROUP",
    "EnvironmentBackend",
    "Event",
    "FILENAME",
    "HealthLet",
    "HeinzelError",
    "InfoLet",
    "Installation",
    "Issue",
    "JobLet",
    "KERNEL_SECTION",
    "Kernel",
    "Let",
    "MODES",
    "Meeting",
    "Meetings",
    "Message",
    "Mode",
    "Module",
    "ModuleRecord",
    "ModuleSettings",
    "OperationLet",
    "Origin",
    "PROJECT_FILENAME",
    "Participant",
    "PostedMessage",
    "Progress",
    "ProviderLet",
    "PullRequest",
    "ReleaseCache",
    "Requester",
    "Requirement",
    "RequirementLet",
    "RunStatus",
    "SectionReplacement",
    "ServiceLet",
    "Settings",
    "SettingsLet",
    "Status",
    "TokenUsage",
    "ToolProfile",
    "Tracker",
    "Transcript",
    "TranscriptSegment",
    "Window",
    "boot",
    "clean_text",
    "discover",
    "env_prefix",
    "environment_variable",
    "parse_duration",
    "requirement_from_receipt",
    "schedule_problem",
    "subprocess_environment",
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
