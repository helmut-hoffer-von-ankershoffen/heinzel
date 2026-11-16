"""Closed ownership inventory for reviewed mixed local push scopes.

This inventory selects local checks, not release acceptance. Native fixtures that
need separate harnesses remain named pending obligations, even after local success.
"""

import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

APPLE_SOURCES = frozenset(
    {
        "apple/App/HeinzelApp.swift",
        "apple/App/SystemAudio.swift",
        "apple/App/WorkspaceView.swift",
        "apple/Heinzel.xcodeproj/project.pbxproj",
        "apple/Shared/ActivityCatalog.swift",
        "apple/Shared/ActivityWidgetData.swift",
        "apple/Shared/WidgetActivity.entitlements",
        "apple/Shared/WidgetCredentialStore.swift",
        "apple/Shared/WidgetEnrollmentTransport.swift",
        "apple/Shared/WidgetFencedEnrollment.swift",
        "apple/Shared/WidgetSnapshotClient.swift",
        "apple/Shared/WidgetViewRotation.swift",
        "apple/Widgets/HeinzelWidgets.swift",
        "tests/fixtures/apple-activity-catalog.swift",
    }
)
NATIVE_FIXTURES = frozenset(
    f"apple/tests/{name}.swift"
    for name in (
        "SystemAudioStopTests",
        "WidgetCredentialStoreTests",
        "WidgetFencedProtocolHarness",
        "WidgetSnapshotTests",
        "WidgetStatusTests",
        "WidgetTimelineRefreshTests",
        "WidgetTransportTests",
        "WidgetWebKitEnrollmentTests",
        "WidgetWebViewRotationTests",
    )
)
WIDGET_CORE = frozenset(
    f"src/heinzel/heads/web/{name}.py"
    for name in ("__init__", "_devices", "_widget", "_widget_fence", "_widget_fence_routes")
)
CORE_INTEGRATION = frozenset({"tests/integration/web_widget_test.py", "tests/integration/web_widget_fence_test.py"})
APPLE_INTEGRATION = frozenset(
    {
        "tests/integration/apple_activity_catalog_test.py",
        "tests/integration/apple_widget_navigation_test.py",
        "tests/integration/apple_widget_enrollment_transport_test.py",
    }
)
HARNESS_INTEGRATION = frozenset(
    {
        "tests/integration/web_widget_apple_contract_test.py",
        "tests/integration/web_widget_webkit_contract_test.py",
    }
)
WEB_OWNERS = {
    "web/src/audio/AudioVisualizer.tsx": "web/src/audio/AudioVisualizer.test.tsx",
    "web/src/audio/AudioVisualizer.test.tsx": "web/src/audio/AudioVisualizer.test.tsx",
}
SELECTOR_SOURCES = frozenset({"scripts/scoped_checks.py", "scripts/mixed_scope.py"})
SAFE_PATH = re.compile(r"[A-Za-z0-9_./-]+")
MAX_SELECTION = 64


def files(root: Path, paths: list[str]) -> None:
    """Require exact existing files, without symlink parents or deleted owners.

    Raises:
        ValueError: A path is absent, unsafe, or not its canonical checkout path.
    """
    for name in paths:
        path = root / name
        if (
            not SAFE_PATH.fullmatch(name)
            or any(part in {".", "..", ""} for part in name.split("/"))
            or not path.is_file()
            or path.resolve() != path
        ):
            msg = "mixed scope requires existing canonical checkout files"
            raise ValueError(msg)


def selection(packet: dict, key: str) -> list[str]:
    """Read a bounded list without duplicates or caller-supplied commands.

    Raises:
        ValueError: The selection is malformed or unbounded.
    """
    value = packet[key]
    if (
        not isinstance(value, list)
        or len(value) > MAX_SELECTION
        or any(not isinstance(item, str) for item in value)
        or len(set(value)) != len(value)
    ):
        msg = "mixed scope requires bounded unique selections"
        raise ValueError(msg)
    return value


