# /// script
# requires-python = ">=3.15"
# dependencies = []
# ///
"""Closed ownership inventory for reviewed mixed local push scopes.

This inventory selects local checks, not release acceptance. Native fixtures that
need separate harnesses remain named pending obligations, even after local success.
Mixed scopes select whole test files, never node IDs. A unit file needs at least
one passing body; platform skips are counted and remain unverified, not passed.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
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
AUDIO_VISUALIZER_TEST = "web/src/audio/AudioVisualizer.test.tsx"
WEB_OWNERS = {
    "web/src/audio/AudioVisualizer.tsx": AUDIO_VISUALIZER_TEST,
    AUDIO_VISUALIZER_TEST: AUDIO_VISUALIZER_TEST,
}
SELECTOR_SOURCES = frozenset({"scripts/scoped_checks.py", "scripts/mixed_scope.py"})
SAFE_PATH = re.compile(r"[A-Za-z0-9_./-]+")
MAX_SELECTION = 64
PARTITION_TEST = "tests/integration/e2e_lane_partition_test.py"
LOCAL_PARTITION_TESTS = (
    f"{PARTITION_TEST}::test_partition_rejects_unmodeled_or_duplicate_collection",
    f"{PARTITION_TEST}::test_partition_matches_pytest_deselection_prefix_semantics",
)
PARTITION_PARAMETER_CASES = 5
LOCAL_PARTITION_CASES = (
    *(
        f"test_partition_rejects_unmodeled_or_duplicate_collection[arguments{index}]"
        for index in range(PARTITION_PARAMETER_CASES)
    ),
    "test_partition_matches_pytest_deselection_prefix_semantics",
)
HOSTED_PARTITION_TEST = (
    f"{PARTITION_TEST}::test_ci_lanes_preserve_every_original_node_once_including_supervised_parameterizations"
)
HOSTED_E2E = "full-hosted-e2e-and-integration"


def local_check_environment() -> dict[str, str]:
    """Keep parent scope/ref bindings but never redirect child Git into the invoking index.

    Tests own their disposable repositories and configuration. Inherited Git
    directory, index, object-store and injected-config variables must not escape
    the hook process into those tests or their subprocesses.
    """
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


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
    python, web, integration, pending = (
        [],
        set(),
        set(),
        {
            "full-hosted-validation",
            "managed-backend-check",
            HOSTED_E2E,
            HOSTED_PARTITION_TEST,
        },
    )
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
    if any("::" in item for key in ("owning", "hosted_only") for item in selection(packet, key)):
        msg = "mixed scope accepts whole test files only, not node selectors"
        raise ValueError(msg)
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


def pytest_result(report: Path, expected: list[str], *, allow_partial_skips: bool = False) -> None:
    """Require passing bodies in every selected whole file, even when pytest exits zero.

    Unit-file proof can retain platform skips, but never verifies those skipped
    cases. Integration-file proof requires every reported body to have run.

    Raises:
        ValueError: No executed cases or a skipped case prevents local proof.
    """
    if any("::" in path for path in expected):
        msg = "mixed pytest evidence supports whole files only, not node selectors"
        raise ValueError(msg)
    root = ET.parse(report).getroot()  # noqa: S314 - report produced by this invocation of pytest
    cases = list(root.iter("testcase"))
    names = {case.get("classname", "") for case in cases if case.find("skipped") is None}
    modules = {path.removesuffix(".py").replace("/", ".") for path in expected}
    missing = any(not any(name == module or name.startswith(module + ".") for name in names) for module in modules)
    if (
        missing
        or not cases
        or any(case.find(tag) is not None for case in cases for tag in ("failure", "error"))
        or (not allow_partial_skips and any(case.find("skipped") is not None for case in cases))
    ):
        msg = "required mixed pytest checks were empty, skipped or unsuccessful"
        raise ValueError(msg)
    skipped = sum(case.find("skipped") is not None for case in cases)
    if skipped:
        print(f"File-level unit proof: {skipped} skipped cases remain unverified.", flush=True)


def vitest_result(report: Path, expected: list[str], root: Path) -> None:
    """Require a real passing test body from each selected Vitest file.

    Raises:
        ValueError: The report is empty, mismatched, failed, or contains only skipped bodies for a file.
        TypeError: A file result does not have the expected report structure.
    """
    data = json.loads(report.read_text(encoding="utf-8"))
    results = data.get("testResults") if isinstance(data, dict) else None
    if not isinstance(data, dict) or data.get("success") is not True or not isinstance(results, list) or not results:
        msg = "required mixed Vitest results were empty or unsuccessful"
        raise ValueError(msg)
    actual = []
    for result in results:
        if not isinstance(result, dict) or not isinstance(result.get("assertionResults"), list):
            msg = "invalid mixed Vitest file result"
            raise TypeError(msg)
        assertions = result.get("assertionResults", [])
        if any(not isinstance(item, dict) or not isinstance(item.get("status"), str) for item in assertions):
            msg = "invalid mixed Vitest assertion result"
            raise ValueError(msg)
        statuses = [item.get("status") for item in assertions]
        name = result.get("name")
        if (
            not isinstance(name, str)
            or "passed" not in statuses
            or not set(statuses) <= {"passed", "pending", "todo", "skipped"}
        ):
            msg = "required mixed Vitest file has no passing bodies or contains a failure"
            raise ValueError(msg)
        actual.append(Path(name).resolve())
    if len(actual) != len(set(actual)) or set(actual) != {root / path for path in expected}:
        msg = "required mixed Vitest file inventory does not match the selection"
        raise ValueError(msg)


def check_fast(arguments: list[str], directory: Path) -> None:
    """Keep the real Make guards and require fresh unit and Vitest result evidence.

    Raises:
        ValueError: The selected operands are not safe bounded unit/web test files.
    """
    from scoped_checks import HOSTED_ONLY_TESTS, LOCAL_SELECTOR, ROOT, python_overrides, web_test_step  # noqa: PLC0415

    if not arguments or arguments[0] != "--unit" or arguments.count("--web") != 1:
        msg = "check-fast requires unit and web selections"
        raise ValueError(msg)
    split = arguments.index("--web")
    unit, web = arguments[1:split], arguments[split + 1 :]
    if (
        not unit
        or not web
        or len(unit) > MAX_SELECTION
        or len(web) > MAX_SELECTION
        or len(set(unit)) != len(unit)
        or len(set(web)) != len(web)
        or any("::" in item or not LOCAL_SELECTOR.fullmatch(item) or item in HOSTED_ONLY_TESTS for item in unit)
    ):
        msg = "check-fast requires bounded permitted unit and web selections"
        raise ValueError(msg)
    files(ROOT, sorted(set(unit)))
    unit_report, web_report = directory / "unit.xml", directory / "web.json"
    unit_operand, web_operand = report_operand(unit_report), report_operand(web_report)
    overrides = python_overrides([], unit)
    overrides[0] = (
        f'WEB_TESTS="{web_test_step(" ".join(web))} --reporter=default --reporter=json --outputFile.json={web_operand}"'
    )
    overrides[-1] += f" --junitxml={unit_operand}"
    subprocess.run(  # noqa: S603 - fixed Make target and validated selectors
        ["make", "check-fast", *overrides],  # noqa: S607 - Make from the configured toolchain
        env=local_check_environment(),
        check=True,
        timeout=1800,
    )
    pytest_result(unit_report, unit, allow_partial_skips=True)
    vitest_result(web_report, web, ROOT)


def report_operand(path: Path) -> str:
    """Permit only inert absolute paths across Make expansion and shell double quotes.

    Raises:
        ValueError: A temporary report path can change Make or shell syntax.
    """
    value = str(path)
    if not path.is_absolute() or not re.fullmatch(r"/[A-Za-z0-9_./-]+", value) or ".." in path.parts:
        msg = "mixed reports require absolute paths safe for both Make and shell expansion"
        raise ValueError(msg)
    return value


def partition_result(report: Path) -> None:
    """Require every fixed partition body exactly once, including all parameterizations.

    Raises:
        ValueError: A required body is missing, duplicated, skipped, failed or errored.
    """
    root = ET.parse(report).getroot()  # noqa: S314 - generated by this invocation of pytest
    cases = list(root.iter("testcase"))
    module = PARTITION_TEST.removesuffix(".py").replace("/", ".")
    expected = Counter((module, name) for name in LOCAL_PARTITION_CASES)
    actual = Counter((case.get("classname"), case.get("name")) for case in cases)
    if actual != expected or any(
        case.find(tag) is not None for case in cases for tag in ("skipped", "failure", "error")
    ):
        msg = "bounded partition proof requires every expected body exactly once without skips or failures"
        raise ValueError(msg)


def run_partition() -> None:
    """Run only the two fixed assertions and verify their complete six-case result."""
    with tempfile.TemporaryDirectory(prefix="heinzel-partition-check-") as scratch:
        report = Path(scratch) / "partition.xml"
        # Fixed node inventory, no caller-supplied selection.
        subprocess.run(  # noqa: S603
            [  # noqa: S607 - uv from the configured toolchain
                "uv",
                "run",
                "pytest",
                *LOCAL_PARTITION_TESTS,
                "-m",
                "integration",
                "-n",
                "2",
                "-x",
                "-q",
                "--session-timeout=120",
                f"--junitxml={report}",
            ],
            env=local_check_environment(),
            check=True,
            timeout=180,
        )
        partition_result(report)


def run_recipe(arguments: list[str]) -> None:
    """Run a fixed recipe in disposable storage; never launch an app or signed harness.

    Raises:
        ValueError: The requested recipe or its test operands are not supported.
    """
    if arguments == ["partition"]:
        run_partition()
        return
    if arguments[:1] == ["check-fast"]:
        with tempfile.TemporaryDirectory(prefix="heinzel-mixed-check-") as scratch:
            check_fast(arguments[1:], Path(scratch))
        return
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
            subprocess.run(  # noqa: S603 - fixed unsigned build recipe
                ios_command(directory / "ios"),
                env=local_check_environment(),
                check=True,
                timeout=900,
            )
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
                env=local_check_environment(),
                check=True,
                timeout=210,
            )
            pytest_result(report, arguments[1:])


if __name__ == "__main__":
    run_recipe(sys.argv[1:])
