"""Run the native footer geometry guard on macOS; simulator acceptance remains separate."""

import subprocess
import sys
from pathlib import Path

import pytest

from heinzel.testing import scaled

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(sys.platform != "darwin", reason="Swift/AppKit fixture requires macOS")
def test_native_footer_rejects_untrusted_or_stale_geometry(tmp_path: Path) -> None:
    binary = tmp_path / "footer-geometry"
    subprocess.run(
        [
            "xcrun",
            "swiftc",
            "-parse-as-library",
            "-swift-version",
            "6",
            "apple/App/BrowserNetwork.swift",
            "apple/App/Endpoint.swift",
            "apple/App/WorkspaceDirectory.swift",
            "apple/tests/WorkspaceFooterGeometryTests.swift",
            "-o",
            str(binary),
        ],
        cwd=ROOT,
        check=True,
        timeout=scaled(120),
    )
    subprocess.run(["codesign", "--force", "--sign", "-", str(binary)], check=True, timeout=scaled(20))
    subprocess.run(["codesign", "--verify", "--strict", str(binary)], check=True, timeout=scaled(20))
    result = subprocess.run([str(binary)], capture_output=True, text=True, check=True, timeout=scaled(20))
    assert "PASS: native footer slot is bounded" in result.stdout
    assert "PASS: version refresh preserves offline cache and rejects superseded workspaces" in result.stdout
    assert "PASS: iCloud sign-in sync defaults on and keeps an explicit off" in result.stdout
    assert "PASS: iCloud sign-in uses a renewable device token" in result.stdout


@pytest.fixture(scope="module")
def workspace_row_probe(tmp_path_factory: pytest.TempPathFactory) -> Path:
    binary = tmp_path_factory.mktemp("workspace-row") / "probe"
    subprocess.run(
        [
            "xcrun",
            "swiftc",
            "-parse-as-library",
            "-swift-version",
            "6",
            "-whole-module-optimization",
            "apple/App/WorkspaceDirectoryRow.swift",
            "apple/tests/WorkspaceDirectoryRowTests.swift",
            "-o",
            str(binary),
        ],
        cwd=ROOT,
        check=True,
        timeout=scaled(55),
    )
    return binary


@pytest.mark.skipif(sys.platform != "darwin", reason="Native row rendering requires SwiftUI and AppKit")
@pytest.mark.timeout(func_only=True)  # The compiler fixture has its own cold-SDK deadline.
def test_workspace_versions_follow_the_name_and_unused_actions_release_space(workspace_row_probe: Path) -> None:
    result = subprocess.run([str(workspace_row_probe)], capture_output=True, text=True, check=False, timeout=scaled(15))
    assert result.returncode == 0, result.stderr
    for width in (320, 390, 420):
        assert f"PASS: width {width} " in result.stdout
    assert "PASS: rows without an action use the available width" in result.stdout


@pytest.fixture(scope="module")
def workspace_information_probe(tmp_path_factory: pytest.TempPathFactory) -> Path:
    binary = tmp_path_factory.mktemp("workspace-information") / "probe"
    subprocess.run(
        [
            "xcrun",
            "swiftc",
            "-parse-as-library",
            "-swift-version",
            "6",
            "-whole-module-optimization",
            "apple/App/WorkspaceInformationSurface.swift",
            "apple/tests/WorkspaceInformationSurfaceTests.swift",
            "-o",
            str(binary),
        ],
        cwd=ROOT,
        check=True,
        timeout=scaled(55),
    )
    return binary


@pytest.mark.skipif(sys.platform != "darwin", reason="Native information surface requires SwiftUI and AppKit")
@pytest.mark.timeout(func_only=True)
def test_information_and_back_preserve_the_same_surface(workspace_information_probe: Path) -> None:
    result = subprocess.run(
        [str(workspace_information_probe)], capture_output=True, text=True, check=True, timeout=scaled(15)
    )
    assert "PASS: Mac footer stays at bottom center" in result.stdout
    assert "PASS: information and Back keep the original directory and geometry for both surface sizes" in result.stdout