def requirements(changed: list[str]) -> tuple[list[str], set[str], set[str], set[str]]:
    """Partition the complete diff; no unrecognized path is discarded.

    Raises:
        ValueError: A path has no reviewed mixed-lane owner.
    """
    python, web, integration, pending = [], set(), set(), {"full-hosted-validation", "managed-backend-check"}
    for path in changed:
        if path in APPLE_SOURCES | NATIVE_FIXTURES:
            integration.update(APPLE_INTEGRATION)
            pending.update(NATIVE_FIXTURES | HARNESS_INTEGRATION)
        elif path in WEB_OWNERS:
            web.add(WEB_OWNERS[path])
        elif path in CORE_INTEGRATION | APPLE_INTEGRATION:
            integration.add(path)
        elif path in HARNESS_INTEGRATION:
            pending.add(path)
        elif (
            path in WIDGET_CORE
            or path in SELECTOR_SOURCES
            or re.fullmatch(r"tests/unit/[A-Za-z0-9_/-]+_test\.py", path)
        ):
            python.append(path)
            if path in WIDGET_CORE:
                integration.update(CORE_INTEGRATION)
                pending.update(HARNESS_INTEGRATION)
        elif path.endswith(".md"):
            python.append(path)
        else:
            msg = f"mixed scope has no owning recipe for {path}"
            raise ValueError(msg)
    return python, web, integration, pending


def validate(packet: dict, changed: list[str], root: Path) -> list[str]:
    """Require every mandatory local recipe and pending external obligation.

    Raises:
        ValueError: The mixed scope omits an owner or invents a recipe.
    """
    files(root, changed)
    python, web, integration, pending = requirements(changed)
    expected = {"web": web, "integration": integration, "pending": pending}
    if not python or not web:
        msg = "mixed scope requires Python and supported web changes"
        raise ValueError(msg)
    for key, required in expected.items():
        if set(selection(packet, key)) != required:
            msg = f"mixed scope must retain exactly its {key} obligations"
            raise ValueError(msg)
    if set(changed) & SELECTOR_SOURCES:
        owning = {item.split("::", 1)[0] for item in selection(packet, "owning")}
        if not {"tests/unit/scoped_checks_test.py", "tests/unit/mixed_scope_test.py"} <= owning:
            msg = "mixed scope must retain both selector test files"
            raise ValueError(msg)
    files(root, sorted(web | integration))
    return python


def ios_command(derived_data: Path) -> list[str]:
    """Compile the app and extension without signing, booting, or launching them."""
    return [
        "xcodebuild",
        "-project",
        "apple/Heinzel.xcodeproj",
        "-scheme",
        "Heinzel",
        "-configuration",
        "Debug",
        "-sdk",
        "iphonesimulator",
        "-destination",
        "generic/platform=iOS Simulator",
        "-derivedDataPath",
        str(derived_data),
        "-jobs",
        "2",
        "CODE_SIGNING_ALLOWED=NO",
        "build",
    ]


def integration_result(report: Path, expected: list[str]) -> None:
    """Reject empty or skipped required integration checks, even when pytest exits zero.

    Raises:
        ValueError: No executed cases or a skipped case prevents local proof.
    """
    root = ET.parse(report).getroot()  # noqa: S314 - report produced by this invocation of pytest
    cases = list(root.iter("testcase"))
    names = {case.get("classname", "") for case in cases}
    modules = {path.removesuffix(".py").replace("/", ".") for path in expected}
    missing = any(not any(name == module or name.startswith(module + ".") for name in names) for module in modules)
    if (
        missing
        or not cases
        or any(case.find(tag) is not None for case in cases for tag in ("skipped", "failure", "error"))
    ):
        msg = "required mixed integration checks were empty, skipped or unsuccessful"
        raise ValueError(msg)


def run_recipe(arguments: list[str]) -> None:
    """Run a fixed recipe in disposable storage; never launch an app or signed harness.

    Raises:
        ValueError: The requested recipe or its test operands are not supported.
    """
    if arguments != ["ios"] and (
        not arguments[1:]
        or arguments[0] != "integration"
        or len(arguments[1:]) != len(set(arguments[1:]))
        or not set(arguments[1:]) <= CORE_INTEGRATION | APPLE_INTEGRATION
    ):
        msg = "unsupported mixed local recipe"
        raise ValueError(msg)
    with tempfile.TemporaryDirectory(prefix="heinzel-mixed-check-") as scratch:
        directory = Path(scratch)
        if arguments == ["ios"]:
            subprocess.run(ios_command(directory / "ios"), check=True, timeout=900)  # noqa: S603
        else:
            report = directory / "integration.xml"
            subprocess.run(  # noqa: S603 - fixed recipe and closed test inventory
                [  # noqa: S607 - uv from the configured toolchain
                    "uv",
                    "run",
                    "pytest",
                    *arguments[1:],
                    "-n",
                    "2",
                    "-x",
                    "-q",
                    "--session-timeout=180",
                    f"--junitxml={report}",
                ],
                check=True,
                timeout=210,
            )
            integration_result(report, arguments[1:])


if __name__ == "__main__":
    run_recipe(sys.argv[1:])
